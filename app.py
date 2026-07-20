from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from functools import wraps
import os
import sys
import data_loader

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = Flask(__name__)
app.secret_key = "casamusa_dashboard_2026_secreto"

# ══════════════════════════════════════════════════════
#  GERENTES AUTORIZADOS
#  Para agregar un gerente: agregar una línea aquí
#  "email": {"password": "clave", "nombre": "Nombre", "admin": True/False}
#  "admin" controla quien ve/puede usar "Actualizar datos". Por
#  defecto (sin la clave, o en False) NO tiene el boton.
# ══════════════════════════════════════════════════════
GERENTES = {
    "dsepulveda@casamusa.cl": {"password": "Admin2026",         "nombre": "Administrador", "admin": True},
    "emusa@casamusa.cl":      {"password": "GGeneral2026",      "nombre": "G. General"},
    "fmusa@casamusa.cl":      {"password": "Importaciones2026", "nombre": "Importaciones"},
    "malvarado@casamusa.cl":  {"password": "Finanzas2026",      "nombre": "Finanzas"},
    "jsantana@casamusa.cl":   {"password": "Comercial2026",     "nombre": "Comercial"},
    "naguilera@casamusa.cl":  {"password": "ECI2026",           "nombre": "ECI"},
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
    return {"es_admin": session.get("admin", False)}


# ══════════════════════════════════════════════════════
#  AUTENTICACIÓN
# ══════════════════════════════════════════════════════
@app.route("/", methods=["GET"])
def index():
    if "usuario" in session:
        return redirect(url_for("resumen"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        gerente  = GERENTES.get(email)
        if gerente and gerente["password"] == password:
            session["usuario"] = email
            session["nombre"]  = gerente["nombre"]
            session["admin"]   = gerente.get("admin", False)
            return redirect(url_for("resumen"))
        error = "Correo o contraseña incorrectos."
    return render_template("login.html", error=error)


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
        return jsonify(data_loader.get_ventas_por_vendedor())
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
        return jsonify(data_loader.get_ventas_por_canal())
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
        return jsonify(data_loader.get_ventas_por_familia(agrupar_por))
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
        return jsonify(data_loader.get_ventas_por_procedencia())
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
        return jsonify(data_loader.get_ventas_por_cliente())
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
        return jsonify(data_loader.get_ventas_por_producto())
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
        resultado = data_loader.actualizar_desde_archivo_mensual()
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Error: {str(e)}"}), 500


# ══════════════════════════════════════════════════════
#  API — DATOS REALES
# ══════════════════════════════════════════════════════
@app.route("/api/resumen")
@login_requerido
def api_resumen():
    try:
        return jsonify(data_loader.get_resumen())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ventas_por_mes")
@login_requerido
def api_ventas_por_mes():
    try:
        return jsonify(data_loader.get_ventas_por_mes())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ventas_por_sucursal")
@login_requerido
def api_ventas_por_sucursal():
    try:
        return jsonify(data_loader.get_ventas_por_sucursal())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/filtros_proyeccion")
@login_requerido
def api_filtros_proyeccion():
    try:
        return jsonify(data_loader.get_filtros_proyeccion())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/proyeccion", methods=["POST"])
@login_requerido
def api_proyeccion():
    try:
        filtros = request.get_json(silent=True) or {}
        return jsonify(data_loader.get_proyeccion(filtros))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/metas", methods=["POST"])
@login_requerido
def api_metas():
    try:
        filtros = request.get_json(silent=True) or {}
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
        return jsonify(data_loader.get_seguimiento_ppto())
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
        return jsonify(data_loader.get_filtros_vta_acum())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/vta_acum", methods=["POST"])
@login_requerido
def api_vta_acum():
    try:
        filtros = request.get_json(silent=True) or {}
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
        return jsonify(data_loader.get_vta_mg_mensual(filtros))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════
#  ARRANQUE
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    print("")
    print("  ╔══════════════════════════════════════╗")
    print("  ║   CASAMUSA — Dashboard de Gerencia   ║")
    print("  ╚══════════════════════════════════════╝")
    print("")
    print("  Abre tu navegador en: http://localhost:5000")
    print("")
    app.run(debug=False, host="0.0.0.0", port=5000)
