from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from functools import wraps
from datetime import date
import io
import os
import shutil
import sys
import pandas as pd
import data_loader
import data_loader_inventario
import data_loader_obligatorios
import data_loader_segunda_linea
import data_loader_exclusion_compra
import data_loader_nivel_servicio
import data_loader_adquisiciones

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "casamusa_dashboard_2026_secreto")

# ══════════════════════════════════════════════════════
#  MIGRACION A POSTGRES (Fase 1, dominio Comercial)
#  Flag para poder volver a la version Excel sin deploy (ver plan de
#  migracion, seccion "Verificacion") -- default ON porque en Vercel
#  no existen los .xlsx locales, asi que la version Excel no
#  funcionaria ahi de todas formas. En desarrollo local, poner
#  USAR_POSTGRES_COMERCIAL=0 en .env para comparar contra la version
#  Excel original.
# ══════════════════════════════════════════════════════
USAR_POSTGRES_COMERCIAL = os.environ.get("USAR_POSTGRES_COMERCIAL", "1") == "1"
if USAR_POSTGRES_COMERCIAL:
    import data_loader_pg

# Mismo patron para el dominio Inventario (ver migrations/004_inventario.sql)
USAR_POSTGRES_INVENTARIO = os.environ.get("USAR_POSTGRES_INVENTARIO", "1") == "1"
if USAR_POSTGRES_INVENTARIO:
    import data_loader_inventario_pg
    import data_loader_obligatorios_pg

# Mismo patron para el dominio Adquisiciones (ver migrations/009_adquisiciones.sql)
USAR_POSTGRES_ADQUISICIONES = os.environ.get("USAR_POSTGRES_ADQUISICIONES", "1") == "1"
if USAR_POSTGRES_ADQUISICIONES:
    import data_loader_adquisiciones_pg

# ══════════════════════════════════════════════════════
#  GERENTES AUTORIZADOS
#  Para agregar un gerente: agregar una línea aquí
#  "email": {"password": "clave", "nombre": "Nombre", "admin": True/False,
#            "sucursal": "MT"}
#  "admin" controla quien ve/puede usar "Actualizar datos". Por
#  defecto (sin la clave, o en False) NO tiene el boton.
#  "sucursal" (opcional) hace de este usuario un "Jefe de Sucursal": ve
#  todo el dashboard, pero acotado SOLO a esa sucursal (SUCURSAL_LOGICA) —
#  el filtro se fuerza en el servidor en cada endpoint, no solo en la
#  interfaz, para que no se pueda ver otra sucursal cambiando el filtro.
#  "sucursal" tambien puede ser una LISTA (ej. ["CH","MP"]) para perfiles
#  que combinan varias sucursales bajo un mismo nombre (ej. "Express").
# ══════════════════════════════════════════════════════
# Canales de venta (tipo_venta) que conforman "E-commerce" para el
# perfil de Elennys Perez -- definido a mano con el usuario, no se
# deduce de ningun patron (ver tipo_venta real en ventas: NORMAL,
# MERCADO LIBRE CM/SC, CASAMUSA.CL, VENTA ASISTIDA, SODIMAC/LEGRAND,
# SPAZIO BTICINO, MKT PLACE). Deja fuera NORMAL y SPAZIO BTICINO.
CANALES_ECOMMERCE = ["CASAMUSA.CL", "MERCADO LIBRE CM", "MERCADO LIBRE SC", "MKT PLACE", "VENTA ASISTIDA", "SODIMAC/LEGRAND"]

GERENTES = {
    "dsepulveda@casamusa.cl": {"password": "Admin2026",         "nombre": "Administrador", "admin": True},
    "emusa@casamusa.cl":      {"password": "GGeneral2026",      "nombre": "G. General", "admin": True},
    "fmusa@casamusa.cl":      {"password": "Importaciones2026", "nombre": "Importaciones"},
    "malvarado@casamusa.cl":  {"password": "Finanzas2026",      "nombre": "Finanzas"},
    "jsantana@casamusa.cl":   {"password": "Comercial2026",     "nombre": "Comercial", "admin": True},
    "gcarrasco@casamusa.cl":  {"password": "MT2026",            "nombre": "MT", "sucursal": "MT"},
    "sarjona@casamusa.cl":    {"password": "LC2026",            "nombre": "LC", "sucursal": "LC"},
    "evalera@casamusa.cl":    {"password": "MR2026",            "nombre": "MR", "sucursal": "MR"},
    "jvillegas@casamusa.cl":  {"password": "Express2026",       "nombre": "Express", "sucursal": ["CH", "MP"]},
    # sucursal_ne (distinto de "sucursal"): Elennys es vendedora propia
    # bajo "CANAL DIGITAL" en vendedor_home, asi que su fila de NE x
    # Facturar vive ahi -- pero NO se le puede poner "sucursal" a secas
    # (eso restringiria TODOS sus reportes de Comercial a solo esa
    # sucursal, rompiendo el filtro de canal que la deja ver e-commerce
    # de toda la empresa). sucursal_ne solo se usa para /cargar_ne.
    "eperez@casamusa.cl":     {"password": "Ecommerce2026",     "nombre": "E-commerce", "canal": CANALES_ECOMMERCE, "sucursal_ne": "CANAL DIGITAL"},
}

# ══════════════════════════════════════════════════════
#  DECORADOR: exige login para ver páginas
# ══════════════════════════════════════════════════════
def login_requerido(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if "usuario" not in session:
            return redirect(url_for("login"))
        if session["usuario"] not in GERENTES:
            # Cuenta eliminada de GERENTES (ej. alguien que se fue de la
            # empresa) -- cierra cualquier sesion ya abierta de inmediato,
            # en vez de esperar a que la cookie expire sola.
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorado


# ══════════════════════════════════════════════════════
#  DECORADOR: exige que el usuario sea admin (ej. Actualizar datos)
# ══════════════════════════════════════════════════════
def admin_requerido(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if "usuario" not in session:
            return redirect(url_for("login"))
        if session["usuario"] not in GERENTES:
            session.clear()
            return redirect(url_for("login"))
        if not session.get("admin"):
            return jsonify({"ok": False, "msg": "No tienes permiso para esta accion."}), 403
        return f(*args, **kwargs)
    return decorado


# ══════════════════════════════════════════════════════
#  DECORADOR: admin, o Jefe de Sucursal (ej. NE x Facturar -- cada uno
#  carga solo lo suyo, el filtrado real por sucursal lo hace cada ruta)
# ══════════════════════════════════════════════════════
def admin_o_jefe_sucursal_requerido(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if "usuario" not in session:
            return redirect(url_for("login"))
        if session["usuario"] not in GERENTES:
            session.clear()
            return redirect(url_for("login"))
        if not session.get("admin") and not session.get("sucursal") and not session.get("sucursal_ne"):
            return jsonify({"ok": False, "msg": "No tienes permiso para esta accion."}), 403
        return f(*args, **kwargs)
    return decorado


# ══════════════════════════════════════════════════════
#  ACCESO RESTRINGIDO: Inventario y Adquisiciones -- por ahora solo
#  el usuario dueño del proyecto puede verlas (pedido explicito).
#  Para agregar a alguien mas, sumarlo a este set -- no hace falta
#  tocar ninguna ruta individual.
# ══════════════════════════════════════════════════════
USUARIOS_INVENTARIO_ADQUISICIONES = {"dsepulveda@casamusa.cl"}

PREFIJOS_RESTRINGIDOS_INV_ADQ = (
    "/inventario", "/api/inventario",
    "/adquisiciones", "/api/adquisiciones",
    "/subir_inventario", "/api/subir_inventario",
    "/subir_datos_duros", "/api/subir_datos_duros",
    "/subir_compras", "/api/subir_compras", "/api/subir_recepciones",
    "/gestionar_productos_compra", "/api/gestionar_productos_compra",
    "/gestionar_prioridad", "/api/gestionar_prioridad",
    "/admin/actualizar_inventario",
    # Plan de Compra y Nivel de Servicio viven en Forecast pero siguen
    # siendo solo para dsepulveda (pedido explicito) -- Forecast en si
    # es de gerencia (ver PREFIJOS_SOLO_GERENCIA), esto los restringe
    # un paso mas.
    "/forecast/plan_compra", "/api/forecast/plan_compras",
    "/forecast/nivel_servicio", "/api/forecast/nivel_servicio",
)


@app.before_request
def _restringir_inventario_adquisiciones():
    if not request.path.startswith(PREFIJOS_RESTRINGIDOS_INV_ADQ):
        return None
    if "usuario" not in session:
        return None  # el login_requerido/admin_requerido de la ruta se encarga del redirect a /login
    if session["usuario"] in USUARIOS_INVENTARIO_ADQUISICIONES:
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "msg": "No tienes acceso a esta sección."}), 403
    return redirect(url_for("inicio"))


# Seguimiento Metas/Ppto comparan venta real contra una meta/presupuesto
# fijado por vendedor o sucursal COMPLETOS -- para un perfil restringido
# solo por canal (ej. Elennys/E-commerce) eso compara "solo su venta de
# e-commerce" contra la meta de TODO el vendedor, mostrando practicamente
# siempre "100% atrasado" para cualquiera que no sea ella misma (y ella ni
# siquiera tiene meta propia cargada) -- probado en la practica, no una
# suposicion. Se oculta para cualquier cuenta con "canal" en sesion.
PREFIJOS_RESTRINGIDOS_CANAL = ("/metas", "/api/metas", "/ppto", "/api/ppto")


@app.before_request
def _restringir_metas_ppto_canal():
    if not request.path.startswith(PREFIJOS_RESTRINGIDOS_CANAL):
        return None
    if "usuario" not in session or not session.get("canal"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "msg": "No disponible para este perfil."}), 403
    return redirect(url_for("inicio"))


# Gerencia (pedido explicito del usuario, NO es lo mismo que
# admin=True -- fmusa y malvarado son gerencia pero no admin, y no hay
# ninguna cuenta admin=True que no sea gerencia): estas 5 ven todas las
# areas. Cualquier otra cuenta (Jefe de Sucursal, E-commerce, etc) solo
# ve Comercial -- ni siquiera las areas "en construccion" (Finanzas/
# Logistica/Bodega/Forecast/Tareas). Adquisiciones e Inventario ya
# tienen su propia restriccion mas estricta (solo dsepulveda) mas arriba.
USUARIOS_GERENCIA = {
    "dsepulveda@casamusa.cl", "emusa@casamusa.cl", "fmusa@casamusa.cl",
    "malvarado@casamusa.cl", "jsantana@casamusa.cl",
}

PREFIJOS_SOLO_GERENCIA = ("/finanzas", "/logistica", "/bodega", "/forecast", "/api/forecast", "/tareas")


@app.before_request
def _restringir_areas_no_comercial():
    if not request.path.startswith(PREFIJOS_SOLO_GERENCIA):
        return None
    if "usuario" not in session or session["usuario"] in USUARIOS_GERENCIA:
        return None
    return redirect(url_for("inicio"))


# ══════════════════════════════════════════════════════
#  Variables disponibles en todos los templates
# ══════════════════════════════════════════════════════
@app.context_processor
def inject_es_admin():
    suc = session.get("sucursal")
    # Si son varias sucursales combinadas (ej. Express = ["CH","MP"]),
    # el badge muestra el nombre del perfil, no la lista cruda.
    suc_label = session.get("nombre") if isinstance(suc, list) else suc
    canal_label = session.get("nombre") if session.get("canal") else None
    return {
        "es_admin": session.get("admin", False),
        "sucursal_sesion": suc_label,
        "canal_sesion": canal_label,
        # Jefe de Sucursal real, o un perfil sin sucursal general que
        # de todos modos tiene su propia fila de NE (ej. Elennys/
        # E-commerce bajo "CANAL DIGITAL") -- ver _sucursal_ne_forzada().
        "puede_cargar_ne": bool(session.get("sucursal") or session.get("sucursal_ne")),
    }


def _sucursal_forzada():
    """Sucursal a la que esta atado el usuario logueado (Jefe de Sucursal),
    o None si ve todo el dashboard sin restriccion."""
    return session.get("sucursal")


def _canal_forzado():
    """Canales de venta (tipo_venta) a los que esta atado el usuario
    logueado (ej. E-commerce), o None si ve todos los canales sin
    restriccion. Mismo mecanismo que _sucursal_forzada()."""
    return session.get("canal")


def _sucursal_ne_forzada():
    """Sucursal a usar SOLO para NE x Facturar -- session["sucursal"]
    para un Jefe de Sucursal real, o session["sucursal_ne"] para un
    perfil sin sucursal general (ej. Elennys/E-commerce, cuya fila de
    NE vive bajo "CANAL DIGITAL" en vendedor_home aunque sus reportes
    de Comercial no esten restringidos por sucursal)."""
    return session.get("sucursal") or session.get("sucursal_ne")


# ══════════════════════════════════════════════════════
#  HEALTHCHECK (Fase 0 Vercel+Supabase: prueba conexion a Postgres)
# ══════════════════════════════════════════════════════
@app.route("/api/ping", methods=["GET"])
def ping():
    try:
        import db
        resultado = db.query_one("SELECT now() AS hora_servidor")
        return jsonify({"ok": True, "hora_servidor": str(resultado["hora_servidor"])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ══════════════════════════════════════════════════════
#  AUTENTICACIÓN
# ══════════════════════════════════════════════════════
@app.route("/", methods=["GET"])
def index():
    if "usuario" in session:
        return redirect(url_for("inicio"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        gerente  = GERENTES.get(email)
        if gerente and gerente["password"] == password:
            session["usuario"]  = email
            session["nombre"]   = gerente["nombre"]
            session["admin"]    = gerente.get("admin", False)
            session["sucursal"] = gerente.get("sucursal")
            session["canal"]    = gerente.get("canal")
            session["sucursal_ne"] = gerente.get("sucursal_ne")
            return redirect(url_for("inicio"))
        error = "Correo o contraseña incorrectos."
    return render_template("login.html", error=error)


# ══════════════════════════════════════════════════════
#  AREAS — landing de todos los reportes de Casa Musa
#  Para agregar una nueva area con contenido real: crear sus rutas
#  propias y cambiar "activo" a True aqui.
# ══════════════════════════════════════════════════════
AREAS = [
    {"slug": "comercial",     "nombre": "Comercial",     "icono": "🛒", "url": "/resumen",       "activo": True},
    {"slug": "adquisiciones", "nombre": "Adquisiciones", "icono": "📦", "url": "/adquisiciones", "activo": True},
    {"slug": "finanzas",      "nombre": "Finanzas",      "icono": "💰", "url": "/finanzas",      "activo": False},
    {"slug": "inventario",    "nombre": "Inventario",    "icono": "🗄️", "url": "/inventario",    "activo": True},
    {"slug": "logistica",     "nombre": "Logística",     "icono": "🚚", "url": "/logistica",     "activo": False},
    {"slug": "bodega",        "nombre": "Bodega",        "icono": "🏭", "url": "/bodega",        "activo": False},
    {"slug": "forecast",      "nombre": "Forecast",      "icono": "🔮", "url": "/forecast",      "activo": True},
    {"slug": "tareas",        "nombre": "Tareas Pendientes de Gerencia", "icono": "📋", "url": "/tareas", "activo": False},
]


@app.route("/inicio")
@login_requerido
def inicio():
    if session.get("usuario") not in USUARIOS_GERENCIA:
        # No es gerencia (Jefe de Sucursal, E-commerce, etc) -- solo ve
        # Comercial, ni siquiera como tile deshabilitado. Pedido
        # explicito del usuario.
        areas_visibles = [a for a in AREAS if a["slug"] == "comercial"]
    else:
        areas_visibles = [
            a for a in AREAS
            if a["slug"] not in ("inventario", "adquisiciones", "forecast") or session.get("usuario") in USUARIOS_INVENTARIO_ADQUISICIONES
        ]
    return render_template("inicio.html", areas=areas_visibles, session_nombre=session.get("nombre"))


def _area_en_construccion(slug):
    area = next(a for a in AREAS if a["slug"] == slug)
    return render_template("area_construccion.html", area_nombre=area["nombre"], area_icono=area["icono"])


@app.route("/adquisiciones")
@login_requerido
def adquisiciones():
    return render_template("adquisiciones_resumen.html",
                           active="adquisiciones_resumen",
                           session_nombre=session.get("nombre"))


@app.route("/api/adquisiciones/resumen")
@login_requerido
def api_adquisiciones_resumen():
    try:
        tipo = request.args.get("tipo", "") or None
        if USAR_POSTGRES_ADQUISICIONES:
            return jsonify(data_loader_adquisiciones_pg.get_resumen_combinado_pg(tipo))
        return jsonify(data_loader_adquisiciones.get_resumen(tipo))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/adquisiciones/por_mes")
@login_requerido
def api_adquisiciones_por_mes():
    try:
        tipo = request.args.get("tipo", "") or None
        if USAR_POSTGRES_ADQUISICIONES:
            return jsonify(data_loader_adquisiciones_pg.get_por_mes_combinado_pg(tipo))
        return jsonify(data_loader_adquisiciones.get_compras_por_mes(tipo))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/adquisiciones/proveedores")
@login_requerido
def adquisiciones_proveedores():
    return render_template("adquisiciones_proveedores.html",
                           active="adquisiciones_proveedores",
                           session_nombre=session.get("nombre"))


@app.route("/api/adquisiciones/por_proveedor")
@login_requerido
def api_adquisiciones_por_proveedor():
    try:
        tipo = request.args.get("tipo", "") or None
        if USAR_POSTGRES_ADQUISICIONES:
            return jsonify(data_loader_adquisiciones_pg.get_por_proveedor_combinado_pg(tipo))
        return jsonify(data_loader_adquisiciones.get_compras_por_proveedor(tipo))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/adquisiciones/pedido")
@login_requerido
def adquisiciones_pedido():
    return render_template("adquisiciones_por_tipo.html",
                           active="adquisiciones_pedido",
                           tipo_compra="PEDIDO",
                           titulo="Compras a Pedido",
                           session_nombre=session.get("nombre"))


@app.route("/adquisiciones/stock")
@login_requerido
def adquisiciones_stock():
    return render_template("adquisiciones_por_tipo.html",
                           active="adquisiciones_stock",
                           tipo_compra="STOCK",
                           titulo="Compras para Stock",
                           session_nombre=session.get("nombre"))


@app.route("/adquisiciones/lead_time")
@login_requerido
def adquisiciones_lead_time():
    return render_template("adquisiciones_lead_time.html",
                           active="adquisiciones_lead_time",
                           session_nombre=session.get("nombre"))


@app.route("/api/adquisiciones/lead_time")
@login_requerido
def api_adquisiciones_lead_time():
    try:
        if USAR_POSTGRES_ADQUISICIONES:
            return jsonify(data_loader_adquisiciones_pg.get_lead_time_combinado_pg())
        return jsonify(data_loader_adquisiciones.get_lead_time_por_proveedor())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/adquisiciones/otif")
@login_requerido
def adquisiciones_otif():
    return render_template("adquisiciones_otif.html",
                           active="adquisiciones_otif",
                           session_nombre=session.get("nombre"))


@app.route("/api/adquisiciones/otif")
@login_requerido
def api_adquisiciones_otif():
    try:
        if USAR_POSTGRES_ADQUISICIONES:
            return jsonify(data_loader_adquisiciones_pg.get_cumplimiento_combinado_pg())
        return jsonify(data_loader_adquisiciones.get_cumplimiento_por_proveedor())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/finanzas")
@login_requerido
def finanzas():
    return _area_en_construccion("finanzas")


@app.route("/inventario")
@login_requerido
def inventario():
    return render_template("inventario_resumen.html",
                           active="inventario_resumen",
                           session_nombre=session.get("nombre"))


@app.route("/inventario/bodegas")
@login_requerido
def inventario_bodegas():
    return render_template("inventario_bodegas.html",
                           active="inventario_bodegas",
                           session_nombre=session.get("nombre"))


@app.route("/inventario/clasificacion")
@login_requerido
def inventario_clasificacion():
    return render_template("inventario_clasificacion.html",
                           active="inventario_clasificacion",
                           session_nombre=session.get("nombre"))


@app.route("/inventario/procedencia")
@login_requerido
def inventario_procedencia():
    return render_template("inventario_procedencia.html",
                           active="inventario_procedencia",
                           session_nombre=session.get("nombre"))


@app.route("/inventario/familia")
@login_requerido
def inventario_familia():
    return render_template("inventario_familia.html",
                           active="inventario_familia",
                           session_nombre=session.get("nombre"))


@app.route("/inventario/marca")
@login_requerido
def inventario_marca():
    return render_template("inventario_marca.html",
                           active="inventario_marca",
                           session_nombre=session.get("nombre"))


@app.route("/inventario/alertas")
@login_requerido
def inventario_alertas():
    return render_template("inventario_alertas.html",
                           active="inventario_alertas",
                           session_nombre=session.get("nombre"))


@app.route("/api/inventario/alertas_familias")
@login_requerido
def api_inventario_alertas_familias():
    try:
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_obligatorios_pg.get_familias_obligatorios_pg())
        return jsonify(data_loader_obligatorios.get_familias_obligatorios())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/alertas_quiebre")
@login_requerido
def api_inventario_alertas_quiebre():
    try:
        familia = request.args.get("familia", "") or None
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_obligatorios_pg.get_alertas_quiebre_critico_pg(familia))
        return jsonify(data_loader_obligatorios.get_alertas_quiebre_critico(familia))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/alertas_quiebre/exportar")
@login_requerido
def api_inventario_alertas_quiebre_exportar():
    try:
        familia = request.args.get("familia", "") or None
        if USAR_POSTGRES_INVENTARIO:
            buffer = data_loader_obligatorios_pg.exportar_alertas_excel_pg(familia)
        else:
            buffer = data_loader_obligatorios.exportar_alertas_excel(familia)
        nombre = f"Alertas_Quiebre_{familia or 'Todas'}.xlsx".replace(" ", "_")
        return send_file(
            buffer,
            as_attachment=True,
            download_name=nombre,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/inventario/distribucion")
@login_requerido
def inventario_distribucion():
    return render_template("inventario_distribucion.html",
                           active="inventario_distribucion",
                           session_nombre=session.get("nombre"))


@app.route("/api/inventario/distribucion")
@login_requerido
def api_inventario_distribucion():
    try:
        familia = request.args.get("familia", "") or None
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_obligatorios_pg.get_distribucion_desde_san_isidro_pg(familia))
        return jsonify(data_loader_obligatorios.get_distribucion_desde_san_isidro(familia))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/distribucion/exportar")
@login_requerido
def api_inventario_distribucion_exportar():
    try:
        familia = request.args.get("familia", "") or None
        if USAR_POSTGRES_INVENTARIO:
            buffer = data_loader_obligatorios_pg.exportar_distribucion_excel_pg(familia)
        else:
            buffer = data_loader_obligatorios.exportar_distribucion_excel(familia)
        nombre = f"Distribucion_San_Isidro_{familia or 'Todas'}.xlsx".replace(" ", "_")
        return send_file(
            buffer,
            as_attachment=True,
            download_name=nombre,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/forecast/plan_compra")
@login_requerido
def forecast_plan_compra():
    return render_template("forecast_plan_compra.html",
                           active="forecast_plan_compra",
                           session_nombre=session.get("nombre"))


@app.route("/api/forecast/plan_compras")
@login_requerido
def api_forecast_plan_compras():
    try:
        familia = request.args.get("familia", "") or None
        meses = request.args.get("meses", type=float)
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_obligatorios_pg.get_plan_compra_reposicion_pg(familia, meses))
        return jsonify(data_loader_obligatorios.get_plan_compra_reposicion(familia, meses))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/forecast/plan_compras/resumen_valor")
@login_requerido
def api_forecast_plan_compras_resumen_valor():
    try:
        familia = request.args.get("familia", "") or None
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_obligatorios_pg.get_resumen_valor_compra_pg(familia))
        return jsonify(data_loader_obligatorios.get_resumen_valor_compra(familia))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/forecast/plan_compras/exportar")
@login_requerido
def api_forecast_plan_compras_exportar():
    try:
        familia = request.args.get("familia", "") or None
        meses = request.args.get("meses", type=float)
        if USAR_POSTGRES_INVENTARIO:
            buffer = data_loader_obligatorios_pg.exportar_plan_compras_excel_pg(familia, meses)
        else:
            buffer = data_loader_obligatorios.exportar_plan_compras_excel(familia, meses)
        nombre = f"Plan_de_Compra_{familia or 'Todas'}.xlsx".replace(" ", "_")
        return send_file(
            buffer,
            as_attachment=True,
            download_name=nombre,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
#  SEGUNDA LINEA -- productos AAA/M05 fuera de Obligatorios (ver
#  data_loader_segunda_linea.py). Sin version Excel: se calcula 100%
#  desde Postgres, no requiere ningun archivo nuevo.
# ══════════════════════════════════════════════════════
@app.route("/inventario/alertas_segunda_linea")
@login_requerido
def inventario_alertas_segunda_linea():
    return render_template("inventario_alertas_2l.html",
                           active="inventario_alertas_2l",
                           session_nombre=session.get("nombre"))


@app.route("/inventario/distribucion_segunda_linea")
@login_requerido
def inventario_distribucion_segunda_linea():
    return render_template("inventario_distribucion_2l.html",
                           active="inventario_distribucion_2l",
                           session_nombre=session.get("nombre"))


@app.route("/api/inventario/segunda_linea/familias")
@login_requerido
def api_segunda_linea_familias():
    try:
        return jsonify(data_loader_segunda_linea.get_familias_segunda_linea())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/segunda_linea/alertas")
@login_requerido
def api_segunda_linea_alertas():
    try:
        familia = request.args.get("familia", "") or None
        return jsonify(data_loader_segunda_linea.get_alertas_quiebre_segunda_linea(familia))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/segunda_linea/alertas/exportar")
@login_requerido
def api_segunda_linea_alertas_exportar():
    try:
        familia = request.args.get("familia", "") or None
        buffer = data_loader_segunda_linea.exportar_alertas_excel(familia)
        nombre = f"Alertas_Segunda_Linea_{familia or 'Todas'}.xlsx".replace(" ", "_")
        return send_file(
            buffer,
            as_attachment=True,
            download_name=nombre,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/segunda_linea/distribucion")
@login_requerido
def api_segunda_linea_distribucion():
    try:
        familia = request.args.get("familia", "") or None
        return jsonify(data_loader_segunda_linea.get_distribucion_segunda_linea(familia))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/segunda_linea/distribucion/exportar")
@login_requerido
def api_segunda_linea_distribucion_exportar():
    try:
        familia = request.args.get("familia", "") or None
        buffer = data_loader_segunda_linea.exportar_distribucion_excel(familia)
        nombre = f"Distribucion_Segunda_Linea_{familia or 'Todas'}.xlsx".replace(" ", "_")
        return send_file(
            buffer,
            as_attachment=True,
            download_name=nombre,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/forecast/plan_compra_2da_linea")
@login_requerido
def forecast_plan_compra_2l():
    return render_template("forecast_plan_compra_2l.html",
                           active="forecast_plan_compra_2l",
                           session_nombre=session.get("nombre"))


@app.route("/api/forecast/plan_compras_2da_linea")
@login_requerido
def api_forecast_plan_compras_2l():
    try:
        familia = request.args.get("familia", "") or None
        meses = request.args.get("meses", type=float)
        return jsonify(data_loader_segunda_linea.get_plan_compra_segunda_linea(familia, meses))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/forecast/plan_compras_2da_linea/resumen_valor")
@login_requerido
def api_forecast_plan_compras_2l_resumen_valor():
    try:
        familia = request.args.get("familia", "") or None
        return jsonify(data_loader_segunda_linea.get_resumen_valor_compra_segunda_linea(familia))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/forecast/plan_compras_2da_linea/exportar")
@login_requerido
def api_forecast_plan_compras_2l_exportar():
    try:
        familia = request.args.get("familia", "") or None
        meses = request.args.get("meses", type=float)
        buffer = data_loader_segunda_linea.exportar_plan_compras_excel(familia, meses)
        nombre = f"Plan_de_Compra_Segunda_Linea_{familia or 'Todas'}.xlsx".replace(" ", "_")
        return send_file(
            buffer,
            as_attachment=True,
            download_name=nombre,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
#  NIVEL DE SERVICIO -- mide si el stock actual alcanza para cubrir la
#  demanda, general y por sucursal, sobre Productos Prioritarios (ver
#  data_loader_nivel_servicio.py).
# ══════════════════════════════════════════════════════
@app.route("/forecast/nivel_servicio")
@login_requerido
def forecast_nivel_servicio():
    return render_template("forecast_nivel_servicio.html",
                           active="forecast_nivel_servicio",
                           session_nombre=session.get("nombre"))


@app.route("/api/forecast/nivel_servicio")
@login_requerido
def api_forecast_nivel_servicio():
    try:
        return jsonify(data_loader_nivel_servicio.get_nivel_servicio_pg())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/resumen")
@login_requerido
def api_inventario_resumen():
    try:
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_inventario_pg.get_resumen_inventario_pg())
        return jsonify(data_loader_inventario.get_resumen_inventario())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/bodegas")
@login_requerido
def api_inventario_bodegas():
    try:
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_inventario_pg.get_inventario_por_bodega_pg())
        return jsonify(data_loader_inventario.get_inventario_por_bodega())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/clasificacion")
@login_requerido
def api_inventario_clasificacion():
    try:
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_inventario_pg.get_inventario_por_clasificacion_pg())
        return jsonify(data_loader_inventario.get_inventario_por_clasificacion())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/procedencia")
@login_requerido
def api_inventario_procedencia():
    try:
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_inventario_pg.get_inventario_por_procedencia_pg())
        return jsonify(data_loader_inventario.get_inventario_por_procedencia())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/familia")
@login_requerido
def api_inventario_familia():
    try:
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_inventario_pg.get_inventario_por_familia_pg())
        return jsonify(data_loader_inventario.get_inventario_por_familia())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/bodegas_lista")
@login_requerido
def api_inventario_bodegas_lista():
    try:
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_inventario_pg.get_bodegas_disponibles_pg())
        return jsonify(data_loader_inventario.get_bodegas_disponibles())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/marca_subfamilia")
@login_requerido
def api_inventario_marca_subfamilia():
    try:
        bodega = request.args.get("bodega", "")
        procedencia = request.args.get("procedencia", "todas")
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_inventario_pg.get_inventario_por_marca_subfamilia_pg(bodega, procedencia))
        return jsonify(data_loader_inventario.get_inventario_por_marca_subfamilia(bodega, procedencia))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/logistica")
@login_requerido
def logistica():
    return _area_en_construccion("logistica")


@app.route("/bodega")
@login_requerido
def bodega():
    return _area_en_construccion("bodega")


@app.route("/forecast")
@login_requerido
def forecast():
    return redirect(url_for("forecast_nivel_servicio"))


@app.route("/tareas")
@login_requerido
def tareas():
    return _area_en_construccion("tareas")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ══════════════════════════════════════════════════════
#  PÁGINAS (por ahora solo estructura, datos después)
# ══════════════════════════════════════════════════════
@app.route("/resumen")
@login_requerido
def resumen():
    return render_template("resumen.html",
                           active="resumen",
                           session_nombre=session.get("nombre"))


@app.route("/proyeccion")
@login_requerido
def proyeccion():
    return render_template("proyeccion.html",
                           active="proyeccion",
                           session_nombre=session.get("nombre"))


@app.route("/metas")
@login_requerido
def metas():
    return render_template("metas.html",
                           active="metas",
                           session_nombre=session.get("nombre"))


@app.route("/sucursales")
@login_requerido
def sucursales():
    return render_template("sucursales.html",
                           active="sucursales",
                           session_nombre=session.get("nombre"))


@app.route("/vendedores")
@login_requerido
def vendedores():
    return render_template("vendedores.html",
                           active="vendedores",
                           session_nombre=session.get("nombre"))


@app.route("/api/ventas_por_vendedor")
@login_requerido
def api_ventas_por_vendedor():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_ventas_por_vendedor_pg(filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_ventas_por_vendedor(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/canal")
@login_requerido
def canal():
    return render_template("canal.html",
                           active="canal",
                           session_nombre=session.get("nombre"))


@app.route("/api/ventas_por_canal")
@login_requerido
def api_ventas_por_canal():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_ventas_por_canal_pg(filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_ventas_por_canal(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/familia")
@login_requerido
def familia():
    return render_template("familia.html",
                           active="familia",
                           session_nombre=session.get("nombre"))


@app.route("/api/ventas_por_familia")
@login_requerido
def api_ventas_por_familia():
    try:
        agrupar_por = request.args.get("agrupar_por", "familia")
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_ventas_por_familia_pg(agrupar_por, filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_ventas_por_familia(agrupar_por, filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/procedencia")
@login_requerido
def procedencia():
    return render_template("procedencia.html",
                           active="procedencia",
                           session_nombre=session.get("nombre"))


@app.route("/api/ventas_por_procedencia")
@login_requerido
def api_ventas_por_procedencia():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_ventas_por_procedencia_pg(filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_ventas_por_procedencia(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/clientes")
@login_requerido
def clientes():
    return render_template("clientes.html",
                           active="clientes",
                           session_nombre=session.get("nombre"))


@app.route("/api/ventas_por_cliente")
@login_requerido
def api_ventas_por_cliente():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_ventas_por_cliente_pg(filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_ventas_por_cliente(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/productos")
@login_requerido
def productos():
    return render_template("productos.html",
                           active="productos",
                           session_nombre=session.get("nombre"))


@app.route("/api/ventas_por_producto")
@login_requerido
def api_ventas_por_producto():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_ventas_por_producto_pg(filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_ventas_por_producto(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/datos")
@login_requerido
def datos():
    return render_template("en_construccion.html",
                           active="datos",
                           session_nombre=session.get("nombre"),
                           pagina="Vista de Datos")


# ══════════════════════════════════════════════════════
#  ACTUALIZACION DE DATOS (archivo mensual YYMM_Vtas.xlsx)
# ══════════════════════════════════════════════════════
@app.route("/admin/actualizar", methods=["POST"])
@admin_requerido
def admin_actualizar():
    try:
        on_nuevo = data_loader_pg.sincronizar_ventas_pg if USAR_POSTGRES_COMERCIAL else None
        resultado = data_loader.actualizar_desde_archivo_mensual(on_nuevo=on_nuevo)
        if resultado.get("pg_sync_error"):
            resultado["msg"] += f" (aviso: sync Postgres fallo: {resultado['pg_sync_error']})"

        # Siempre, no solo cuando no hay archivo -- mismo fix que
        # actualizar_diario.py: un archivo puede consolidarse bien y
        # aun asi no traer fila para un dia sin actividad real (el
        # export de SAP omite los dias en $0), dejando el corte de
        # "Datos al" atrasado. confirmar_dia_sin_ventas() es
        # idempotente, no tiene efecto si el archivo ya cubre el dia
        # de ayer.
        on_confirmado = data_loader_pg.confirmar_fecha_pg if USAR_POSTGRES_COMERCIAL else None
        confirmacion = data_loader.confirmar_dia_sin_ventas(on_confirmado=on_confirmado)
        if confirmacion.get("pg_sync_error"):
            resultado["msg"] += f" (aviso: sync Postgres fecha_confirmada fallo: {confirmacion['pg_sync_error']})"

        return jsonify(resultado)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {str(e)}"}), 500


# ══════════════════════════════════════════════════════
#  SUBIR VENTAS POR WEB (directo a Postgres, sin depender de esta
#  maquina) -- pensado para que el Gerente Comercial/G. General suban
#  el export mensual de SAP desde cualquier lugar, arrastrando el
#  archivo en el navegador. No toca el Excel/cache local en absoluto
#  (por diseño -- ver CLAUDE.md): Postgres queda como la fuente real
#  de lo subido por esta via.
# ══════════════════════════════════════════════════════
COLUMNAS_SAP_REQUERIDAS = [
    "DOC_SAP", "FOLIO", "TIPO_DOC", "FECHA_CONTA", "FECHA_DOC",
    "CODIGO_CLIENTE", "NOMBRE_CLIENTE", "PROCEDENCIA", "SUCURSAL",
    "CODIGO_CM", "ID_PROCEDENCIA", "CODIGO_PROVEEDOR", "DESCRIPCION",
    "MARCA", "UNIDAD_MEDIDA", "FAMILIA", "SUBFAMILIA", "GRUPO",
    "CANTIDAD", "COSTO_CUP", "COSTO_TOTAL", "PRECIO_UNITARIO", "TOTAL",
    "UTILIDAD_BRUTA", "MG_BRUTO", "VENDEDOR", "COND_PAGO", "EMPRESA",
    "PROVEEDOR_POR_DEFECTO", "LIQUIDAR", "ESTATUS_SKU",
]


@app.route("/subir_ventas")
@admin_requerido
def subir_ventas():
    return render_template("subir_ventas.html",
                           active="subir_ventas",
                           session_nombre=session.get("nombre"))


@app.route("/api/subir_ventas", methods=["POST"])
@admin_requerido
def api_subir_ventas():
    if not USAR_POSTGRES_COMERCIAL:
        return jsonify({"ok": False, "msg": "Esta funcion requiere Postgres (USAR_POSTGRES_COMERCIAL=1)."}), 400

    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return jsonify({"ok": False, "msg": "No se recibio ningun archivo."}), 400
    if not archivo.filename.lower().endswith(".xlsx"):
        return jsonify({"ok": False, "msg": "El archivo debe ser .xlsx (export directo de SAP, sin convertir)."}), 400

    try:
        df = pd.read_excel(io.BytesIO(archivo.read()))
    except Exception as e:
        return jsonify({"ok": False, "msg": f"No se pudo leer el Excel: {e}"}), 400

    if "TIPO VENTA" in df.columns:
        df = df.rename(columns={"TIPO VENTA": "TIPO_VENTA"})
    faltantes = [c for c in COLUMNAS_SAP_REQUERIDAS if c not in df.columns]
    if faltantes:
        return jsonify({
            "ok": False,
            "msg": f"El archivo no tiene el formato esperado del export de SAP. Faltan columnas: {', '.join(faltantes)}",
        }), 400
    if len(df) == 0:
        return jsonify({"ok": False, "msg": "El archivo esta vacio (0 filas)."}), 400

    try:
        df = data_loader._normalizar_df(df)
        df = data_loader._aplicar_sucursal_logica(df)
        fechas = df["FECHA_CONTA"].dt.date.unique().tolist()
        data_loader_pg.sincronizar_ventas_pg(df, fechas)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error al procesar/subir el archivo: {e}"}), 500

    n_filas   = len(df)
    vta_total = round(float(df["TOTAL"].sum()), 0)
    f_min, f_max = min(fechas), max(fechas)
    rango = f_min.strftime("%d/%m/%Y") if f_min == f_max else f"{f_min.strftime('%d/%m/%Y')} - {f_max.strftime('%d/%m/%Y')}"
    filas_fmt = f"{n_filas:,}".replace(",", ".")
    vta_fmt   = data_loader.fmt_clp(vta_total)
    return jsonify({
        "ok": True,
        "filas": n_filas,
        "vta": vta_total,
        "rango": rango,
        "msg": f"OK: {filas_fmt} filas cargadas ({vta_fmt}), {rango}.",
    })


# ══════════════════════════════════════════════════════
#  NE X FACTURAR — tabla web (reemplaza el Excel de columnas
#  bloqueadas: el roster sale de vendedor_home, el monto se edita y
#  guarda directo en Postgres)
# ══════════════════════════════════════════════════════
@app.route("/cargar_ne")
@admin_o_jefe_sucursal_requerido
def cargar_ne():
    return render_template("cargar_ne.html",
                           active="cargar_ne",
                           session_nombre=session.get("nombre"))


@app.route("/api/cargar_ne", methods=["GET", "POST"])
@admin_o_jefe_sucursal_requerido
def api_cargar_ne():
    if not USAR_POSTGRES_COMERCIAL:
        return jsonify({"ok": False, "msg": "Esta funcion requiere Postgres (USAR_POSTGRES_COMERCIAL=1)."}), 400
    try:
        if request.method == "GET":
            return jsonify({"ok": True, "filas": data_loader_pg.get_ne_x_facturar_pg(filtro_sucursal=_sucursal_ne_forzada())})

        body = request.get_json(silent=True) or {}
        filas = body.get("filas", [])
        n_guardadas = data_loader_pg.guardar_ne_x_facturar_pg(
            filas, updated_by=session.get("usuario", "admin"), sucursales_permitidas=_sucursal_ne_forzada(),
        )
        return jsonify({"ok": True, "msg": f"Guardado: {n_guardadas} filas de NE x Facturar."})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


# ══════════════════════════════════════════════════════
#  METAS — tabla web (reemplaza metas.xlsx; roster desde
#  vendedor_home, una meta por vendedor/mes/año)
# ══════════════════════════════════════════════════════
@app.route("/cargar_metas")
@admin_requerido
def cargar_metas():
    return render_template("cargar_metas.html",
                           active="cargar_metas",
                           session_nombre=session.get("nombre"))


@app.route("/api/cargar_metas", methods=["GET", "POST"])
@admin_requerido
def api_cargar_metas():
    if not USAR_POSTGRES_COMERCIAL:
        return jsonify({"ok": False, "msg": "Esta funcion requiere Postgres (USAR_POSTGRES_COMERCIAL=1)."}), 400
    try:
        if request.method == "GET":
            hoy = data_loader._hoy()
            ano = int(request.args.get("ano", hoy.year))
            mes = int(request.args.get("mes", hoy.month))
            return jsonify({"ok": True, "ano": ano, "mes": mes, "filas": data_loader_pg.get_metas_roster_pg(ano, mes)})

        body = request.get_json(silent=True) or {}
        ano   = int(body.get("ano"))
        mes   = int(body.get("mes"))
        filas = body.get("filas", [])
        data_loader_pg.guardar_metas_pg(ano, mes, filas)
        return jsonify({"ok": True, "msg": f"Guardado: {len(filas)} metas de {mes:02d}/{ano}."})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


# ══════════════════════════════════════════════════════
#  GESTIONAR VENDEDORES — mover/sacar/reemplazar sin tocar codigo
#  (ver CLAUDE.md, seccion "En Postgres: cambiar de sucursal...")
# ══════════════════════════════════════════════════════
@app.route("/gestionar_vendedores")
@admin_requerido
def gestionar_vendedores():
    return render_template("gestionar_vendedores.html",
                           active="gestionar_vendedores",
                           session_nombre=session.get("nombre"))


@app.route("/api/gestionar_vendedores/datos")
@admin_requerido
def api_gestionar_vendedores_datos():
    if not USAR_POSTGRES_COMERCIAL:
        return jsonify({"ok": False, "msg": "Esta funcion requiere Postgres (USAR_POSTGRES_COMERCIAL=1)."}), 400
    try:
        return jsonify({
            "ok": True,
            "roster": data_loader_pg.get_roster_vendedor_home_pg(),
            "vendedores": data_loader_pg.get_vendedores_con_venta_pg(),
            "sucursales": data_loader.ORDEN_SUCURSALES,
        })
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


@app.route("/api/gestionar_vendedores", methods=["POST"])
@admin_requerido
def api_gestionar_vendedores():
    if not USAR_POSTGRES_COMERCIAL:
        return jsonify({"ok": False, "msg": "Esta funcion requiere Postgres (USAR_POSTGRES_COMERCIAL=1)."}), 400
    body = request.get_json(silent=True) or {}
    accion = body.get("accion")
    quien = session.get("usuario", "admin")

    def _fecha(valor):
        return date.fromisoformat(valor) if valor else None

    try:
        if accion == "asignar":
            vendedor = (body.get("vendedor") or "").strip().upper()
            sucursal = body.get("sucursal")
            if not vendedor or not sucursal:
                return jsonify({"ok": False, "msg": "Falta el nombre del vendedor o la sucursal."}), 400
            data_loader_pg.asignar_vendedor_home(vendedor, sucursal, _fecha(body.get("vigente_desde")), updated_by=quien)
            return jsonify({"ok": True, "msg": f"{vendedor} asignado a {sucursal}."})

        if accion == "quitar":
            vendedor = (body.get("vendedor") or "").strip().upper()
            if not vendedor:
                return jsonify({"ok": False, "msg": "Falta el nombre del vendedor."}), 400
            data_loader_pg.quitar_vendedor_home(vendedor)
            return jsonify({"ok": True, "msg": f"{vendedor} sacado del equipo — su venta pasa a 'Otros'."})

        if accion == "reemplazar":
            viejo    = (body.get("nombre_viejo") or "").strip().upper()
            nuevo    = (body.get("nombre_nuevo") or "").strip().upper()
            sucursal = body.get("sucursal")
            if not viejo or not nuevo or not sucursal:
                return jsonify({"ok": False, "msg": "Falta el vendedor que se va, el que entra, o la sucursal."}), 400
            data_loader_pg.reemplazar_vendedor(viejo, nuevo, sucursal, _fecha(body.get("vigente_desde")), updated_by=quien)
            return jsonify({"ok": True, "msg": f"{nuevo} reemplaza a {viejo} en {sucursal} (metas/NE ya transferidas)."})

        return jsonify({"ok": False, "msg": "Accion no reconocida."}), 400
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


# ══════════════════════════════════════════════════════
#  GESTIONAR PRODUCTOS COMPRA -- marcar productos como "no se compra"
#  (afecta solo Plan de Compra, Obligatorios y Segunda Linea; el
#  producto sigue visible en Alertas/Distribucion con su stock real).
#  Reemplaza tener que pedir un filtro SQL nuevo cada vez.
# ══════════════════════════════════════════════════════
@app.route("/gestionar_productos_compra")
@admin_requerido
def gestionar_productos_compra():
    return render_template("gestionar_productos_compra.html",
                           active="gestionar_productos_compra",
                           session_nombre=session.get("nombre"))


@app.route("/api/gestionar_productos_compra/datos")
@admin_requerido
def api_gestionar_productos_compra_datos():
    try:
        return jsonify({"ok": True, "excluidos": data_loader_exclusion_compra.get_productos_no_comprar()})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


@app.route("/api/gestionar_productos_compra/familias")
@admin_requerido
def api_gestionar_productos_compra_familias():
    try:
        return jsonify({"ok": True, "familias": data_loader_exclusion_compra.get_familias_productos()})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


@app.route("/api/gestionar_productos_compra/buscar")
@admin_requerido
def api_gestionar_productos_compra_buscar():
    try:
        q = request.args.get("q", "")
        familia = request.args.get("familia", "") or None
        resultados, total = data_loader_exclusion_compra.buscar_productos(q, familia)
        return jsonify({"ok": True, "resultados": resultados, "total": total})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


@app.route("/api/gestionar_productos_compra", methods=["POST"])
@admin_requerido
def api_gestionar_productos_compra():
    body = request.get_json(silent=True) or {}
    accion = body.get("accion")
    quien = session.get("usuario", "admin")

    try:
        if accion == "agregar":
            codigo = body.get("codigo")
            if not codigo:
                return jsonify({"ok": False, "msg": "Falta el código del producto."}), 400
            data_loader_exclusion_compra.agregar_no_comprar(int(codigo), motivo=body.get("motivo") or None, updated_by=quien)
            return jsonify({"ok": True, "msg": f"Código {codigo} marcado como 'no se compra'."})

        if accion == "quitar":
            codigo = body.get("codigo")
            if not codigo:
                return jsonify({"ok": False, "msg": "Falta el código del producto."}), 400
            data_loader_exclusion_compra.quitar_no_comprar(int(codigo))
            return jsonify({"ok": True, "msg": f"Código {codigo} vuelve a considerarse para compra."})

        return jsonify({"ok": False, "msg": "Accion no reconocida."}), 400
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


# ══════════════════════════════════════════════════════
#  GESTIONAR PRIORIDAD -- promover un producto (tipicamente de
#  Segunda Linea) a la lista curada de Obligatorios/Prioridad, o
#  quitarlo (vuelve solo a Segunda Linea si sigue siendo AAA/M05).
#  Reemplaza editar Productos_Obligatorios.xlsx a mano.
# ══════════════════════════════════════════════════════
@app.route("/gestionar_prioridad")
@admin_requerido
def gestionar_prioridad():
    return render_template("gestionar_prioridad.html",
                           active="gestionar_prioridad",
                           session_nombre=session.get("nombre"))


@app.route("/api/gestionar_prioridad/datos")
@admin_requerido
def api_gestionar_prioridad_datos():
    if not USAR_POSTGRES_INVENTARIO:
        return jsonify({"ok": False, "msg": "Esta funcion requiere Postgres (USAR_POSTGRES_INVENTARIO=1)."}), 400
    try:
        return jsonify({"ok": True, "prioridad": data_loader_obligatorios_pg.get_lista_prioridad()})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


@app.route("/api/gestionar_prioridad/familias")
@admin_requerido
def api_gestionar_prioridad_familias():
    try:
        return jsonify({"ok": True, "familias": data_loader_exclusion_compra.get_familias_productos()})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


@app.route("/api/gestionar_prioridad/buscar")
@admin_requerido
def api_gestionar_prioridad_buscar():
    try:
        q = request.args.get("q", "")
        familia = request.args.get("familia", "") or None
        # Solo excluye el digito 6 (no 3/7): promover un producto
        # Importado a Prioridad con su equivalente Nacional es
        # justamente la forma de resolverle su "sin injerencia de
        # compra" (ver data_loader_segunda_linea.py). excluir_sm0=True
        # porque no tiene sentido promover algo sin movimiento.
        resultados, total = data_loader_exclusion_compra.buscar_productos(
            q, familia, prefijos_excluidos=data_loader_exclusion_compra.PREFIJOS_FUERA_SEGUNDA_LINEA,
            excluir_sm0=True,
        )
        resultados = [
            {
                "codigo":      r["codigo"],
                "descripcion": r["descripcion"],
                "familia":     r["familia"],
                "subfamilia":  r["subfamilia"],
                "procedencia": "Importado" if r["id_procedencia"] in data_loader_inventario.IDS_IMPORTADO else "Nacional",
            }
            for r in resultados
        ]
        return jsonify({"ok": True, "resultados": resultados, "total": total})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


@app.route("/api/gestionar_prioridad", methods=["POST"])
@admin_requerido
def api_gestionar_prioridad():
    if not USAR_POSTGRES_INVENTARIO:
        return jsonify({"ok": False, "msg": "Esta funcion requiere Postgres (USAR_POSTGRES_INVENTARIO=1)."}), 400
    body = request.get_json(silent=True) or {}
    accion = body.get("accion")
    quien = session.get("usuario", "admin")

    try:
        if accion == "promover":
            codigo = body.get("codigo")
            procedencia = body.get("procedencia")
            if not codigo or not procedencia:
                return jsonify({"ok": False, "msg": "Falta el código o la procedencia."}), 400
            codigo_equivalente = body.get("codigo_equivalente") or None
            data_loader_obligatorios_pg.promover_a_prioridad(
                int(codigo), procedencia,
                codigo_equivalente=int(codigo_equivalente) if codigo_equivalente else None,
                updated_by=quien,
            )
            return jsonify({"ok": True, "msg": f"Código {codigo} promovido a Prioridad."})

        if accion == "quitar":
            codigo = body.get("codigo")
            if not codigo:
                return jsonify({"ok": False, "msg": "Falta el código del producto."}), 400
            data_loader_obligatorios_pg.quitar_de_prioridad(int(codigo))
            return jsonify({"ok": True, "msg": f"Código {codigo} sacado de Prioridad."})

        return jsonify({"ok": False, "msg": "Accion no reconocida."}), 400
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {e}"}), 500


@app.route("/admin/actualizar_inventario", methods=["POST"])
@admin_requerido
def admin_actualizar_inventario():
    try:
        resultado = data_loader_inventario.actualizar_desde_archivo()
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {str(e)}"}), 500


# ══════════════════════════════════════════════════════
#  SUBIR INVENTARIO — self-service (reemplaza el flujo de dejar el
#  archivo en data/inventario/ + boton "Actualizar datos")
# ══════════════════════════════════════════════════════
@app.route("/subir_inventario")
@admin_requerido
def subir_inventario():
    return render_template("subir_inventario.html",
                           active="subir_inventario",
                           session_nombre=session.get("nombre"))


@app.route("/api/subir_inventario", methods=["POST"])
@admin_requerido
def api_subir_inventario():
    if not USAR_POSTGRES_INVENTARIO:
        return jsonify({"ok": False, "msg": "Esta funcion requiere Postgres (USAR_POSTGRES_INVENTARIO=1)."}), 400

    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return jsonify({"ok": False, "msg": "No se recibio ningun archivo."}), 400
    if not archivo.filename.lower().endswith(".xlsx"):
        return jsonify({"ok": False, "msg": "El archivo debe ser .xlsx (export directo de SAP, sin convertir)."}), 400

    import tempfile
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(archivo.read())
            tmp_path = tmp.name
        df = data_loader_inventario._leer_hoja_con_datos(tmp_path, columnas_esperadas=["CODIGO", "CUP"])
    except Exception as e:
        return jsonify({"ok": False, "msg": f"No se pudo leer el Excel: {e}"}), 400
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    faltantes = [c for c in ("CODIGO", "CUP") if c not in df.columns]
    if faltantes:
        return jsonify({
            "ok": False,
            "msg": f"El archivo no tiene el formato esperado del export de SAP. Faltan columnas: {', '.join(faltantes)}",
        }), 400
    if len(df) == 0:
        return jsonify({"ok": False, "msg": "El archivo esta vacio (0 filas)."}), 400

    df["CUP"] = pd.to_numeric(df["CUP"], errors="coerce").fillna(0)
    # Fusiona Datos_Duros_Inventario.xlsx si existe en este servidor (la
    # maquina local lo tiene; Vercel no) -- si no existe, cargar_productos/
    # cargar_stock conservan FAMILIA/SUBFAMILIA/GRUPO/venta_mensual que ya
    # estaban en Postgres en vez de borrarlos (ver backfill_inventario.py).
    df = data_loader_inventario.fusionar_datos_duros(df)

    try:
        data_loader_inventario_pg.sincronizar_inventario_pg(df)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error al procesar/subir el archivo: {e}"}), 500

    n_filas = len(df)
    filas_fmt = f"{n_filas:,}".replace(",", ".")
    return jsonify({
        "ok": True,
        "filas": n_filas,
        "msg": f"OK: inventario actualizado, {filas_fmt} productos.",
    })


# ══════════════════════════════════════════════════════
#  SUBIR DATOS DUROS — self-service (reemplaza dejar
#  Datos_Duros_Inventario.xlsx a mano en el servidor). Solo actualiza
#  el archivo en disco -- FAMILIA/SUBFAMILIA/GRUPO/venta_mensual
#  llegan a Postgres en la SIGUIENTE subida de /subir_inventario (que
#  ya fusiona contra este archivo), no hace falta duplicar esa logica
#  aqui.
# ══════════════════════════════════════════════════════
@app.route("/subir_datos_duros")
@admin_requerido
def subir_datos_duros():
    return render_template("subir_datos_duros.html",
                           active="subir_datos_duros",
                           session_nombre=session.get("nombre"))


@app.route("/api/subir_datos_duros", methods=["POST"])
@admin_requerido
def api_subir_datos_duros():
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return jsonify({"ok": False, "msg": "No se recibio ningun archivo."}), 400
    if not archivo.filename.lower().endswith(".xlsx"):
        return jsonify({"ok": False, "msg": "El archivo debe ser .xlsx."}), 400

    import tempfile
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            archivo.save(tmp.name)
            tmp_path = tmp.name
        dd = data_loader_inventario._leer_hoja_con_datos(
            tmp_path, columnas_esperadas=["CODIGO", "FAMILIA", "SUBFAMILIA"]
        )
    except Exception as e:
        return jsonify({"ok": False, "msg": f"No se pudo leer el Excel: {e}"}), 400

    faltantes = [c for c in ("CODIGO", "FAMILIA", "SUBFAMILIA", "GRUPO") if c not in dd.columns]
    if faltantes:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return jsonify({
            "ok": False,
            "msg": f"El archivo no tiene el formato esperado de Datos Duros. Faltan columnas: {', '.join(faltantes)}",
        }), 400
    if len(dd) == 0:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return jsonify({"ok": False, "msg": "El archivo esta vacio (0 filas)."}), 400

    cols_venta = [c for c in dd.columns if c.startswith("VENTA MENSUAL")]

    # Datos Duros no tiene tabla en Postgres (a diferencia de Compras/
    # Recepciones) -- el Excel local ES la fuente de verdad, asi que si
    # no se puede escribir (p.ej. filesystem de solo lectura en Vercel)
    # hay que avisarlo claro, no dejar que crashee sin JSON de vuelta.
    try:
        os.makedirs(data_loader_inventario.DATA_DIR_INVENTARIO, exist_ok=True)
        shutil.move(tmp_path, data_loader_inventario.DATOS_DUROS_XLSX)
    except OSError as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return jsonify({
            "ok": False,
            "msg": f"No se pudo guardar el archivo en el servidor (filesystem de solo lectura): {e}. "
                   "Esta pantalla requiere ejecutarse localmente hasta que Datos Duros tenga tabla en Postgres.",
        }), 500
    data_loader_inventario._cache["mod_time_dd"] = None  # forzar relectura la proxima vez

    n_filas = len(dd)
    filas_fmt = f"{n_filas:,}".replace(",", ".")
    return jsonify({
        "ok": True,
        "filas": n_filas,
        "columnas_venta": cols_venta,
        "msg": (
            f"OK: Datos Duros actualizado, {filas_fmt} productos, "
            f"{len(cols_venta)} columnas de venta mensual detectadas "
            f"({', '.join(cols_venta)}). Se aplica a Postgres en la proxima subida de stock (/subir_inventario)."
        ),
    })


# ══════════════════════════════════════════════════════
#  SUBIR COMPRAS / RECEPCIONES — self-service (reemplaza dejar
#  Compras_20XX.xlsx / Recepciones_20XX.xlsx a mano en el servidor).
#  Cada carga reemplaza SOLO el año elegido (no el otro), tanto en el
#  archivo local (ground truth Excel, ver data_loader_adquisiciones.py)
#  como en Postgres.
# ══════════════════════════════════════════════════════
@app.route("/subir_compras")
@admin_requerido
def subir_compras():
    return render_template("subir_compras.html",
                           active="subir_compras",
                           session_nombre=session.get("nombre"))


def _leer_archivo_subido(archivo, tmp_suffix=".xlsx"):
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=tmp_suffix, delete=False) as tmp:
        archivo.save(tmp.name)
        return tmp.name


@app.route("/api/subir_compras", methods=["POST"])
@admin_requerido
def api_subir_compras():
    if not USAR_POSTGRES_ADQUISICIONES:
        return jsonify({"ok": False, "msg": "Esta funcion requiere Postgres (USAR_POSTGRES_ADQUISICIONES=1)."}), 400

    archivo = request.files.get("archivo")
    ano = request.form.get("ano")
    if not archivo or not archivo.filename:
        return jsonify({"ok": False, "msg": "No se recibio ningun archivo."}), 400
    if ano not in ("2025", "2026"):
        return jsonify({"ok": False, "msg": "Falta indicar el año (2025 o 2026)."}), 400
    if not archivo.filename.lower().endswith(".xlsx"):
        return jsonify({"ok": False, "msg": "El archivo debe ser .xlsx."}), 400
    ano = int(ano)

    tmp_path = None
    try:
        tmp_path = _leer_archivo_subido(archivo)
        xl = None
        try:
            try:
                xl = pd.ExcelFile(tmp_path, engine="calamine")
            except Exception:
                xl = pd.ExcelFile(tmp_path)
            df = data_loader_adquisiciones._hoja_con_datos(xl)
        finally:
            # Cerrar el handle ANTES de que el except de mas abajo intente
            # os.remove(tmp_path) -- un finally en el try/except externo
            # corre DESPUES del cuerpo del except (incluido su return), asi
            # que cerrar alla no alcanza a tiempo (bug real encontrado
            # 2026-08-26: PermissionError de Windows al intentar borrar un
            # archivo que ExcelFile todavia tenia abierto).
            if xl is not None:
                xl.close()
    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return jsonify({"ok": False, "msg": f"No se pudo leer el Excel: {e}"}), 400

    faltantes = [c for c in data_loader_adquisiciones.COLUMNAS_ESPERADAS if c not in df.columns]
    if faltantes:
        os.remove(tmp_path)
        return jsonify({"ok": False, "msg": f"Faltan columnas esperadas: {', '.join(sorted(faltantes))}"}), 400
    if len(df) == 0:
        os.remove(tmp_path)
        return jsonify({"ok": False, "msg": "El archivo esta vacio (0 filas)."}), 400

    df["FECHA_CREACION"] = pd.to_datetime(df["FECHA_CREACION"])
    df = df[~df["NOMBRE_PROVEEDOR"].isin(data_loader_adquisiciones.PROVEEDORES_EXCLUIDOS)]

    # Reemplaza el Excel local (ground truth) para este año -- mismo
    # nombre fijo que ya usa data_loader_adquisiciones.py. Best-effort:
    # en Vercel el filesystem del proyecto es de solo lectura (solo
    # /tmp es escribible), asi que esto falla ahi -- no debe abortar
    # la carga real a Postgres, que es la fuente de verdad en produccion.
    destino = data_loader_adquisiciones.COMPRAS_2025_XLSX if ano == 2025 else data_loader_adquisiciones.COMPRAS_2026_XLSX
    try:
        os.makedirs(data_loader_adquisiciones.DATA_DIR_ADQUISICIONES, exist_ok=True)
        shutil.copy(tmp_path, destino)
        data_loader_adquisiciones._cache_compras[ano] = {"df": None, "mod_time": None}
    except OSError:
        pass
    finally:
        os.remove(tmp_path)

    try:
        import scripts.backfill_adquisiciones as ba
        ba.cargar_compras(ano, df)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Archivo guardado, pero fallo la carga a Postgres: {e}"}), 500

    n_filas = len(df)
    return jsonify({
        "ok": True,
        "filas": n_filas,
        "msg": f"OK: Compras {ano} actualizado, {n_filas:,}".replace(",", ".") + " filas.",
    })


@app.route("/api/subir_recepciones", methods=["POST"])
@admin_requerido
def api_subir_recepciones():
    if not USAR_POSTGRES_ADQUISICIONES:
        return jsonify({"ok": False, "msg": "Esta funcion requiere Postgres (USAR_POSTGRES_ADQUISICIONES=1)."}), 400

    archivo = request.files.get("archivo")
    ano = request.form.get("ano")
    if not archivo or not archivo.filename:
        return jsonify({"ok": False, "msg": "No se recibio ningun archivo."}), 400
    if ano not in ("2025", "2026"):
        return jsonify({"ok": False, "msg": "Falta indicar el año (2025 o 2026)."}), 400
    if not archivo.filename.lower().endswith(".xlsx"):
        return jsonify({"ok": False, "msg": "El archivo debe ser .xlsx."}), 400
    ano = int(ano)

    tmp_path = None
    try:
        tmp_path = _leer_archivo_subido(archivo)
        try:
            df = pd.read_excel(tmp_path, engine="calamine")
        except Exception:
            df = pd.read_excel(tmp_path)
    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return jsonify({"ok": False, "msg": f"No se pudo leer el Excel: {e}"}), 400

    columnas_esperadas_recepciones = ["FECHA_RECEPCION", "N_OC", "TOTAL_CLP", "NOMBRE_PROVEEDOR"]
    faltantes = [c for c in columnas_esperadas_recepciones if c not in df.columns]
    if faltantes:
        os.remove(tmp_path)
        return jsonify({"ok": False, "msg": f"Faltan columnas esperadas: {', '.join(faltantes)}"}), 400
    if len(df) == 0:
        os.remove(tmp_path)
        return jsonify({"ok": False, "msg": "El archivo esta vacio (0 filas)."}), 400

    df["FECHA_RECEPCION"] = pd.to_datetime(df["FECHA_RECEPCION"])
    df = df[~df["NOMBRE_PROVEEDOR"].isin(data_loader_adquisiciones.PROVEEDORES_EXCLUIDOS)]

    # Best-effort -- ver comentario equivalente en /api/subir_compras.
    destino = data_loader_adquisiciones.RECEPCIONES_2025_XLSX if ano == 2025 else data_loader_adquisiciones.RECEPCIONES_2026_XLSX
    try:
        os.makedirs(data_loader_adquisiciones.DATA_DIR_ADQUISICIONES, exist_ok=True)
        shutil.copy(tmp_path, destino)
        data_loader_adquisiciones._cache_recepciones[ano] = {"df": None, "mod_time": None}
    except OSError:
        pass
    finally:
        os.remove(tmp_path)

    try:
        import scripts.backfill_adquisiciones as ba
        ba.cargar_recepciones(ano, df)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Archivo guardado, pero fallo la carga a Postgres: {e}"}), 500

    n_filas = len(df)
    return jsonify({
        "ok": True,
        "filas": n_filas,
        "msg": f"OK: Recepciones {ano} actualizado, {n_filas:,}".replace(",", ".") + " filas.",
    })


# ══════════════════════════════════════════════════════
#  API — DATOS REALES
# ══════════════════════════════════════════════════════
@app.route("/api/resumen")
@login_requerido
def api_resumen():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_resumen_pg(filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_resumen(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ventas_por_mes")
@login_requerido
def api_ventas_por_mes():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_ventas_por_mes_pg(filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_ventas_por_mes(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ventas_por_sucursal")
@login_requerido
def api_ventas_por_sucursal():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_ventas_por_sucursal_pg(filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_ventas_por_sucursal(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/filtros_proyeccion")
@login_requerido
def api_filtros_proyeccion():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_filtros_proyeccion_pg(filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_filtros_proyeccion(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/proyeccion", methods=["POST"])
@login_requerido
def api_proyeccion():
    try:
        filtros = request.get_json(silent=True) or {}
        if _sucursal_forzada():
            filtros["sucursal"] = _sucursal_forzada()
        if _canal_forzado():
            filtros["tipo_venta"] = _canal_forzado()
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_proyeccion_pg(filtros))
        return jsonify(data_loader.get_proyeccion(filtros))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/metas", methods=["POST"])
@login_requerido
def api_metas():
    try:
        filtros = request.get_json(silent=True) or {}
        if _sucursal_forzada():
            filtros["sucursal"] = _sucursal_forzada()
        if _canal_forzado():
            filtros["tipo_venta"] = _canal_forzado()
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_seguimiento_metas_pg(filtros))
        return jsonify(data_loader.get_seguimiento_metas(filtros))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ppto")
@login_requerido
def ppto():
    return render_template("ppto.html",
                           active="ppto",
                           session_nombre=session.get("nombre"))


@app.route("/api/ppto", methods=["GET", "POST"])
@login_requerido
def api_ppto():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_seguimiento_ppto_pg(filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_seguimiento_ppto(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/vta_acum")
@login_requerido
def vta_acum():
    return render_template("vta_acum.html",
                           active="vta_acum",
                           session_nombre=session.get("nombre"))


@app.route("/api/filtros_vta_acum")
@login_requerido
def api_filtros_vta_acum():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_filtros_vta_acum_pg(filtro_sucursal=_sucursal_forzada(), filtro_canal=_canal_forzado()))
        return jsonify(data_loader.get_filtros_vta_acum(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vta_acum", methods=["POST"])
@login_requerido
def api_vta_acum():
    try:
        filtros = request.get_json(silent=True) or {}
        if _sucursal_forzada():
            filtros["sucursal"] = _sucursal_forzada()
        if _canal_forzado():
            filtros["tipo_venta"] = _canal_forzado()
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_vta_acum_pg(filtros))
        return jsonify(data_loader.get_vta_acum(filtros))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/vta_mes_mg")
@login_requerido
def vta_mes_mg():
    return render_template("vta_mes_mg.html",
                           active="vta_mes_mg",
                           session_nombre=session.get("nombre"))


@app.route("/api/vta_mes_mg", methods=["POST"])
@login_requerido
def api_vta_mes_mg():
    try:
        filtros = request.get_json(silent=True) or {}
        if _sucursal_forzada():
            filtros["sucursal"] = _sucursal_forzada()
        if _canal_forzado():
            filtros["tipo_venta"] = _canal_forzado()
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_vta_mes_mg_acum_pg(filtros))
        return jsonify(data_loader.get_vta_mes_mg_acum(filtros))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/vta_mg_mensual")
@login_requerido
def vta_mg_mensual():
    return render_template("vta_mg_mensual.html",
                           active="vta_mg_mensual",
                           session_nombre=session.get("nombre"))


@app.route("/api/vta_mg_mensual", methods=["POST"])
@login_requerido
def api_vta_mg_mensual():
    try:
        filtros = request.get_json(silent=True) or {}
        if _sucursal_forzada():
            filtros["sucursal"] = _sucursal_forzada()
        if _canal_forzado():
            filtros["tipo_venta"] = _canal_forzado()
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_vta_mg_mensual_pg(filtros))
        return jsonify(data_loader.get_vta_mg_mensual(filtros))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/vta_mg")
@login_requerido
def vta_mg():
    return render_template("vta_mg.html",
                           active="vta_mg",
                           session_nombre=session.get("nombre"))


@app.route("/api/vta_mg", methods=["POST"])
@login_requerido
def api_vta_mg():
    try:
        filtros = request.get_json(silent=True) or {}
        if _sucursal_forzada():
            filtros["sucursal"] = _sucursal_forzada()
        if _canal_forzado():
            filtros["tipo_venta"] = _canal_forzado()
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_vta_mg_pg(filtros))
        return jsonify(data_loader.get_vta_mg(filtros))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
#  ARRANQUE
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 5000))
    print("")
    print("  ╔══════════════════════════════════════╗")
    print("  ║   CASAMUSA — Dashboard de Gerencia   ║")
    print("  ╚══════════════════════════════════════╝")
    print("")
    print(f"  Abre tu navegador en: http://localhost:{puerto}")
    print("")
    app.run(debug=False, host="0.0.0.0", port=puerto)
