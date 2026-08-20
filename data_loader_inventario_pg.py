"""
Equivalente Postgres de data_loader_inventario.py (Fase 2, Inventario).
Mismo patron que data_loader_pg.py: misma firma y forma de retorno que
cada funcion original, para poder diffear resultado contra resultado
(ver scripts/validar_inventario.py) antes de cortar app.py a esta
version. No se borra ni reemplaza nada de data_loader_inventario.py.

Reusa las constantes puras (BODEGAS, VENTA_MENSUAL_COL, IDS_IMPORTADO,
ORDEN_CLASIFICACION) directamente desde data_loader_inventario -- son
solo Python, no dependen de leer ningun Excel.
"""
import db
import data_loader_inventario as dli


def _stock_por_bodega_pg(cur):
    """Stock/transito/valor agregado por bodega (excluye la fila
    sintetica 'Todas', que no es una bodega fisica) -- ~12 filas, se
    itera en Python igual que ya hace data_loader_inventario.py (no son
    miles de valores, no hace falta resolver todo en SQL)."""
    cur.execute(
        """SELECT s.bodega,
             coalesce(sum(s.stock), 0) AS stock_qty,
             coalesce(sum(s.transito), 0) AS transito_qty,
             coalesce(sum(s.stock * p.cup), 0) AS valor_stock,
             coalesce(sum(s.transito * p.cup), 0) AS valor_transito,
             count(*) FILTER (WHERE s.stock > 0) AS skus_con_stock
           FROM inventario_stock s
           JOIN productos p ON p.codigo = s.codigo
           WHERE s.bodega != 'Todas'
           GROUP BY s.bodega"""
    )
    return {r["bodega"]: r for r in cur.fetchall()}


def _bodegas_activas_pg(por_bodega_raw):
    """Igual que _bodegas_activas(df) en la version Excel: BODEGAS
    filtrado a las que existen y tienen stock > 0."""
    return [
        (nombre, stock_col, transito_col)
        for nombre, stock_col, transito_col in dli.BODEGAS
        if nombre in por_bodega_raw and float(por_bodega_raw[nombre]["stock_qty"]) > 0
    ]


def get_resumen_inventario_pg():
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            por_bodega_raw = _stock_por_bodega_pg(cur)
            cur.execute(
                """SELECT count(*) AS total, count(*) FILTER (WHERE tg > 0) AS con_stock
                   FROM (
                       SELECT s.codigo, sum(coalesce(s.stock,0) + coalesce(s.transito,0)) AS tg
                       FROM inventario_stock s WHERE s.bodega != 'Todas'
                       GROUP BY s.codigo
                   ) t"""
            )
            r = cur.fetchone()

    total_skus = r["total"]
    skus_con_stock = r["con_stock"]

    bodegas_activas = _bodegas_activas_pg(por_bodega_raw)

    valor_stock_total = sum(float(por_bodega_raw[n]["valor_stock"]) for n, _, _ in bodegas_activas)
    valor_transito_total = sum(
        float(por_bodega_raw[n]["valor_transito"]) for n, _, t in bodegas_activas if t
    )
    if "Servicio Técnico" in por_bodega_raw:
        valor_transito_total += float(por_bodega_raw["Servicio Técnico"]["valor_transito"])

    por_bodega = sorted(
        (
            {"bodega": n, "valor_stock": round(float(por_bodega_raw[n]["valor_stock"]), 0)}
            for n, _, _ in bodegas_activas
        ),
        key=lambda r: -r["valor_stock"],
    )

    return {
        "total_skus": total_skus,
        "skus_con_stock": skus_con_stock,
        "skus_sin_stock": total_skus - skus_con_stock,
        "valor_stock_total": round(valor_stock_total, 0),
        "valor_transito_total": round(valor_transito_total, 0),
        "valor_total": round(valor_stock_total + valor_transito_total, 0),
        "por_bodega": por_bodega,
    }


def get_inventario_por_bodega_pg():
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            por_bodega_raw = _stock_por_bodega_pg(cur)
            cur.execute(
                """SELECT count(*) AS con_stock
                   FROM (
                       SELECT s.codigo, sum(coalesce(s.stock,0) + coalesce(s.transito,0)) AS tg
                       FROM inventario_stock s WHERE s.bodega != 'Todas'
                       GROUP BY s.codigo
                   ) t WHERE tg > 0"""
            )
            skus_con_stock_total = cur.fetchone()["con_stock"]

    bodegas_activas = _bodegas_activas_pg(por_bodega_raw)

    filas = []
    for nombre, stock_col, transito_col in bodegas_activas:
        r = por_bodega_raw[nombre]
        stock_qty = float(r["stock_qty"])
        valor_stock = float(r["valor_stock"])
        skus_con_stock = int(r["skus_con_stock"])
        transito_qty = float(r["transito_qty"]) if transito_col else None
        valor_transito = float(r["valor_transito"]) if transito_col else None
        filas.append({
            "bodega": nombre,
            "valor_total": round(valor_stock + (valor_transito or 0), 0),
            "stock_qty": round(stock_qty, 0),
            "valor_stock": round(valor_stock, 0),
            "skus_con_stock": skus_con_stock,
            "transito_qty": round(transito_qty, 0) if transito_qty is not None else None,
            "valor_transito": round(valor_transito, 0) if valor_transito is not None else None,
        })

    if "Servicio Técnico" in por_bodega_raw:
        r = por_bodega_raw["Servicio Técnico"]
        transito_qty = float(r["transito_qty"])
        valor_transito = float(r["valor_transito"])
        filas.append({
            "bodega": "Servicio Técnico (tránsito)",
            "valor_total": round(valor_transito, 0),
            "stock_qty": 0, "valor_stock": 0, "skus_con_stock": 0,
            "transito_qty": round(transito_qty, 0), "valor_transito": round(valor_transito, 0),
        })

    filas.sort(key=lambda r: -r["valor_total"])

    total = {
        "bodega": "TOTAL GENERAL",
        "valor_total": round(sum(f["valor_total"] for f in filas), 0),
        "stock_qty": round(sum(f["stock_qty"] for f in filas), 0),
        "valor_stock": round(sum(f["valor_stock"] for f in filas), 0),
        "skus_con_stock": skus_con_stock_total,
        "transito_qty": round(sum(f["transito_qty"] or 0 for f in filas), 0),
        "valor_transito": round(sum(f["valor_transito"] or 0 for f in filas), 0),
    }

    return {"filas": filas, "total": total}


def _pivot_por_bodega_pg(dim_sql, valores_dim, filtro_sql="", params=None):
    """Equivalente de _pivot_dimension_por_bodega: una dimension (ya
    resuelta como expresion SQL sobre productos p) x bodega."""
    params = dict(params or {})
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            por_bodega_raw = _stock_por_bodega_pg(cur)

            cur.execute(
                f"""SELECT {dim_sql} AS clase, count(*) AS skus
                    FROM productos p
                    WHERE p.codigo IN (SELECT DISTINCT codigo FROM inventario_stock WHERE bodega != 'Todas')
                      {filtro_sql}
                    GROUP BY {dim_sql}""",
                params,
            )
            skus_por_clase = {r["clase"]: r["skus"] for r in cur.fetchall()}

            cur.execute(
                f"""SELECT {dim_sql} AS clase, s.bodega,
                      coalesce(sum(s.stock * p.cup), 0) AS valor_stock,
                      coalesce(sum(s.transito * p.cup), 0) AS valor_transito
                    FROM inventario_stock s
                    JOIN productos p ON p.codigo = s.codigo
                    WHERE s.bodega != 'Todas' {filtro_sql}
                    GROUP BY {dim_sql}, s.bodega""",
                params,
            )
            valores_raw = {}
            for r in cur.fetchall():
                valores_raw.setdefault(r["clase"], {})[r["bodega"]] = r

    columnas_bodega = [
        {"bodega": nombre, "stock_col": stock_col, "transito_col": transito_col}
        for nombre, stock_col, transito_col in _bodegas_activas_pg(por_bodega_raw)
    ]
    if "Servicio Técnico" in por_bodega_raw:
        columnas_bodega.append({
            "bodega": "Servicio Técnico (ST-TRANS)",
            "stock_col": None, "transito_col": "TRANSITO SERVICIO TECNICO",
        })

    def _bodega_real(col):
        return "Servicio Técnico" if col["bodega"].startswith("Servicio Técnico") else col["bodega"]

    filas = []
    for valor_dim in valores_dim:
        fila = {"clase": valor_dim, "skus": skus_por_clase.get(valor_dim, 0), "valores": {}}
        for col in columnas_bodega:
            r = valores_raw.get(valor_dim, {}).get(_bodega_real(col))
            valor_stock = float(r["valor_stock"]) if r and col["stock_col"] else None
            valor_transito = float(r["valor_transito"]) if r and col["transito_col"] else None
            fila["valores"][col["bodega"]] = {
                "stock":    round(valor_stock, 0) if valor_stock is not None else None,
                "transito": round(valor_transito, 0) if valor_transito is not None else None,
            }
        fila["total_general"] = round(sum(
            (v["stock"] or 0) + (v["transito"] or 0) for v in fila["valores"].values()
        ), 0)
        filas.append(fila)

    total = {"clase": "TOTAL GENERAL", "skus": sum(skus_por_clase.values()), "valores": {}}
    for col in columnas_bodega:
        total["valores"][col["bodega"]] = {
            "stock":    round(sum((f["valores"][col["bodega"]]["stock"] or 0) for f in filas), 0)
                        if col["stock_col"] else None,
            "transito": round(sum((f["valores"][col["bodega"]]["transito"] or 0) for f in filas), 0)
                        if col["transito_col"] else None,
        }
    total["total_general"] = round(sum(f["total_general"] for f in filas), 0)

    bodegas_meta = [
        {"bodega": c["bodega"], "tiene_stock": bool(c["stock_col"]), "tiene_transito": bool(c["transito_col"])}
        for c in columnas_bodega
    ]

    return {"filas": filas, "total": total, "bodegas": bodegas_meta}


def get_inventario_por_clasificacion_pg():
    dim_sql = "coalesce(p.clas_csd, 'SIN CLASIFICAR')"
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT DISTINCT {dim_sql} AS clase FROM productos p
                    WHERE p.codigo IN (SELECT DISTINCT codigo FROM inventario_stock WHERE bodega != 'Todas')"""
            )
            presentes = {r["clase"] for r in cur.fetchall()}

    clases_presentes = [c for c in dli.ORDEN_CLASIFICACION if c in presentes]
    if "SIN CLASIFICAR" in presentes:
        clases_presentes.append("SIN CLASIFICAR")
    return _pivot_por_bodega_pg(dim_sql, clases_presentes)


def get_inventario_por_procedencia_pg():
    dim_sql = "CASE WHEN p.id_procedencia = ANY(%(ids_importado)s) THEN 'Importado' ELSE 'Nacional' END"
    params = {"ids_importado": list(dli.IDS_IMPORTADO)}
    return _pivot_por_bodega_pg(dim_sql, ["Nacional", "Importado"], params=params)


def get_inventario_por_familia_pg():
    dim_sql = "coalesce(p.familia, 'SIN FAMILIA')"
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT {dim_sql} AS clase,
                      coalesce(sum(s.stock * p.cup), 0) + coalesce(sum(s.transito * p.cup), 0) AS valor
                    FROM inventario_stock s JOIN productos p ON p.codigo = s.codigo
                    WHERE s.bodega != 'Todas'
                    GROUP BY {dim_sql}"""
            )
            filas_valor = cur.fetchall()

    familias_presentes = [r["clase"] for r in sorted(filas_valor, key=lambda r: -float(r["valor"]))]
    return _pivot_por_bodega_pg(dim_sql, familias_presentes)


def get_bodegas_disponibles_pg():
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            por_bodega_raw = _stock_por_bodega_pg(cur)
    activas = [(n, t) for n, s, t in _bodegas_activas_pg(por_bodega_raw)]
    return [{"bodega": "Todas", "tiene_transito": any(t for _, t in activas)}] + [
        {"bodega": nombre, "tiene_transito": bool(t)} for nombre, t in activas
    ]


def get_inventario_por_marca_subfamilia_pg(bodega, procedencia=None):
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            por_bodega_raw = _stock_por_bodega_pg(cur)
            activas = _bodegas_activas_pg(por_bodega_raw)

            filtro_proc = ""
            params = {}
            if procedencia and procedencia not in ("todas", "Todas", ""):
                params["ids_importado"] = list(dli.IDS_IMPORTADO)
                if procedencia == "Importado":
                    filtro_proc = "AND p.id_procedencia = ANY(%(ids_importado)s)"
                else:
                    filtro_proc = "AND (p.id_procedencia IS NULL OR NOT (p.id_procedencia = ANY(%(ids_importado)s)))"

            if bodega == "Todas":
                tiene_transito = any(t for _, _, t in activas)
                params["bodegas"] = [n for n, _, _ in activas]
                cur.execute(
                    f"""SELECT coalesce(p.marca,'SIN MARCA') AS marca,
                          coalesce(p.subfamilia,'SIN SUBFAMILIA') AS subfamilia,
                          coalesce(sum(s.stock * p.cup), 0) AS stock,
                          coalesce(sum(s.transito * p.cup), 0) AS transito,
                          coalesce(sum(s.stock), 0) AS stock_qty,
                          coalesce(sum(s.transito), 0) AS transito_qty
                        FROM inventario_stock s JOIN productos p ON p.codigo = s.codigo
                        WHERE s.bodega = ANY(%(bodegas)s) {filtro_proc}
                        GROUP BY marca, subfamilia""",
                    params,
                )
                agg = {(r["marca"], r["subfamilia"]): dict(r) for r in cur.fetchall()}

                # venta_valor = suma de (venta_mensual * cup) PRODUCTO POR
                # PRODUCTO antes de agrupar -- igual que la version Excel
                # (df["_VENTA_VALOR"] = venta_qty * df["CUP"], calculado por
                # fila). Un CUP promedio del grupo despues de sumar NO es lo
                # mismo si los productos del grupo tienen precios distintos
                # (bug real encontrado: distorsionaba bastante el numero).
                cur.execute(
                    f"""SELECT coalesce(p.marca,'SIN MARCA') AS marca,
                          coalesce(p.subfamilia,'SIN SUBFAMILIA') AS subfamilia,
                          coalesce(sum(s.venta_mensual), 0) AS venta_qty,
                          coalesce(sum(s.venta_mensual * p.cup), 0) AS venta_valor
                        FROM inventario_stock s JOIN productos p ON p.codigo = s.codigo
                        WHERE s.bodega = 'Todas' {filtro_proc}
                        GROUP BY marca, subfamilia""",
                    params,
                )
                venta_por_grupo = {
                    (r["marca"], r["subfamilia"]): (float(r["venta_qty"]), float(r["venta_valor"]))
                    for r in cur.fetchall()
                }

                cur.execute("SELECT EXISTS(SELECT 1 FROM inventario_stock WHERE bodega = 'Todas') AS existe")
                venta_col = cur.fetchone()["existe"]
            else:
                bodega_info = next(((n, s, t) for n, s, t in activas if n == bodega), None)
                if bodega_info is None:
                    raise ValueError(f"Bodega no encontrada o sin stock: {bodega}")
                _, stock_col, transito_col = bodega_info
                tiene_transito = bool(transito_col)
                venta_col = dli.VENTA_MENSUAL_COL.get(bodega) is not None
                params["bodega"] = bodega
                cur.execute(
                    f"""SELECT coalesce(p.marca,'SIN MARCA') AS marca,
                          coalesce(p.subfamilia,'SIN SUBFAMILIA') AS subfamilia,
                          coalesce(sum(s.stock * p.cup), 0) AS stock,
                          coalesce(sum(s.transito * p.cup), 0) AS transito,
                          coalesce(sum(s.stock), 0) AS stock_qty,
                          coalesce(sum(s.transito), 0) AS transito_qty,
                          coalesce(sum(s.venta_mensual), 0) AS venta_qty,
                          coalesce(sum(s.venta_mensual * p.cup), 0) AS venta_valor
                        FROM inventario_stock s JOIN productos p ON p.codigo = s.codigo
                        WHERE s.bodega = %(bodega)s {filtro_proc}
                        GROUP BY marca, subfamilia""",
                    params,
                )
                agg = {(r["marca"], r["subfamilia"]): dict(r) for r in cur.fetchall()}
                venta_por_grupo = {k: (float(v["venta_qty"]), float(v["venta_valor"])) for k, v in agg.items()}

    def fila_de(stock, transito, stock_qty, transito_qty, venta_qty):
        alcance = round(stock_qty / venta_qty, 2) if venta_col and venta_qty > 0 else None
        return {
            "stock":         round(float(stock), 0),
            "transito":      round(float(transito), 0),
            "stock_qty":     round(float(stock_qty), 0),
            "transito_qty":  round(float(transito_qty), 0),
            "venta_mensual": None,
            "venta_qty":     round(float(venta_qty), 0) if venta_col else None,
            "alcance":       alcance,
        }

    # venta_valor ya viene sumado producto-por-producto (venta_mensual x
    # cup de CADA producto, antes de agrupar) desde la consulta SQL --
    # igual que la version Excel.
    #
    # OJO: el total de marca se calcula sobre TODAS sus subfamilias (sin
    # filtrar), igual que la version Excel (fm sale de `grupo` completo,
    # antes del filtro stock==0/transito==0 que solo se aplica a que
    # subfamilias se LISTAN en el detalle) -- una subfamilia ya sin stock
    # pero con venta reciente real debe seguir sumando al total de la
    # marca aunque no aparezca como fila propia (bug real encontrado:
    # calcular el total sumando solo las subfamilias ya filtradas dejaba
    # afuera esa venta).
    por_marca = {}
    for (marca, subfamilia), r in agg.items():
        venta_qty, venta_valor = venta_por_grupo.get((marca, subfamilia), (0.0, 0.0))
        por_marca.setdefault(marca, []).append({
            "subfamilia": subfamilia,
            "stock": float(r["stock"]), "transito": float(r["transito"]),
            "stock_qty": float(r["stock_qty"]), "transito_qty": float(r["transito_qty"]),
            "venta_qty": venta_qty, "venta_valor": venta_valor,
        })

    resultado_marcas = []
    for marca, filas_sub in por_marca.items():
        stock_m = sum(f["stock"] for f in filas_sub)
        transito_m = sum(f["transito"] for f in filas_sub)
        if stock_m == 0 and transito_m == 0:
            continue
        stock_qty_m = sum(f["stock_qty"] for f in filas_sub)
        transito_qty_m = sum(f["transito_qty"] for f in filas_sub)
        venta_qty_m = sum(f["venta_qty"] for f in filas_sub)
        venta_valor_m = sum(f["venta_valor"] for f in filas_sub)
        alcance_m = round(stock_qty_m / venta_qty_m, 2) if venta_col and venta_qty_m > 0 else None

        subfamilias = []
        for f in filas_sub:
            if f["stock"] == 0 and f["transito"] == 0:
                continue
            fs = fila_de(f["stock"], f["transito"], f["stock_qty"], f["transito_qty"], f["venta_qty"])
            fs["venta_mensual"] = round(f["venta_valor"], 0) if venta_col else None
            subfamilias.append({"subfamilia": f["subfamilia"], **fs})
        subfamilias.sort(key=lambda s: -(s["stock"] + s["transito"]))

        resultado_marcas.append({
            "marca": marca,
            "stock": round(stock_m, 0), "transito": round(transito_m, 0),
            "stock_qty": round(stock_qty_m, 0), "transito_qty": round(transito_qty_m, 0),
            "venta_mensual": round(venta_valor_m, 0) if venta_col else None,
            "venta_qty": round(venta_qty_m, 0) if venta_col else None,
            "alcance": alcance_m,
            "subfamilias": subfamilias,
        })
    resultado_marcas.sort(key=lambda m: -(m["stock"] + m["transito"]))

    # Igual que la marca: el total general sale del agregado COMPLETO
    # (todas las combinaciones marca/subfamilia, sin filtrar por stock/
    # transito), no de sumar las marcas ya filtradas -- si una marca
    # entera quedo sin stock/transito pero con venta real, igual debe
    # sumar al total general aunque no aparezca como fila propia.
    tot_stock = sum(float(r["stock"]) for r in agg.values())
    tot_transito = sum(float(r["transito"]) for r in agg.values())
    tot_stock_qty = sum(float(r["stock_qty"]) for r in agg.values())
    tot_transito_qty = sum(float(r["transito_qty"]) for r in agg.values())
    tot_venta_qty = sum(v[0] for v in venta_por_grupo.values())
    tot_venta_valor = sum(v[1] for v in venta_por_grupo.values())
    total = {
        "stock": round(tot_stock, 0), "transito": round(tot_transito, 0),
        "stock_qty": round(tot_stock_qty, 0), "transito_qty": round(tot_transito_qty, 0),
        "venta_mensual": round(tot_venta_valor, 0) if venta_col else None,
        "venta_qty": round(tot_venta_qty, 0) if venta_col else None,
        "alcance": round(tot_stock_qty / tot_venta_qty, 2) if venta_col and tot_venta_qty > 0 else None,
    }

    return {
        "bodega": bodega,
        "tiene_transito": tiene_transito,
        "tiene_venta": venta_col,
        "marcas": resultado_marcas,
        "total": total,
    }


def sincronizar_inventario_pg(df):
    """Reemplaza inventario_stock + productos completos desde un
    dataframe ya leido/mergeado (mismo shape que
    data_loader_inventario._leer_inventario()). Reusa la logica de
    scripts/backfill_inventario.py -- pensado para llamarse desde el
    endpoint de subida web (/api/subir_inventario)."""
    import scripts.backfill_inventario as bi
    bi.cargar_productos(df.copy())
    bi.cargar_stock(df, archivo_origen="subir_inventario_web")
