"""
Equivalente Postgres de data_loader_adquisiciones.py -- lee de las
tablas compras/recepciones en vez de los Excel locales. A diferencia
de las pantallas originales (que mostraban Comprado y Recibido en
pantallas separadas), estas funciones devuelven AMBOS juntos en cada
resultado -- pedido explicito del usuario: "cada pantalla debe tener
Compras y recepciones".

Igual que en data_loader_pg.py: una conexion por funcion, agregados
por ventana de fecha con FILTER (WHERE ...) en un solo GROUP BY/scan.
"""
import db

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def var_pct(actual, anterior):
    if anterior == 0:
        return 0.0
    return round((float(actual) - float(anterior)) / float(anterior) * 100, 1)


def _filtro_tipo(tipo_compra):
    """Fragmento SQL + parametro para acotar por tipo (STOCK/PEDIDO) --
    compras usa la columna tipo_compra, recepciones usa tipo_oc (mismos
    valores, distinto nombre de columna en cada tabla de origen)."""
    if not tipo_compra:
        return "", "", {}
    return " AND tipo_compra = %(tipo)s", " AND tipo_oc = %(tipo)s", {"tipo": tipo_compra}


def _fecha_datos_pg(cur, frag_c, frag_r, params):
    cur.execute(f"SELECT max(fecha_creacion) AS f FROM compras WHERE ano = 2026 {frag_c}", params)
    fc = cur.fetchone()["f"]
    cur.execute(f"SELECT max(fecha_recepcion) AS f FROM recepciones WHERE ano = 2026 {frag_r}", params)
    fr = cur.fetchone()["f"]
    candidatas = [d for d in (fc, fr) if d is not None]
    return max(candidatas) if candidatas else None


def get_resumen_combinado_pg(tipo_compra=None):
    """KPIs de Adquisiciones -- dos filas (Comprado / Recibido), mismos
    3 grupos de comparacion que el resto del dashboard (Año Actual/
    Anterior, Mes Actual/Año Ant, Mes Actual/Mes Ant)."""
    frag_c, frag_r, params = _filtro_tipo(tipo_compra)

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            fecha_datos = _fecha_datos_pg(cur, frag_c, frag_r, params)
            if fecha_datos is None:
                return {"filas": [], "ano_actual": 2026, "ano_anterior": 2025, "fecha_datos": None}

            mes_actual = fecha_datos.month
            dia_actual = fecha_datos.day
            mes_anterior = mes_actual - 1 if mes_actual > 1 else 12
            params.update({"mes_actual": mes_actual, "dia_actual": dia_actual, "mes_anterior": mes_anterior})

            cur.execute(
                f"""SELECT
                      coalesce(sum(precio_total) FILTER (WHERE ano = 2026), 0) AS ano_26,
                      coalesce(sum(precio_total) FILTER (
                          WHERE ano = 2025 AND (
                              extract(month from fecha_creacion) < %(mes_actual)s OR
                              (extract(month from fecha_creacion) = %(mes_actual)s AND extract(day from fecha_creacion) <= %(dia_actual)s)
                          )
                      ), 0) AS ano_25,
                      coalesce(sum(precio_total) FILTER (WHERE ano = 2026 AND extract(month from fecha_creacion) = %(mes_actual)s), 0) AS mes_26,
                      coalesce(sum(precio_total) FILTER (
                          WHERE ano = 2025 AND extract(month from fecha_creacion) = %(mes_actual)s AND extract(day from fecha_creacion) <= %(dia_actual)s
                      ), 0) AS mes_25,
                      coalesce(sum(precio_total) FILTER (
                          WHERE ano = 2026 AND extract(month from fecha_creacion) = %(mes_anterior)s AND extract(day from fecha_creacion) <= %(dia_actual)s
                      ), 0) AS mes_ant,
                      count(DISTINCT n_orden_compra) FILTER (WHERE ano = 2026) AS oc_26,
                      count(DISTINCT nombre_proveedor) FILTER (WHERE ano = 2026) AS proveedores_26
                    FROM compras WHERE ano IN (2025, 2026) {frag_c}""",
                params,
            )
            rc = cur.fetchone()

            cur.execute(
                f"""SELECT
                      coalesce(sum(total_clp) FILTER (WHERE ano = 2026), 0) AS ano_26,
                      coalesce(sum(total_clp) FILTER (
                          WHERE ano = 2025 AND (
                              extract(month from fecha_recepcion) < %(mes_actual)s OR
                              (extract(month from fecha_recepcion) = %(mes_actual)s AND extract(day from fecha_recepcion) <= %(dia_actual)s)
                          )
                      ), 0) AS ano_25,
                      coalesce(sum(total_clp) FILTER (WHERE ano = 2026 AND extract(month from fecha_recepcion) = %(mes_actual)s), 0) AS mes_26,
                      coalesce(sum(total_clp) FILTER (
                          WHERE ano = 2025 AND extract(month from fecha_recepcion) = %(mes_actual)s AND extract(day from fecha_recepcion) <= %(dia_actual)s
                      ), 0) AS mes_25,
                      coalesce(sum(total_clp) FILTER (
                          WHERE ano = 2026 AND extract(month from fecha_recepcion) = %(mes_anterior)s AND extract(day from fecha_recepcion) <= %(dia_actual)s
                      ), 0) AS mes_ant,
                      count(DISTINCT n_recepcion) FILTER (WHERE ano = 2026) AS rec_26,
                      count(DISTINCT nombre_proveedor) FILTER (WHERE ano = 2026) AS proveedores_26
                    FROM recepciones WHERE ano IN (2025, 2026) {frag_r}""",
                params,
            )
            rr = cur.fetchone()

    def _fila(categoria, r):
        v26, v25, m26, m25, mant = float(r["ano_26"]), float(r["ano_25"]), float(r["mes_26"]), float(r["mes_25"]), float(r["mes_ant"])
        return {
            "categoria":     categoria,
            "v_ano_actual":  round(v26, 0),
            "v_ano_anterior": round(v25, 0),
            "var_ano":       var_pct(v26, v25),
            "v_mes_actual":  round(m26, 0),
            "v_mes_ant_ano": round(m25, 0),
            "var_mes_ano":   var_pct(m26, m25),
            "v_mes_ant_mes": round(mant, 0),
            "var_mes_mes":   var_pct(m26, mant),
        }

    filas = [_fila("Comprado", rc), _fila("Recibido", rr)]

    return {
        "filas":              filas,
        "n_oc_actual":        int(rc["oc_26"]),
        "n_recepciones_actual": int(rr["rec_26"]),
        "proveedores_compra": int(rc["proveedores_26"]),
        "proveedores_recepcion": int(rr["proveedores_26"]),
        "mes_nombre":         MESES.get(mes_actual, ""),
        "mes_anterior_nombre": MESES.get(mes_anterior, ""),
        "ano_actual":         2026,
        "ano_anterior":       2025,
        "fecha_datos":        fecha_datos.strftime("%d-%m-%Y"),
    }


def get_por_mes_combinado_pg(tipo_compra=None):
    """Comprado y Recibido por mes calendario, año actual vs año
    anterior completo -- para el grafico de evolucion mensual."""
    frag_c, frag_r, params = _filtro_tipo(tipo_compra)

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT extract(month from fecha_creacion)::int AS mes, ano, sum(precio_total) AS total
                    FROM compras WHERE ano IN (2025, 2026) {frag_c}
                    GROUP BY mes, ano""",
                params,
            )
            filas_c = {(r["mes"], r["ano"]): float(r["total"]) for r in cur.fetchall()}

            cur.execute(
                f"""SELECT extract(month from fecha_recepcion)::int AS mes, ano, sum(total_clp) AS total
                    FROM recepciones WHERE ano IN (2025, 2026) {frag_r}
                    GROUP BY mes, ano""",
                params,
            )
            filas_r = {(r["mes"], r["ano"]): float(r["total"]) for r in cur.fetchall()}

    meses = []
    for mes in range(1, 13):
        meses.append({
            "mes":               mes,
            "mes_nombre":        MESES.get(mes, ""),
            "comprado_actual":   round(filas_c.get((mes, 2026), 0.0), 0),
            "comprado_anterior": round(filas_c.get((mes, 2025), 0.0), 0),
            "recibido_actual":   round(filas_r.get((mes, 2026), 0.0), 0),
            "recibido_anterior": round(filas_r.get((mes, 2025), 0.0), 0),
        })

    return {"meses": meses, "ano_actual": 2026, "ano_anterior": 2025}


def get_por_proveedor_combinado_pg(tipo_compra=None):
    """Compra y Recepcion por proveedor -- dos filas por proveedor
    (Comprado / Recibido), año actual (YTD) vs mismo periodo año
    anterior, y participacion sobre el total comprado del año actual."""
    frag_c, frag_r, params = _filtro_tipo(tipo_compra)

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            fecha_datos = _fecha_datos_pg(cur, frag_c, frag_r, params)
            if fecha_datos is None:
                return {"proveedores": [], "ano_actual": 2026, "ano_anterior": 2025}
            mes_actual = fecha_datos.month
            dia_actual = fecha_datos.day
            params.update({"mes_actual": mes_actual, "dia_actual": dia_actual})

            cur.execute(
                f"""SELECT nombre_proveedor,
                      coalesce(sum(precio_total) FILTER (WHERE ano = 2026), 0) AS c_26,
                      coalesce(sum(precio_total) FILTER (
                          WHERE ano = 2025 AND (
                              extract(month from fecha_creacion) < %(mes_actual)s OR
                              (extract(month from fecha_creacion) = %(mes_actual)s AND extract(day from fecha_creacion) <= %(dia_actual)s)
                          )
                      ), 0) AS c_25,
                      count(DISTINCT n_orden_compra) FILTER (WHERE ano = 2026) AS n_oc
                    FROM compras
                    WHERE ano IN (2025, 2026) AND nombre_proveedor IS NOT NULL {frag_c}
                    GROUP BY nombre_proveedor""",
                params,
            )
            compra_por_prov = {r["nombre_proveedor"]: r for r in cur.fetchall()}

            cur.execute(
                f"""SELECT nombre_proveedor,
                      coalesce(sum(total_clp) FILTER (WHERE ano = 2026), 0) AS r_26,
                      coalesce(sum(total_clp) FILTER (
                          WHERE ano = 2025 AND (
                              extract(month from fecha_recepcion) < %(mes_actual)s OR
                              (extract(month from fecha_recepcion) = %(mes_actual)s AND extract(day from fecha_recepcion) <= %(dia_actual)s)
                          )
                      ), 0) AS r_25,
                      count(DISTINCT n_recepcion) FILTER (WHERE ano = 2026) AS n_rec
                    FROM recepciones
                    WHERE ano IN (2025, 2026) AND nombre_proveedor IS NOT NULL {frag_r}
                    GROUP BY nombre_proveedor""",
                params,
            )
            recepcion_por_prov = {r["nombre_proveedor"]: r for r in cur.fetchall()}

    total_compra_26 = sum(float(r["c_26"]) for r in compra_por_prov.values())
    nombres = set(compra_por_prov) | set(recepcion_por_prov)

    proveedores = []
    for nombre in nombres:
        rc = compra_por_prov.get(nombre)
        rr = recepcion_por_prov.get(nombre)
        c26 = float(rc["c_26"]) if rc else 0.0
        c25 = float(rc["c_25"]) if rc else 0.0
        r26 = float(rr["r_26"]) if rr else 0.0
        r25 = float(rr["r_25"]) if rr else 0.0
        proveedores.append({
            "nombre":        nombre,
            "n_oc":          int(rc["n_oc"]) if rc else 0,
            "n_recepciones": int(rr["n_rec"]) if rr else 0,
            "participacion": round(c26 / total_compra_26 * 100, 2) if total_compra_26 > 0 else 0.0,
            "filas": [
                {
                    "categoria":      "Comprado",
                    "v_ano_actual":   round(c26, 0),
                    "v_ano_anterior": round(c25, 0),
                    "var_ano":        var_pct(c26, c25),
                },
                {
                    "categoria":      "Recibido",
                    "v_ano_actual":   round(r26, 0),
                    "v_ano_anterior": round(r25, 0),
                    "var_ano":        var_pct(r26, r25),
                },
            ],
        })

    proveedores.sort(key=lambda p: -p["filas"][0]["v_ano_actual"])

    return {
        "proveedores": proveedores,
        "ano_actual":  2026,
        "ano_anterior": 2025,
    }
