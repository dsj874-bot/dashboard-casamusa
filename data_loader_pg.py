"""
Versiones de get_resumen()/get_ventas_por_campo() de data_loader.py
respaldadas por Postgres en vez de los Excel locales.

Mismas firmas y misma forma de retorno que las originales -- el
objetivo es poder llamarlas con los mismos parametros y diffear
resultado contra resultado (ver scripts/validar_fase1_comercial.py)
antes de cortar cualquier ruta de app.py a esta version. No se borra
ni reemplaza nada de data_loader.py todavia.

El fetch a SQL es delgado (GROUP BY hace el trabajo pesado); la forma
de combinar los distintos agregados por valor de "campo" se mantiene
igual a la version pandas original (dict.get(val, 0.0) en vez de un
merge de DataFrames) para minimizar diferencias de comportamiento.
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


def _fecha_datos_pg():
    """Equivalente Postgres de data_loader._fecha_datos(): en vez de
    leer data/comercial/fecha_confirmada.txt, lee la tabla
    control_datos (fila area='comercial')."""
    hoy = _hoy()
    fila = db.query_one(
        "SELECT max(fecha_conta) AS max_fecha FROM ventas WHERE ano = %s AND mes = %s",
        (hoy.year, hoy.month),
    )
    max_fecha = fila["max_fecha"] if fila else None
    fecha_real = max_fecha if max_fecha is not None else hoy - timedelta(days=1)

    fila_conf = db.query_one(
        "SELECT fecha_confirmada FROM control_datos WHERE area = 'comercial'"
    )
    confirmada = fila_conf["fecha_confirmada"] if fila_conf else None
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
    params_base = {"suc": suc} if suc else {}

    fecha_datos = _fecha_datos_pg()
    mes_actual = fecha_datos.month
    dia_actual = fecha_datos.day
    mes_anterior = mes_actual - 1 if mes_actual > 1 else 12

    venta_ano_26 = float(db.query_one(
        f"SELECT coalesce(sum(total), 0) AS s FROM ventas WHERE ano = 2026 {frag_suc}",
        params_base,
    )["s"])

    venta_ano_25 = float(db.query_one(
        f"""SELECT coalesce(sum(total), 0) AS s FROM ventas
            WHERE ano = 2025 {frag_suc}
              AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))""",
        {**params_base, "mes_actual": mes_actual, "dia_actual": dia_actual},
    )["s"])

    fila_mes_26 = db.query_one(
        f"""SELECT coalesce(sum(total), 0) AS venta, coalesce(sum(utilidad_bruta), 0) AS utilidad
            FROM ventas WHERE ano = 2026 AND mes = %(mes_actual)s {frag_suc}""",
        {**params_base, "mes_actual": mes_actual},
    )
    venta_mes_26 = float(fila_mes_26["venta"])
    utilidad_mes = float(fila_mes_26["utilidad"])
    mg_pct = round(utilidad_mes / venta_mes_26 * 100, 1) if venta_mes_26 > 0 else 0.0

    venta_mes_25 = float(db.query_one(
        f"""SELECT coalesce(sum(total), 0) AS s FROM ventas
            WHERE ano = 2025 AND mes = %(mes_actual)s AND dia <= %(dia_actual)s {frag_suc}""",
        {**params_base, "mes_actual": mes_actual, "dia_actual": dia_actual},
    )["s"])

    venta_mes_ant = float(db.query_one(
        f"""SELECT coalesce(sum(total), 0) AS s FROM ventas
            WHERE ano = 2026 AND mes = %(mes_anterior)s AND dia <= %(dia_actual)s {frag_suc}""",
        {**params_base, "mes_anterior": mes_anterior, "dia_actual": dia_actual},
    )["s"])

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


def get_ventas_por_campo_pg(campo, orden_map=None, top_n=None, filtro_sucursal=None):
    if campo not in COLUMNAS_AGRUPABLES:
        raise ValueError(f"Columna no permitida para agrupar: {campo}")
    campo_col = COLUMNAS_AGRUPABLES[campo]

    frag_suc, suc = _filtro_sucursal_sql(filtro_sucursal)
    params_base = {"suc": suc} if suc else {}

    fecha_datos = _fecha_datos_pg()
    mes_actual = fecha_datos.month
    dia_actual = fecha_datos.day
    mes_anterior = mes_actual - 1 if mes_actual > 1 else 12

    g_ano_26 = {
        r["val"]: float(r["s"])
        for r in db.query_all(
            f"""SELECT {campo_col} AS val, sum(total) AS s FROM ventas
                WHERE ano = 2026 AND {campo_col} IS NOT NULL {frag_suc}
                GROUP BY {campo_col}""",
            params_base,
        )
    }

    g_ano_25 = {
        r["val"]: float(r["s"])
        for r in db.query_all(
            f"""SELECT {campo_col} AS val, sum(total) AS s FROM ventas
                WHERE ano = 2025 AND {campo_col} IS NOT NULL {frag_suc}
                  AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                GROUP BY {campo_col}""",
            {**params_base, "mes_actual": mes_actual, "dia_actual": dia_actual},
        )
    }

    g_mes_26, g_util_26 = {}, {}
    for r in db.query_all(
        f"""SELECT {campo_col} AS val, sum(total) AS s, sum(utilidad_bruta) AS u FROM ventas
            WHERE ano = 2026 AND mes = %(mes_actual)s AND {campo_col} IS NOT NULL {frag_suc}
            GROUP BY {campo_col}""",
        {**params_base, "mes_actual": mes_actual},
    ):
        g_mes_26[r["val"]] = float(r["s"])
        g_util_26[r["val"]] = float(r["u"])

    g_mes_prev = {
        r["val"]: float(r["s"])
        for r in db.query_all(
            f"""SELECT {campo_col} AS val, sum(total) AS s FROM ventas
                WHERE ano = 2026 AND mes = %(mes_anterior)s AND dia <= %(dia_actual)s
                  AND {campo_col} IS NOT NULL {frag_suc}
                GROUP BY {campo_col}""",
            {**params_base, "mes_anterior": mes_anterior, "dia_actual": dia_actual},
        )
    }

    valores = {
        r["val"] for r in db.query_all(
            f"SELECT DISTINCT {campo_col} AS val FROM ventas WHERE {campo_col} IS NOT NULL {frag_suc}",
            params_base,
        )
    }

    resultado = []
    for val in valores:
        v_ano_26   = g_ano_26.get(val, 0.0)
        v_ano_25   = g_ano_25.get(val, 0.0)
        v_mes_26   = g_mes_26.get(val, 0.0)
        util_mes   = g_util_26.get(val, 0.0)
        v_mes_prev = g_mes_prev.get(val, 0.0)
        mg_mes     = round(util_mes / v_mes_26 * 100, 1) if v_mes_26 > 0 else 0.0

        resultado.append({
            "nombre":         str(val),
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
