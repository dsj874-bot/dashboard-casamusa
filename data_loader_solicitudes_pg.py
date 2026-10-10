"""
Solicitudes de Control de Inventario (ver migrations/017). Etapa 1:
registrar la solicitud, verla y su historial. Aprobaciones, analisis de
salidas por el ECI y dictamen vienen en la etapa 2.
"""
import db

SUCURSALES = {"CH": "Chicureo", "MP": "Maipú", "MT": "Matta", "MR": "Manuel Rodríguez",
              "LC": "Las Condes", "SI": "San Isidro"}
BODEGA_DE = {"CH": "Chicureo", "MP": "Maipú", "MT": "Matta", "MR": "Manuel Rodríguez",
             "LC": "Las Condes", "SI": "San Isidro"}
# sucursal_logica de ventas que cubre cada sucursal fisica (San Isidro = SE + CMD)
SUC_VENTAS = {"CH": ["CH"], "MP": ["MP"], "MT": ["MT"], "MR": ["MR"], "LC": ["LC"], "SI": ["SE", "CMD"]}

# Tipo -> nombre, subtipos y respaldo exigido (acordado con el usuario)
TIPOS = {
    "ENTRADA": {
        "nombre": "Entrada de mercadería",
        "subtipos": ["Recepción sin documento", "Sobrante encontrado", "Traspaso recibido", "Ajuste positivo", "Otro"],
        "foto": True, "explicacion_min": 0, "destino": False, "documento": "N° de guía, OC o traspaso (opcional)",
        "ayuda": "La foto es obligatoria: muestra que el producto está físicamente en la sucursal.",
    },
    "SALIDA": {
        "nombre": "Salida de mercadería",
        "subtipos": ["Faltante", "Traspaso enviado", "Uso interno", "Muestra a cliente", "Ajuste negativo", "Otro"],
        "foto": False, "explicacion_min": 20, "destino": True, "documento": "N° de guía o documento (opcional)",
        "ayuda": "Explica por qué el producto ya no está. La solicitud pasa a análisis de Control de Inventario.",
    },
    "MERMA": {
        "nombre": "Merma",
        "subtipos": ["Producto dañado", "Rotura en bodega", "Daño en transporte", "Vencido o deteriorado", "Otro"],
        "foto": True, "explicacion_min": 10, "destino": False, "documento": None,
        "ayuda": "La foto es obligatoria: muestra cómo quedó el producto.",
    },
    "POSTVENTA": {
        "nombre": "Postventa",
        "subtipos": ["Cambio", "Devolución"],
        "foto": False, "explicacion_min": 10, "destino": False, "documento": "N° de boleta o factura",
        "ayuda": "Busca la boleta o factura para elegir los productos: no se puede devolver más de lo vendido.",
    },
    "GARANTIA": {
        "nombre": "Garantía",
        "subtipos": ["Envío a proveedor", "Envío a servicio técnico", "Retorno de garantía"],
        "foto": False, "explicacion_min": 10, "destino": False, "documento": "N° de venta, OC o serie (opcional)",
        "ayuda": "Describe la falla. La garantía es de funcionamiento: la foto es opcional.",
    },
}
ESTADOS = {"abierta": "Abierta", "en_analisis": "En análisis", "aprobada": "Aprobada", "rechazada": "Rechazada",
           "ejecutada": "Ejecutada", "cerrada": "Cerrada", "anulada": "Anulada"}
MAX_FOTOS = 5
MAX_BYTES_FOTO = 1_500_000


def buscar_productos(q, sucursal=None, limite=20):
    """Por codigo exacto/prefijo o por palabras de la descripcion."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    bodega = BODEGA_DE.get(sucursal)
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            if q.isdigit():
                where, params = "p.codigo::text LIKE %(pref)s", {"pref": q + "%"}
                orden = "CASE WHEN p.codigo::text = %(q)s THEN 0 ELSE 1 END, p.codigo"
                params["q"] = q
            else:
                palabras = [w for w in q.split() if w][:5]
                where = " AND ".join(f"p.descripcion ILIKE %(w{i})s" for i in range(len(palabras)))
                params = {f"w{i}": f"%{w}%" for i, w in enumerate(palabras)}
                orden = "p.descripcion"
            params.update({"bodega": bodega, "lim": limite})
            cur.execute(
                f"""SELECT p.codigo, p.descripcion, p.marca, coalesce(p.cup, 0) AS cup,
                           coalesce(nullif(p.embalaje, 0), 1) AS embalaje,
                           (SELECT s.stock FROM inventario_stock s WHERE s.codigo = p.codigo AND s.bodega = %(bodega)s) AS stock
                      FROM productos p WHERE {where} ORDER BY {orden} LIMIT %(lim)s""",
                params,
            )
            return [{"codigo": r["codigo"], "descripcion": r["descripcion"], "marca": r["marca"],
                     "cup": float(r["cup"]), "embalaje": int(r["embalaje"]),
                     "stock": float(r["stock"]) if r["stock"] is not None else None} for r in cur.fetchall()]


def buscar_documento(numero):
    """Boleta/factura por folio o por N interno SAP: lineas vendidas
    (codigo, cantidad neta) para elegir que vuelve en una postventa."""
    n = (numero or "").strip().lstrip("0")
    if not n.isdigit():
        return None
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT tipo_doc, folio, doc_sap, min(fecha_conta) AS fecha, min(nombre_cliente) AS cliente,
                          min(sucursal_logica) AS sucursal, codigo_cm AS codigo, min(descripcion) AS descripcion,
                          sum(cantidad) AS cantidad, sum(total) AS total
                     FROM v_ventas
                    WHERE folio IN (%(n)s, %(n)s || '.0') OR doc_sap = %(n)s
                    GROUP BY tipo_doc, folio, doc_sap, codigo_cm
                    ORDER BY tipo_doc, folio, sum(total) DESC""",
                {"n": n},
            )
            filas = cur.fetchall()
    if not filas:
        return None
    docs = {}
    for r in filas:
        clave = (r["tipo_doc"], r["folio"], r["doc_sap"])
        d = docs.setdefault(clave, {"tipo_doc": r["tipo_doc"], "folio": (r["folio"] or "").replace(".0", ""),
                                    "doc_sap": r["doc_sap"], "fecha": r["fecha"].isoformat() if r["fecha"] else None,
                                    "cliente": r["cliente"], "sucursal": r["sucursal"], "lineas": []})
        if float(r["cantidad"] or 0) > 0:
            d["lineas"].append({"codigo": r["codigo"], "descripcion": r["descripcion"],
                                "cantidad": float(r["cantidad"]), "total": float(r["total"] or 0)})
    return list(docs.values())


def crear_solicitud(datos, lineas, fotos, usuario, nombre):
    """datos: tipo, subtipo, sucursal, sucursal_destino, explicacion,
    documento, tercero. lineas: [{codigo, cantidad}]. fotos: [(nombre,
    mime, bytes)]. Valida el respaldo exigido por tipo. Devuelve (id, numero)."""
    tipo = datos.get("tipo")
    regla = TIPOS.get(tipo)
    if not regla:
        raise ValueError("Elige el tipo de solicitud.")
    if datos.get("sucursal") not in SUCURSALES:
        raise ValueError("Elige la sucursal.")
    if datos.get("subtipo") not in regla["subtipos"]:
        raise ValueError("Elige el motivo.")
    lineas = [l for l in lineas if l.get("codigo") and float(l.get("cantidad") or 0) > 0]
    if not lineas:
        raise ValueError("Agrega al menos un producto con su cantidad.")
    explicacion = (datos.get("explicacion") or "").strip()
    if len(explicacion) < regla["explicacion_min"]:
        raise ValueError(f"La explicación es obligatoria (mínimo {regla['explicacion_min']} caracteres).")
    if regla["foto"] and not fotos:
        raise ValueError("Para este tipo de solicitud la foto es obligatoria.")
    if len(fotos) > MAX_FOTOS:
        raise ValueError(f"Máximo {MAX_FOTOS} fotos por solicitud.")
    for _, _, b in fotos:
        if len(b) > MAX_BYTES_FOTO:
            raise ValueError("Una de las fotos es demasiado grande; vuelve a tomarla.")
    destino = datos.get("sucursal_destino") or None
    if destino and destino not in SUCURSALES:
        destino = None
    if datos.get("subtipo") == "Traspaso enviado" and not destino:
        raise ValueError("Para un traspaso indica la sucursal de destino.")

    codigos = list({int(l["codigo"]) for l in lineas})
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT codigo, descripcion, coalesce(cup, 0) AS cup FROM productos WHERE codigo = ANY(%s)", (codigos,))
            prod = {r["codigo"]: r for r in cur.fetchall()}
            faltan = [c for c in codigos if c not in prod]
            if faltan:
                raise ValueError(f"Código no encontrado: {faltan[0]}")
            cant_total = sum(float(l["cantidad"]) for l in lineas)
            monto = sum(float(l["cantidad"]) * float(prod[int(l["codigo"])]["cup"]) for l in lineas)
            estado = "en_analisis" if tipo == "SALIDA" else "abierta"
            cur.execute(
                """INSERT INTO solicitud (tipo, subtipo, estado, sucursal, sucursal_destino, solicitante,
                                          solicitante_nombre, explicacion, documento, tercero, cantidad_total, monto_total)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (tipo, datos.get("subtipo"), estado, datos["sucursal"], destino, usuario, nombre, explicacion or None,
                 (datos.get("documento") or "").strip() or None, (datos.get("tercero") or "").strip() or None,
                 cant_total, round(monto, 0)),
            )
            sid = cur.fetchone()["id"]
            cur.executemany(
                "INSERT INTO solicitud_linea (solicitud_id, codigo, descripcion_snap, cantidad, cup_snap) VALUES (%s, %s, %s, %s, %s)",
                [(sid, int(l["codigo"]), prod[int(l["codigo"])]["descripcion"], float(l["cantidad"]),
                  float(prod[int(l["codigo"])]["cup"])) for l in lineas],
            )
            for nom, mime, b in fotos:
                cur.execute(
                    "INSERT INTO solicitud_foto (solicitud_id, nombre, mime, bytes, datos, subido_por) VALUES (%s, %s, %s, %s, %s, %s)",
                    (sid, nom, mime or "image/jpeg", len(b), b, usuario),
                )
            cur.execute(
                "INSERT INTO solicitud_evento (solicitud_id, usuario, estado_desde, estado_hasta, comentario) VALUES (%s, %s, NULL, %s, %s)",
                (sid, usuario, estado, "Solicitud creada" + (" — pasa a análisis de Control de Inventario" if estado == "en_analisis" else "")),
            )
        conn.commit()
    return sid, numero_de(sid)


def numero_de(sid):
    return f"SOL-{sid:05d}"


def listar_solicitudes(usuario, ve_todas, sucursales_usuario=None, estado=None, tipo=None, sucursal=None, limite=300):
    """Las del usuario (y las de su sucursal si es jefe); todas si ve_todas."""
    where, params = ["true"], {"lim": limite}
    if not ve_todas:
        if sucursales_usuario:
            where.append("(s.solicitante = %(u)s OR s.sucursal = ANY(%(sucs)s))")
            params["sucs"] = list(sucursales_usuario)
        else:
            where.append("s.solicitante = %(u)s")
        params["u"] = usuario
    if estado:
        where.append("s.estado = ANY(%(estado)s)")
        params["estado"] = estado
    if tipo:
        where.append("s.tipo = ANY(%(tipo)s)")
        params["tipo"] = tipo
    if sucursal:
        where.append("s.sucursal = ANY(%(suc)s)")
        params["suc"] = sucursal
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT s.*, (SELECT count(*) FROM solicitud_linea l WHERE l.solicitud_id = s.id) AS n_lineas,
                           (SELECT count(*) FROM solicitud_foto f WHERE f.solicitud_id = s.id) AS n_fotos,
                           (SELECT l.descripcion_snap FROM solicitud_linea l WHERE l.solicitud_id = s.id
                             ORDER BY l.cantidad * l.cup_snap DESC LIMIT 1) AS producto_principal
                      FROM solicitud s WHERE {' AND '.join(where)}
                     ORDER BY s.creado_en DESC LIMIT %(lim)s""",
                params,
            )
            filas = cur.fetchall()
    return [_fila_resumen(r) for r in filas]


def _fila_resumen(r):
    return {
        "id": r["id"], "numero": numero_de(r["id"]), "tipo": r["tipo"],
        "tipo_nombre": TIPOS.get(r["tipo"], {}).get("nombre", r["tipo"]), "subtipo": r["subtipo"],
        "estado": r["estado"], "estado_nombre": ESTADOS.get(r["estado"], r["estado"]),
        "sucursal": r["sucursal"], "sucursal_nombre": SUCURSALES.get(r["sucursal"], r["sucursal"]),
        "sucursal_destino": r["sucursal_destino"], "solicitante": r["solicitante"],
        "solicitante_nombre": r["solicitante_nombre"], "documento": r["documento"], "tercero": r["tercero"],
        "cantidad_total": float(r["cantidad_total"]), "monto_total": float(r["monto_total"]),
        "creado_en": r["creado_en"].isoformat(), "n_lineas": r.get("n_lineas"), "n_fotos": r.get("n_fotos"),
        "producto_principal": r.get("producto_principal"),
    }


def get_solicitud(sid):
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM solicitud WHERE id = %s", (sid,))
            s = cur.fetchone()
            if not s:
                return None
            cur.execute("SELECT codigo, descripcion_snap, cantidad, cup_snap FROM solicitud_linea WHERE solicitud_id = %s ORDER BY id", (sid,))
            lineas = [{"codigo": r["codigo"], "descripcion": r["descripcion_snap"], "cantidad": float(r["cantidad"]),
                       "cup": float(r["cup_snap"]), "monto": round(float(r["cantidad"]) * float(r["cup_snap"]), 0)}
                      for r in cur.fetchall()]
            cur.execute("SELECT id, nombre, bytes, subido_en FROM solicitud_foto WHERE solicitud_id = %s ORDER BY id", (sid,))
            fotos = [{"id": r["id"], "nombre": r["nombre"], "bytes": r["bytes"]} for r in cur.fetchall()]
            cur.execute("SELECT ts, usuario, estado_desde, estado_hasta, comentario FROM solicitud_evento WHERE solicitud_id = %s ORDER BY ts", (sid,))
            eventos = [{"ts": r["ts"].isoformat(), "usuario": r["usuario"], "estado": ESTADOS.get(r["estado_hasta"], r["estado_hasta"]),
                        "comentario": r["comentario"]} for r in cur.fetchall()]
    d = _fila_resumen(s)
    d.update({"explicacion": s["explicacion"], "lineas": lineas, "fotos": fotos, "eventos": eventos,
              "sucursal_destino_nombre": SUCURSALES.get(s["sucursal_destino"]) if s["sucursal_destino"] else None})
    return d


def get_foto(foto_id):
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT f.mime, f.datos, s.solicitante, s.sucursal FROM solicitud_foto f
                             JOIN solicitud s ON s.id = f.solicitud_id WHERE f.id = %s""", (foto_id,))
            return cur.fetchone()
