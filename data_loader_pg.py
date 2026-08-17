"""
Versiones de get_resumen()/get_ventas_por_campo()/get_ventas_por_mes()
de data_loader.py respaldadas por Postgres en vez de los Excel locales.

Mismas firmas y misma forma de retorno que las originales -- el
objetivo es poder llamarlas con los mismos parametros y diffear
resultado contra resultado (ver scripts/validar_fase1_comercial.py)
antes de cortar cualquier ruta de app.py a esta version. No se borra
ni reemplaza nada de data_loader.py todavia.

Cada funcion abre UNA conexion (del pool, ver db.conexion_pool) y hace
como maximo 2 consultas -- los agregados por ventana de fecha se
calculan todos juntos con FILTER (WHERE ...) en un solo GROUP BY/scan,
en vez de una consulta separada por ventana. La primera version hacia
5-6 conexiones nuevas por pantalla (una por consulta) y tardaba
20-30s por el handshake TCP+TLS contra la region de Supabase (Oregon,
con latencia real desde Chile); esta version reduce eso a 1 conexion
y 1-2 round trips.
"""
from datetime import datetime, timedelta

import db
from data_loader import MESES, ORDEN_SUCURSALES, var_pct

# Whitelist de columnas agrupables -- evita interpolar un nombre de
# columna arbitrario en SQL y documenta que "campo" (nombre de columna
# del DataFrame, mayusculas) mapea a la columna real en Postgres.
COLUMNAS_AGRUPABLES = {
    "SUCURSAL_LOGICA": "sucursal_logica",
    "VENDEDOR_RPT":    "vendedor_rpt",
    "VENDEDOR":        "vendedor",
    "TIPO_VENTA":      "tipo_venta",
    "PROCEDENCIA":     "procedencia",
    "FAMILIA":         "familia",
    "MARCA":           "marca",
    "NOMBRE_CLIENTE":  "nombre_cliente",
}


def _hoy():
    return datetime.now().date()


def _fecha_datos_pg(cur):
    """Equivalente Postgres de data_loader._fecha_datos(): en vez de
    leer data/comercial/fecha_confirmada.txt, lee la tabla
    control_datos (fila area='comercial'). Reutiliza el cursor que le
    pasa el caller (misma conexion, no abre una nueva)."""
    hoy = _hoy()
    cur.execute(
        """SELECT
             (SELECT max(fecha_conta) FROM ventas WHERE ano = %(ano)s AND mes = %(mes)s) AS max_fecha,
             (SELECT fecha_confirmada FROM control_datos WHERE area = 'comercial') AS confirmada""",
        {"ano": hoy.year, "mes": hoy.month},
    )
    fila = cur.fetchone()
    max_fecha = fila["max_fecha"]
    fecha_real = max_fecha if max_fecha is not None else hoy - timedelta(days=1)

    confirmada = fila["confirmada"]
    if confirmada and confirmada > fecha_real:
        return confirmada
    return fecha_real


def _filtro_sucursal_sql(filtro_sucursal):
    """Retorna (fragmento_sql, valor_param) para el filtro de sucursal.
    Igual que _col_coincide en data_loader.py: acepta un string o una
    lista/tupla (perfiles combinados, ej. "Express" = ["CH","MP"])."""
    if not filtro_sucursal:
        return "", None
    valores = list(filtro_sucursal) if isinstance(filtro_sucursal, (list, tuple, set)) else [filtro_sucursal]
    return " AND sucursal_logica = ANY(%(suc)s)", valores


def get_resumen_pg(filtro_sucursal=None):
    frag_suc, suc = _filtro_sucursal_sql(filtro_sucursal)
    params = {"suc": suc} if suc else {}

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            fecha_datos = _fecha_datos_pg(cur)
            mes_actual = fecha_datos.month
            dia_actual = fecha_datos.day
            mes_anterior = mes_actual - 1 if mes_actual > 1 else 12
            params.update({"mes_actual": mes_actual, "dia_actual": dia_actual, "mes_anterior": mes_anterior})

            cur.execute(
                f"""SELECT
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS venta_ano_26,
                      coalesce(sum(total) FILTER (
                          WHERE ano = 2025 AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                      ), 0) AS venta_ano_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS venta_mes_26,
                      coalesce(sum(utilidad_bruta) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS utilidad_mes,
                      coalesce(sum(total) FILTER (WHERE ano = 2025 AND mes = %(mes_actual)s AND dia <= %(dia_actual)s), 0) AS venta_mes_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_anterior)s AND dia <= %(dia_actual)s), 0) AS venta_mes_ant
                    FROM ventas
                    WHERE ano IN (2025, 2026) {frag_suc}""",
                params,
            )
            r = cur.fetchone()

    venta_ano_26 = float(r["venta_ano_26"])
    venta_ano_25 = float(r["venta_ano_25"])
    venta_mes_26 = float(r["venta_mes_26"])
    utilidad_mes = float(r["utilidad_mes"])
    venta_mes_25 = float(r["venta_mes_25"])
    venta_mes_ant = float(r["venta_mes_ant"])
    mg_pct = round(utilidad_mes / venta_mes_26 * 100, 1) if venta_mes_26 > 0 else 0.0

    return {
        "v_ano_actual":       round(venta_ano_26, 0),
        "v_ano_anterior":     round(venta_ano_25, 0),
        "var_ano":            var_pct(venta_ano_26, venta_ano_25),
        "v_mes_actual":       round(venta_mes_26, 0),
        "v_mes_ant_ano":      round(venta_mes_25, 0),
        "var_mes_ano":        var_pct(venta_mes_26, venta_mes_25),
        "v_mes_ant_mes":      round(venta_mes_ant, 0),
        "var_mes_mes":        var_pct(venta_mes_26, venta_mes_ant),
        "utilidad_mes":       round(utilidad_mes, 0),
        "mg_pct":             mg_pct,
        "mes_nombre":         MESES.get(mes_actual, ""),
        "mes_anterior_nombre": MESES.get(mes_anterior, ""),
        "ano_actual":         2026,
        "ano_anterior":       2025,
    }


def get_ventas_por_mes_pg(filtro_sucursal=None):
    frag_suc, suc = _filtro_sucursal_sql(filtro_sucursal)
    params = {"suc": suc} if suc else {}

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT mes,
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS v26,
                      coalesce(sum(total) FILTER (WHERE ano = 2025), 0) AS v25
                    FROM ventas
                    WHERE ano IN (2025, 2026) {frag_suc}
                    GROUP BY mes""",
                params,
            )
            filas = cur.fetchall()

    por_mes = {f["mes"]: f for f in filas}
    meses = []
    for mes in range(1, 13):
        f = por_mes.get(mes, {"v26": 0, "v25": 0})
        meses.append({
            "mes":        mes,
            "mes_nombre": MESES.get(mes, ""),
            "actual":     round(float(f["v26"]), 0),
            "anterior":   round(float(f["v25"]), 0),
        })

    return {"meses": meses, "ano_actual": 2026, "ano_anterior": 2025}


def get_ventas_por_campo_pg(campo, orden_map=None, top_n=None, filtro_sucursal=None):
    if campo not in COLUMNAS_AGRUPABLES:
        raise ValueError(f"Columna no permitida para agrupar: {campo}")
    campo_col = COLUMNAS_AGRUPABLES[campo]

    frag_suc, suc = _filtro_sucursal_sql(filtro_sucursal)
    params = {"suc": suc} if suc else {}

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            fecha_datos = _fecha_datos_pg(cur)
            mes_actual = fecha_datos.month
            dia_actual = fecha_datos.day
            mes_anterior = mes_actual - 1 if mes_actual > 1 else 12
            params.update({"mes_actual": mes_actual, "dia_actual": dia_actual, "mes_anterior": mes_anterior})

            # Un solo GROUP BY sobre TODA la tabla (ambos anos, sin
            # restriccion de fecha) enumera de una todos los valores
            # distintos de {campo} que aparecen en cualquiera de los
            # dos anos -- exactamente lo que la version pandas hacia
            # con el union de .unique() de cada dataframe. Cada
            # ventana (ano actual, YTD ano anterior, mes actual, mes
            # anterior) se calcula en la misma pasada con FILTER.
            cur.execute(
                f"""SELECT {campo_col} AS val,
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS v_ano_26,
                      coalesce(sum(total) FILTER (
                          WHERE ano = 2025 AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                      ), 0) AS v_ano_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS v_mes_26,
                      coalesce(sum(utilidad_bruta) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS util_mes,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_anterior)s AND dia <= %(dia_actual)s), 0) AS v_mes_prev
                    FROM ventas
                    WHERE {campo_col} IS NOT NULL {frag_suc}
                    GROUP BY {campo_col}""",
                params,
            )
            filas = cur.fetchall()

    resultado = []
    for f in filas:
        v_ano_26   = float(f["v_ano_26"])
        v_ano_25   = float(f["v_ano_25"])
        v_mes_26   = float(f["v_mes_26"])
        util_mes   = float(f["util_mes"])
        v_mes_prev = float(f["v_mes_prev"])
        mg_mes     = round(util_mes / v_mes_26 * 100, 1) if v_mes_26 > 0 else 0.0

        resultado.append({
            "nombre":         str(f["val"]),
            "v_ano_actual":   round(v_ano_26, 0),
            "v_ano_anterior": round(v_ano_25, 0),
            "var_ano":        var_pct(v_ano_26, v_ano_25),
            "v_mes_actual":   round(v_mes_26, 0),
            "v_mes_anterior": round(v_mes_prev, 0),
            "var_mes":        var_pct(v_mes_26, v_mes_prev),
            "utilidad_mes":   round(util_mes, 0),
            "mg_mes":         mg_mes,
        })

    if orden_map:
        resultado.sort(key=lambda r: orden_map.get(r["nombre"], 99))
    else:
        resultado.sort(key=lambda r: -r["v_mes_actual"])

    t_mes_actual   = sum(r["v_mes_actual"]   for r in resultado)
    t_mes_anterior = sum(r["v_mes_anterior"] for r in resultado)
    t_ano_actual   = sum(r["v_ano_actual"]   for r in resultado)
    t_ano_anterior = sum(r["v_ano_anterior"] for r in resultado)
    t_utilidad_mes = sum(r["utilidad_mes"]   for r in resultado)
    total = {
        "nombre":         "TOTAL GENERAL",
        "v_mes_actual":   t_mes_actual,
        "v_mes_anterior": t_mes_anterior,
        "var_mes":        var_pct(t_mes_actual, t_mes_anterior),
        "v_ano_actual":   t_ano_actual,
        "v_ano_anterior": t_ano_anterior,
        "var_ano":        var_pct(t_ano_actual, t_ano_anterior),
        "utilidad_mes":   t_utilidad_mes,
        "mg_mes":         round(t_utilidad_mes / t_mes_actual * 100, 1) if t_mes_actual > 0 else 0.0,
    }

    if top_n:
        resultado = resultado[:top_n]

    return {
        "items":        resultado,
        "total":        total,
        "ano_actual":         2026,
        "ano_anterior":       2025,
        "mes_nombre":         MESES.get(mes_actual, ""),
        "mes_anterior_nombre": MESES.get(mes_anterior, ""),
    }


def get_ventas_por_sucursal_pg(filtro_sucursal=None):
    orden = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    data = get_ventas_por_campo_pg("SUCURSAL_LOGICA", orden_map=orden, filtro_sucursal=filtro_sucursal)
    return {
        "sucursales":   [{**r, "sucursal": r["nombre"]} for r in data["items"]],
        "total":        data["total"],
        "ano_actual":          data["ano_actual"],
        "ano_anterior":        data["ano_anterior"],
        "mes_nombre":          data["mes_nombre"],
        "mes_anterior_nombre": data["mes_anterior_nombre"],
    }
