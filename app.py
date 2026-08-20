from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from functools import wraps
from datetime import date
import io
import os
import sys
import pandas as pd
import data_loader
import data_loader_inventario
import data_loader_obligatorios
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
GERENTES = {
    "dsepulveda@casamusa.cl": {"password": "Admin2026",         "nombre": "Administrador", "admin": True},
    "emusa@casamusa.cl":      {"password": "GGeneral2026",      "nombre": "G. General", "admin": True},
    "fmusa@casamusa.cl":      {"password": "Importaciones2026", "nombre": "Importaciones"},
    "malvarado@casamusa.cl":  {"password": "Finanzas2026",      "nombre": "Finanzas"},
    "jsantana@casamusa.cl":   {"password": "Comercial2026",     "nombre": "Comercial", "admin": True},
    "naguilera@casamusa.cl":  {"password": "ECI2026",           "nombre": "ECI", "admin": True},
    "gcarrasco@casamusa.cl":  {"password": "MT2026",            "nombre": "MT", "sucursal": "MT"},
    "sarjona@casamusa.cl":    {"password": "LC2026",            "nombre": "LC", "sucursal": "LC"},
    "evalera@casamusa.cl":    {"password": "MR2026",            "nombre": "MR", "sucursal": "MR"},
    "jvillegas@casamusa.cl":  {"password": "Express2026",       "nombre": "Express", "sucursal": ["CH", "MP"]},
}

# ══════════════════════════════════════════════════════
#  DECORADOR: exige login para ver páginas
# ══════════════════════════════════════════════════════
def login_requerido(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        if "usuario" not in session:
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
        if not session.get("admin"):
            return jsonify({"ok": False, "msg": "No tienes permiso para esta accion."}), 403
        return f(*args, **kwargs)
    return decorado


# ══════════════════════════════════════════════════════
#  Variables disponibles en todos los templates
# ══════════════════════════════════════════════════════
@app.context_processor
def inject_es_admin():
    suc = session.get("sucursal")
    # Si son varias sucursales combinadas (ej. Express = ["CH","MP"]),
    # el badge muestra el nombre del perfil, no la lista cruda.
    suc_label = session.get("nombre") if isinstance(suc, list) else suc
    return {"es_admin": session.get("admin", False), "sucursal_sesion": suc_label}


def _sucursal_forzada():
    """Sucursal a la que esta atado el usuario logueado (Jefe de Sucursal),
    o None si ve todo el dashboard sin restriccion."""
    return session.get("sucursal")


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
    {"slug": "forecast",      "nombre": "Forecast",      "icono": "🔮", "url": "/forecast",      "activo": False},
    {"slug": "tareas",        "nombre": "Tareas Pendientes de Gerencia", "icono": "📋", "url": "/tareas", "activo": False},
]


@app.route("/inicio")
@login_requerido
def inicio():
    return render_template("inicio.html", areas=AREAS, session_nombre=session.get("nombre"))


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
        return jsonify(data_loader_adquisiciones.get_resumen(tipo))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/adquisiciones/por_mes")
@login_requerido
def api_adquisiciones_por_mes():
    try:
        tipo = request.args.get("tipo", "") or None
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


@app.route("/adquisiciones/recepciones")
@login_requerido
def adquisiciones_recepciones():
    return render_template("adquisiciones_recepciones.html",
                           active="adquisiciones_recepciones",
                           session_nombre=session.get("nombre"))


@app.route("/api/adquisiciones/recepciones/resumen")
@login_requerido
def api_adquisiciones_recepciones_resumen():
    try:
        tipo = request.args.get("tipo", "") or None
        return jsonify(data_loader_adquisiciones.get_resumen_recepciones(tipo))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/adquisiciones/recepciones/por_mes")
@login_requerido
def api_adquisiciones_recepciones_por_mes():
    try:
        tipo = request.args.get("tipo", "") or None
        return jsonify(data_loader_adquisiciones.get_recepciones_por_mes(tipo))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        return jsonify(data_loader_adquisiciones.get_cumplimiento_por_proveedor())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/adquisiciones/pendientes")
@login_requerido
def adquisiciones_pendientes():
    return render_template("adquisiciones_pendientes.html",
                           active="adquisiciones_pendientes",
                           session_nombre=session.get("nombre"))


@app.route("/api/adquisiciones/pendientes")
@login_requerido
def api_adquisiciones_pendientes():
    try:
        return jsonify(data_loader_adquisiciones.get_oc_pendientes())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/adquisiciones/conversion_pedido")
@login_requerido
def adquisiciones_conversion_pedido():
    return render_template("adquisiciones_conversion_pedido.html",
                           active="adquisiciones_conversion_pedido",
                           session_nombre=session.get("nombre"))


@app.route("/api/adquisiciones/conversion_pedido")
@login_requerido
def api_adquisiciones_conversion_pedido():
    try:
        return jsonify(data_loader_adquisiciones.get_conversion_pedido_venta())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/adquisiciones/codigos_6")
@login_requerido
def adquisiciones_codigos_6():
    return render_template("adquisiciones_codigos_6.html",
                           active="adquisiciones_codigos_6",
                           session_nombre=session.get("nombre"))


@app.route("/api/adquisiciones/codigos_6")
@login_requerido
def api_adquisiciones_codigos_6():
    try:
        return jsonify(data_loader_adquisiciones.get_codigos_6_sin_venta())
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


@app.route("/inventario/compras")
@login_requerido
def inventario_compras():
    return render_template("inventario_compras.html",
                           active="inventario_compras",
                           session_nombre=session.get("nombre"))


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


@app.route("/api/inventario/plan_compras")
@login_requerido
def api_inventario_plan_compras():
    try:
        familia = request.args.get("familia", "") or None
        meses = request.args.get("meses", type=float)
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_obligatorios_pg.get_plan_compra_reposicion_pg(familia, meses))
        return jsonify(data_loader_obligatorios.get_plan_compra_reposicion(familia, meses))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/plan_compras/resumen_valor")
@login_requerido
def api_inventario_plan_compras_resumen_valor():
    try:
        familia = request.args.get("familia", "") or None
        if USAR_POSTGRES_INVENTARIO:
            return jsonify(data_loader_obligatorios_pg.get_resumen_valor_compra_pg(familia))
        return jsonify(data_loader_obligatorios.get_resumen_valor_compra(familia))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/inventario/plan_compras/exportar")
@login_requerido
def api_inventario_plan_compras_exportar():
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
    return _area_en_construccion("forecast")


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
            return jsonify(data_loader_pg.get_ventas_por_vendedor_pg(filtro_sucursal=_sucursal_forzada()))
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
            return jsonify(data_loader_pg.get_ventas_por_canal_pg(filtro_sucursal=_sucursal_forzada()))
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
            return jsonify(data_loader_pg.get_ventas_por_familia_pg(agrupar_por, filtro_sucursal=_sucursal_forzada()))
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
            return jsonify(data_loader_pg.get_ventas_por_procedencia_pg(filtro_sucursal=_sucursal_forzada()))
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
            return jsonify(data_loader_pg.get_ventas_por_cliente_pg(filtro_sucursal=_sucursal_forzada()))
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
            return jsonify(data_loader_pg.get_ventas_por_producto_pg(filtro_sucursal=_sucursal_forzada()))
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
@admin_requerido
def cargar_ne():
    return render_template("cargar_ne.html",
                           active="cargar_ne",
                           session_nombre=session.get("nombre"))


@app.route("/api/cargar_ne", methods=["GET", "POST"])
@admin_requerido
def api_cargar_ne():
    if not USAR_POSTGRES_COMERCIAL:
        return jsonify({"ok": False, "msg": "Esta funcion requiere Postgres (USAR_POSTGRES_COMERCIAL=1)."}), 400
    try:
        if request.method == "GET":
            return jsonify({"ok": True, "filas": data_loader_pg.get_ne_x_facturar_pg()})

        body = request.get_json(silent=True) or {}
        filas = body.get("filas", [])
        data_loader_pg.guardar_ne_x_facturar_pg(filas, updated_by=session.get("usuario", "admin"))
        return jsonify({"ok": True, "msg": f"Guardado: {len(filas)} filas de NE x Facturar."})
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
#  API — DATOS REALES
# ══════════════════════════════════════════════════════
@app.route("/api/resumen")
@login_requerido
def api_resumen():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_resumen_pg(filtro_sucursal=_sucursal_forzada()))
        return jsonify(data_loader.get_resumen(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ventas_por_mes")
@login_requerido
def api_ventas_por_mes():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_ventas_por_mes_pg(filtro_sucursal=_sucursal_forzada()))
        return jsonify(data_loader.get_ventas_por_mes(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ventas_por_sucursal")
@login_requerido
def api_ventas_por_sucursal():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_ventas_por_sucursal_pg(filtro_sucursal=_sucursal_forzada()))
        return jsonify(data_loader.get_ventas_por_sucursal(filtro_sucursal=_sucursal_forzada()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/filtros_proyeccion")
@login_requerido
def api_filtros_proyeccion():
    try:
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_filtros_proyeccion_pg(filtro_sucursal=_sucursal_forzada()))
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
            return jsonify(data_loader_pg.get_seguimiento_ppto_pg(filtro_sucursal=_sucursal_forzada()))
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
            return jsonify(data_loader_pg.get_filtros_vta_acum_pg(filtro_sucursal=_sucursal_forzada()))
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
        if USAR_POSTGRES_COMERCIAL:
            return jsonify(data_loader_pg.get_vta_mg_mensual_pg(filtros))
        return jsonify(data_loader.get_vta_mg_mensual(filtros))
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
