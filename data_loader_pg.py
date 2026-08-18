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
import math
from datetime import date, datetime, timedelta

import pandas as pd

import db
from data_loader import (
    MESES, ORDEN_SUCURSALES, MG_NE_PCT, var_pct,
    _dias_habiles_mes, _dias_habiles_hasta,
)

# Columnas tabla ventas (Postgres) y su mapeo al DataFrame de
# data_loader (mismo orden) -- usadas tanto por el backfill inicial
# (scripts/backfill_fase1_comercial.py) como por la sincronizacion
# diaria (sincronizar_ventas_pg), para no mantener dos copias.
VENTAS_COLUMNAS = [
    "doc_sap", "folio", "tipo_doc", "fecha_conta", "fecha_doc",
    "codigo_cliente", "nombre_cliente", "procedencia", "sucursal",
    "sucursal_logica", "codigo_cm", "id_procedencia", "codigo_proveedor",
    "descripcion", "marca", "unidad_medida", "familia", "subfamilia",
    "grupo", "cantidad", "costo_cup", "costo_total", "precio_unitario",
    "total", "utilidad_bruta", "mg_bruto", "vendedor", "vendedor_rpt",
    "cond_pago", "empresa", "proveedor_por_defecto", "liquidar",
    "tipo_venta", "estatus_sku", "ano", "mes", "dia", "producto_key",
]

VENTAS_DF_COLS = [
    "DOC_SAP", "FOLIO", "TIPO_DOC", "FECHA_CONTA", "FECHA_DOC",
    "CODIGO_CLIENTE", "NOMBRE_CLIENTE", "PROCEDENCIA", "SUCURSAL",
    "SUCURSAL_LOGICA", "CODIGO_CM", "ID_PROCEDENCIA", "CODIGO_PROVEEDOR",
    "DESCRIPCION", "MARCA", "UNIDAD_MEDIDA", "FAMILIA", "SUBFAMILIA",
    "GRUPO", "CANTIDAD", "COSTO_CUP", "COSTO_TOTAL", "PRECIO_UNITARIO",
    "TOTAL", "UTILIDAD_BRUTA", "MG_BRUTO", "VENDEDOR", "VENDEDOR_RPT",
    "COND_PAGO", "EMPRESA", "PROVEEDOR_POR_DEFECTO", "LIQUIDAR",
    "TIPO_VENTA", "ESTATUS_SKU", "ANO", "MES", "DIA", "PRODUCTO_KEY",
]


def valor_sql(v):
    """None para NaN/NaT de pandas; tipos nativos de Python para todo
    lo demas -- necesario porque psycopg no sabe serializar tipos
    numpy/pandas directamente."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if pd.isna(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.date()
    if hasattr(v, "item"):
        return v.item()
    return v

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
    "PRODUCTO_KEY":    "producto_key",
}

# Mapeo filtros de Proyeccion/Metas -> columna Postgres. Mismas claves
# que _aplicar_filtros_comunes() en data_loader.py.
COLUMNAS_FILTRO = {
    "sucursal":    "sucursal_logica",
    "vendedor":    "vendedor_rpt",
    "tipo_venta":  "tipo_venta",
    "familia":     "familia",
    "marca":       "marca",
    "subfamilia":  "subfamilia",
    "procedencia": "procedencia",
}


def _filtros_comunes_sql(filtros):
    """Equivalente SQL de _aplicar_filtros_comunes(): un fragmento
    'AND col = ANY(%(clave)s)' por cada filtro presente y != 'todas',
    mas el dict de params correspondiente. Acepta valor string (una
    sucursal) o lista (perfil combinado, ej. Express=[CH,MP]) -- igual
    que _col_coincide, se envuelve el escalar en una lista de 1 para
    poder usar siempre ANY()."""
    f = filtros or {}
    frag = ""
    params = {}
    for clave, columna in COLUMNAS_FILTRO.items():
        valor = f.get(clave, "todas")
        if valor and valor != "todas":
            valores = list(valor) if isinstance(valor, (list, tuple, set)) else [valor]
            frag += f" AND {columna} = ANY(%({clave})s)"
            params[clave] = valores
    return frag, params


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


def get_ventas_por_vendedor_pg(filtro_sucursal=None):
    # Se agrupa por VENDEDOR (nombre real SAP), no VENDEDOR_RPT -- ver
    # docstring de data_loader.get_ventas_por_vendedor.
    return get_ventas_por_campo_pg("VENDEDOR", filtro_sucursal=filtro_sucursal)


def get_ventas_por_canal_pg(filtro_sucursal=None):
    return get_ventas_por_campo_pg("TIPO_VENTA", filtro_sucursal=filtro_sucursal)


def get_ventas_por_procedencia_pg(filtro_sucursal=None):
    return get_ventas_por_campo_pg("PROCEDENCIA", filtro_sucursal=filtro_sucursal)


def get_ventas_por_cliente_pg(filtro_sucursal=None):
    return get_ventas_por_campo_pg("NOMBRE_CLIENTE", top_n=15, filtro_sucursal=filtro_sucursal)


def get_ventas_por_producto_pg(filtro_sucursal=None):
    data = get_ventas_por_campo_pg("PRODUCTO_KEY", top_n=15, filtro_sucursal=filtro_sucursal)
    for r in data["items"]:
        codigo, _, descripcion = r["nombre"].partition("||")
        r["codigo"] = codigo
        r["nombre"] = descripcion
    return data


def get_ventas_por_familia_pg(agrupar_por="familia", filtro_sucursal=None):
    campo = "MARCA" if agrupar_por == "marca" else "FAMILIA"
    top_n = 30 if campo == "MARCA" else None
    return get_ventas_por_campo_pg(campo, top_n=top_n, filtro_sucursal=filtro_sucursal)


def get_filtros_proyeccion_pg(filtro_sucursal=None):
    frag_suc, suc = _filtro_sucursal_sql(filtro_sucursal)
    params = {"suc": suc} if suc else {}
    orden = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT
                      array_agg(DISTINCT sucursal_logica) FILTER (WHERE sucursal_logica IS NOT NULL) AS sucursales,
                      array_agg(DISTINCT vendedor_rpt)     FILTER (WHERE vendedor_rpt IS NOT NULL AND vendedor_rpt != 'OTROS') AS vendedores,
                      array_agg(DISTINCT tipo_venta)       FILTER (WHERE tipo_venta IS NOT NULL) AS tipo_venta,
                      array_agg(DISTINCT familia)          FILTER (WHERE familia IS NOT NULL) AS familias,
                      array_agg(DISTINCT marca)            FILTER (WHERE marca IS NOT NULL) AS marcas,
                      array_agg(DISTINCT subfamilia)       FILTER (WHERE subfamilia IS NOT NULL) AS subfamilias,
                      array_agg(DISTINCT procedencia)      FILTER (WHERE procedencia IS NOT NULL) AS procedencias
                    FROM ventas
                    WHERE ano = 2026 {frag_suc}""",
                params,
            )
            r = cur.fetchone()

    return {
        "sucursales":   sorted(r["sucursales"] or [], key=lambda s: orden.get(s, 99)),
        "vendedores":   sorted(r["vendedores"] or []),
        "tipo_venta":   sorted(r["tipo_venta"] or []),
        "familias":     sorted(r["familias"] or []),
        "marcas":       sorted(r["marcas"] or []),
        "subfamilias":  sorted(r["subfamilias"] or []),
        "procedencias": sorted(r["procedencias"] or []),
    }


def _leer_ne_x_facturar_pg(cur):
    cur.execute("SELECT sucursal, vendedor, monto_ne FROM ne_x_facturar")
    return {(f["sucursal"], f["vendedor"]): float(f["monto_ne"]) for f in cur.fetchall()}


def get_proyeccion_pg(filtros=None):
    frag_filtros, params = _filtros_comunes_sql(filtros)

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            fecha_datos = _fecha_datos_pg(cur)
            mes_actual = fecha_datos.month
            dia_actual = fecha_datos.day
            mes_anterior = mes_actual - 1 if mes_actual > 1 else 12
            params.update({"mes_actual": mes_actual, "dia_actual": dia_actual, "mes_anterior": mes_anterior})

            cur.execute(
                f"""SELECT
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS v_ano_26,
                      coalesce(sum(total) FILTER (
                          WHERE ano = 2025 AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                      ), 0) AS v_ano_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS v_mes_26,
                      coalesce(sum(total) FILTER (WHERE ano = 2025 AND mes = %(mes_actual)s AND dia <= %(dia_actual)s), 0) AS v_mes_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_anterior)s AND dia <= %(dia_actual)s), 0) AS v_mes_ant
                    FROM ventas
                    WHERE ano IN (2025, 2026) {frag_filtros}""",
                params,
            )
            r = cur.fetchone()
            v_ano_26  = float(r["v_ano_26"])
            v_ano_25  = float(r["v_ano_25"])
            v_mes_26  = float(r["v_mes_26"])
            v_mes_25  = float(r["v_mes_25"])
            v_mes_ant = float(r["v_mes_ant"])

            dh_total         = _dias_habiles_mes(2026, mes_actual)
            dh_transcurridos = _dias_habiles_hasta(2026, mes_actual, dia_actual)

            inicio_ano = date(2026, 1, 1)
            doy = (fecha_datos - inicio_ano).days + 1
            proyeccion_anual = round(v_ano_26 * 365 / doy, 0) if doy > 0 else 0

            kpis = {
                "v_ano_actual":       round(v_ano_26, 0),
                "v_ano_anterior":     round(v_ano_25, 0),
                "var_ano":            var_pct(v_ano_26, v_ano_25),
                "v_mes_actual":       round(v_mes_26, 0),
                "v_mes_ant_ano":      round(v_mes_25, 0),
                "var_mes_ano":        var_pct(v_mes_26, v_mes_25),
                "v_mes_ant_mes":      round(v_mes_ant, 0),
                "var_mes_mes":        var_pct(v_mes_26, v_mes_ant),
                "proyeccion_anual":   proyeccion_anual,
                "dh_transcurridos":   dh_transcurridos,
                "dh_total":           dh_total,
                "fecha_datos":        fecha_datos.strftime("%d/%m/%Y"),
                "mes_nombre":         MESES.get(mes_actual, ""),
                "mes_anterior_nombre":MESES.get(mes_anterior, ""),
                "ano_actual":         2026,
                "ano_anterior":       2025,
            }

            factor_mes = dh_total / dh_transcurridos if dh_transcurridos > 0 else 1.0

            # Union de filas presentes en mes actual 2026 O en el mismo
            # tramo de dia de 2025 -- equivalente al merge(how="outer")
            # de la version pandas; coalesce cubre el fillna(0).
            cur.execute(
                f"""SELECT sucursal_logica, vendedor_rpt,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS vta_mes,
                      coalesce(sum(utilidad_bruta) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS mg_mes,
                      count(*) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s) AS nro_docs,
                      coalesce(sum(total) FILTER (WHERE ano = 2025 AND mes = %(mes_actual)s AND dia <= %(dia_actual)s), 0) AS vta_ant
                    FROM ventas
                    WHERE (
                      (ano = 2026 AND mes = %(mes_actual)s)
                      OR (ano = 2025 AND mes = %(mes_actual)s AND dia <= %(dia_actual)s)
                    ) {frag_filtros}
                    GROUP BY sucursal_logica, vendedor_rpt""",
                params,
            )
            filas_agg = cur.fetchall()

            ne_montos = _leer_ne_x_facturar_pg(cur)

    filas = []
    for row in filas_agg:
        suc     = row["sucursal_logica"]
        vend    = row["vendedor_rpt"]
        vta     = float(row["vta_mes"])
        mg      = float(row["mg_mes"])
        docs    = int(row["nro_docs"])
        vta_ant = float(row["vta_ant"])
        pct_mg  = round(mg / vta * 100, 1) if vta > 0 else 0.0
        proy_l  = round(vta * factor_mes, 0)
        proy_mg = round(mg  * factor_mes, 0)
        monto_ne = ne_montos.get((suc, vend), 0.0)
        filas.append({
            "sucursal":  suc,
            "vendedor":  vend,
            "nro_docs":  docs,
            "vta_mes":   round(vta, 0),
            "mg_mes":    round(mg,  0),
            "pct_mg":    pct_mg,
            "vta_ant":   round(vta_ant, 0),
            "var":       var_pct(vta, vta_ant),
            "proy_lineal": proy_l,
            "proy_mg":   proy_mg,
            "monto_ne":       round(monto_ne, 0),
            "proy_lineal_ne": round(proy_l + monto_ne, 0),
            "mg_con_ne":      round(proy_mg + monto_ne * MG_NE_PCT, 0),
            "is_otros":  vend == "OTROS",
        })

    total_proy_mes = round(v_mes_26 * factor_mes, 0)
    kpis["proy_lineal"] = total_proy_mes

    orden_suc = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    filas.sort(key=lambda x: (
        orden_suc.get(x["sucursal"], 99),
        1 if x["is_otros"] else 0,
        -x["vta_mes"],
    ))

    return {"kpis": kpis, "filas": filas}


def sincronizar_ventas_pg(df, fechas):
    """Sincroniza en Postgres las filas de `df` que caen en `fechas`:
    delete-by-fecha + insert -- mismo criterio de dedupe por rango de
    fecha que usa el cache local (actualizar_desde_archivo_mensual) y
    el backfill inicial (scripts/backfill_fase1_comercial.py).

    `df` debe traer SUCURSAL_LOGICA/VENDEDOR_RPT ya aplicado (ver el
    callback on_nuevo en data_loader.actualizar_desde_archivo_mensual,
    que relee via get_df_2026() antes de llamar esto)."""
    fechas = list(fechas)
    if not fechas:
        return

    cols_sql = ", ".join(VENTAS_COLUMNAS)
    placeholders = ", ".join(["%s"] * len(VENTAS_COLUMNAS))
    sql = f"insert into ventas ({cols_sql}) values ({placeholders})"
    filas = [tuple(valor_sql(getattr(r, c)) for c in VENTAS_DF_COLS) for r in df.itertuples(index=False)]

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from ventas where fecha_conta = ANY(%s)", (fechas,))
            for i in range(0, len(filas), 5000):
                cur.executemany(sql, filas[i:i + 5000])
        conn.commit()


def get_seguimiento_metas_pg(filtros=None):
    frag_filtros, params = _filtros_comunes_sql(filtros)

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            fecha_datos = _fecha_datos_pg(cur)
            mes_actual = fecha_datos.month
            dia_actual = fecha_datos.day
            mes_anterior = mes_actual - 1 if mes_actual > 1 else 12
            params.update({"mes_actual": mes_actual, "dia_actual": dia_actual, "mes_anterior": mes_anterior})

            cur.execute(
                f"""SELECT
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS v_ano_26,
                      coalesce(sum(total) FILTER (
                          WHERE ano = 2025 AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                      ), 0) AS v_ano_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS v_mes_26,
                      coalesce(sum(total) FILTER (WHERE ano = 2025 AND mes = %(mes_actual)s AND dia <= %(dia_actual)s), 0) AS v_mes_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_anterior)s AND dia <= %(dia_actual)s), 0) AS v_mes_ant
                    FROM ventas
                    WHERE ano IN (2025, 2026) {frag_filtros}""",
                params,
            )
            r = cur.fetchone()
            v_ano_26  = float(r["v_ano_26"])
            v_ano_25  = float(r["v_ano_25"])
            v_mes_26  = float(r["v_mes_26"])
            v_mes_25  = float(r["v_mes_25"])
            v_mes_ant = float(r["v_mes_ant"])

            dh_total         = _dias_habiles_mes(2026, mes_actual)
            dh_transcurridos = _dias_habiles_hasta(2026, mes_actual, dia_actual)
            factor_dias = dh_transcurridos / dh_total if dh_total > 0 else 0

            kpis = {
                "v_ano_actual":       round(v_ano_26, 0),
                "v_ano_anterior":     round(v_ano_25, 0),
                "var_ano":            var_pct(v_ano_26, v_ano_25),
                "v_mes_actual":       round(v_mes_26, 0),
                "v_mes_ant_ano":      round(v_mes_25, 0),
                "var_mes_ano":        var_pct(v_mes_26, v_mes_25),
                "v_mes_ant_mes":      round(v_mes_ant, 0),
                "var_mes_mes":        var_pct(v_mes_26, v_mes_ant),
                "dh_transcurridos":   dh_transcurridos,
                "dh_total":           dh_total,
                "fecha_datos":        fecha_datos.strftime("%d/%m/%Y"),
                "mes_nombre":         MESES.get(mes_actual, ""),
                "mes_anterior_nombre":MESES.get(mes_anterior, ""),
                "ano_actual":         2026,
                "ano_anterior":       2025,
            }

            # Metas del mes actual -- ojo, igual que la version Excel: solo
            # se filtra por sucursal, NO por los otros 6 filtros de
            # _aplicar_filtros_comunes (asi se ven las metas de todo el
            # equipo de la sucursal aunque se filtre por un vendedor/
            # familia/etc puntual).
            filtro_suc = (filtros or {}).get("sucursal")
            frag_meta_suc = ""
            params_meta = {"mes_actual": mes_actual}
            if filtro_suc and filtro_suc not in ("todas", "todos", ""):
                valores = list(filtro_suc) if isinstance(filtro_suc, (list, tuple, set)) else [filtro_suc]
                frag_meta_suc = " AND sucursal = ANY(%(suc_meta)s)"
                params_meta["suc_meta"] = valores

            cur.execute(
                f"SELECT sucursal, vendedor, meta FROM metas WHERE ano = 2026 AND mes = %(mes_actual)s {frag_meta_suc}",
                params_meta,
            )
            meta_dic = {(f["sucursal"].strip(), f["vendedor"].strip()): float(f["meta"]) for f in cur.fetchall()}

            # Ventas del mes por sucursal+vendedor (ya filtrado por los 7
            # filtros del panel via frag_filtros).
            cur.execute(
                f"""SELECT sucursal_logica, vendedor_rpt, coalesce(sum(total), 0) AS vta
                    FROM ventas
                    WHERE ano = 2026 AND mes = %(mes_actual)s {frag_filtros}
                    GROUP BY sucursal_logica, vendedor_rpt""",
                params,
            )
            grp = {(f["sucursal_logica"], f["vendedor_rpt"]): float(f["vta"]) for f in cur.fetchall()}

    # Incluir vendedores con meta aunque no tengan venta
    claves = set(grp.keys()) | set(meta_dic.keys())

    filas = {}
    for (suc, vend) in claves:
        vta  = grp.get((suc, vend), 0.0)
        meta = meta_dic.get((suc, vend), 0.0)
        meta_acum  = round(meta * factor_dias, 0) if meta > 0 else 0
        pct_cumpl  = round((1 - vta / meta_acum) * 100, 1) if meta_acum > 0 else None
        pct_global = round((1 - vta / meta)       * 100, 1) if meta > 0       else None
        filas[(suc, vend)] = {
            "sucursal":  suc,
            "vendedor":  vend,
            "vta_mes":   round(vta, 0),
            "meta_acum": meta_acum,
            "pct_cumpl": pct_cumpl,
            "meta":      round(meta, 0),
            "pct_global":pct_global,
            "is_otros":  vend == "OTROS",
        }

    orden_suc = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    lista = list(filas.values())
    lista.sort(key=lambda x: (
        orden_suc.get(x["sucursal"], 99),
        1 if x["is_otros"] else 0,
        -(x["meta"] or 0),
    ))

    return {"kpis": kpis, "filas": lista}


def get_seguimiento_ppto_pg(filtro_sucursal=None):
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
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS v_ano_26,
                      coalesce(sum(total) FILTER (
                          WHERE ano = 2025 AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                      ), 0) AS v_ano_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS v_mes_26,
                      coalesce(sum(total) FILTER (WHERE ano = 2025 AND mes = %(mes_actual)s AND dia <= %(dia_actual)s), 0) AS v_mes_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_anterior)s AND dia <= %(dia_actual)s), 0) AS v_mes_ant
                    FROM ventas
                    WHERE ano IN (2025, 2026) {frag_suc}""",
                params,
            )
            r = cur.fetchone()
            v_ano_26  = float(r["v_ano_26"])
            v_ano_25  = float(r["v_ano_25"])
            v_mes_26  = float(r["v_mes_26"])
            v_mes_25  = float(r["v_mes_25"])
            v_mes_ant = float(r["v_mes_ant"])

            inicio_ano   = date(2026, 1, 1)
            doy          = (fecha_datos - inicio_ano).days + 1
            factor_anual = doy / 365.0

            kpis = {
                "v_ano_actual":        round(v_ano_26, 0),
                "v_ano_anterior":      round(v_ano_25, 0),
                "var_ano":             var_pct(v_ano_26, v_ano_25),
                "v_mes_actual":        round(v_mes_26, 0),
                "v_mes_ant_ano":       round(v_mes_25, 0),
                "var_mes_ano":         var_pct(v_mes_26, v_mes_25),
                "v_mes_ant_mes":       round(v_mes_ant, 0),
                "var_mes_mes":         var_pct(v_mes_26, v_mes_ant),
                "dh_transcurridos":    _dias_habiles_hasta(2026, mes_actual, dia_actual),
                "dh_total":            _dias_habiles_mes(2026, mes_actual),
                "fecha_datos":         fecha_datos.strftime("%d/%m/%Y"),
                "mes_nombre":          MESES.get(mes_actual, ""),
                "mes_anterior_nombre": MESES.get(mes_anterior, ""),
                "ano_actual":          2026,
                "ano_anterior":        2025,
            }

            cur.execute("SELECT sucursal, presupuesto_anual FROM presupuesto WHERE ano = 2026")
            ppto_dic = {f["sucursal"].strip(): float(f["presupuesto_anual"]) for f in cur.fetchall()}

            # sucursales presentes en 2026 (igual que "unique()" sobre df26) +
            # acumulado 2026 (YTD completo) y 2025 (mismo tramo de dias) --
            # n26 sirve para excluir una sucursal que solo tenga historia en
            # 2025 (no aparece en df26, no deberia listarse).
            cur.execute(
                f"""SELECT sucursal_logica,
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS acum_26,
                      coalesce(sum(total) FILTER (
                          WHERE ano = 2025 AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                      ), 0) AS acum_25,
                      count(*) FILTER (WHERE ano = 2026) AS n26
                    FROM ventas
                    WHERE ano IN (2025, 2026) AND sucursal_logica IS NOT NULL {frag_suc}
                    GROUP BY sucursal_logica""",
                params,
            )
            acumulados = {f["sucursal_logica"]: f for f in cur.fetchall()}

            cur.execute(
                f"""SELECT sucursal_logica, ano, mes, coalesce(sum(total), 0) AS v
                    FROM ventas
                    WHERE ano IN (2025, 2026) AND sucursal_logica IS NOT NULL {frag_suc}
                    GROUP BY sucursal_logica, ano, mes""",
                params,
            )
            grid = {(f["sucursal_logica"], f["ano"], f["mes"]): float(f["v"]) for f in cur.fetchall()}

            cur.execute(
                f"""SELECT ano, mes, coalesce(sum(total), 0) AS v
                    FROM ventas
                    WHERE ano IN (2025, 2026) {frag_suc}
                    GROUP BY ano, mes""",
                params,
            )
            totales_grid = {(f["ano"], f["mes"]): float(f["v"]) for f in cur.fetchall()}

    orden_suc  = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    sucursales = sorted(
        (s for s, f in acumulados.items() if f["n26"] > 0),
        key=lambda s: orden_suc.get(s, 99),
    )

    tabla_acum = []
    for suc in sucursales:
        f = acumulados[suc]
        acum_26 = float(f["acum_26"])
        acum_25 = float(f["acum_25"])
        ppto_anual = ppto_dic.get(suc, 0.0)
        ppto_ytd   = ppto_anual * factor_anual
        proyeccion = acum_26 / factor_anual if factor_anual > 0 else 0.0
        var_ppto_v = var_pct(acum_26, ppto_ytd) if ppto_ytd > 0 else None
        tabla_acum.append({
            "sucursal":     suc,
            "vta_acum":     round(acum_26,    0),
            "vta_acum_ant": round(acum_25,    0),
            "pct_crec":     var_pct(acum_26, acum_25),
            "ppto_anual":   round(ppto_anual, 0),
            "ppto_ytd":     round(ppto_ytd,   0),
            "var_ppto":     var_ppto_v,
            "proyeccion":   round(proyeccion, 0),
        })

    mensual_25 = {s: [round(grid.get((s, 2025, m), 0.0), 0) for m in range(1, 13)] for s in sucursales}
    mensual_26 = {s: [round(grid.get((s, 2026, m), 0.0), 0) for m in range(1, 13)] for s in sucursales}
    totales_25 = [round(totales_grid.get((2025, m), 0.0), 0) for m in range(1, 13)]
    totales_26 = [round(totales_grid.get((2026, m), 0.0), 0) for m in range(1, 13)]

    if filtro_sucursal:
        claves_ppto = filtro_sucursal if isinstance(filtro_sucursal, (list, tuple, set)) else [filtro_sucursal]
        ppto_anual_total = sum(ppto_dic.get(s, 0.0) for s in claves_ppto)
    else:
        ppto_anual_total = sum(ppto_dic.values())
    ppto_mensual = [round(ppto_anual_total / 12, 0)] * 12

    return {
        "kpis":          kpis,
        "tabla_acum":    tabla_acum,
        "mensual_25":    mensual_25,
        "mensual_26":    mensual_26,
        "totales_25":    totales_25,
        "totales_26":    totales_26,
        "ppto_mensual":  ppto_mensual,
        "sucursales":    sucursales,
        "meses_nombres": list(MESES.values()),
    }


def confirmar_fecha_pg(fecha, updated_by="actualizar_diario"):
    """Equivalente Postgres de escribir data/comercial/fecha_confirmada.txt
    -- upsert en control_datos (area='comercial'). GREATEST() lo hace
    avanzar-solamente/idempotente: llamarlo con una fecha menor a la ya
    guardada no la retrocede, asi que es seguro llamarlo todos los dias
    aunque no haya cambiado nada (ver docstring de
    data_loader.confirmar_dia_sin_ventas)."""
    db.execute(
        """INSERT INTO control_datos (area, fecha_confirmada, updated_by)
           VALUES ('comercial', %(fecha)s, %(by)s)
           ON CONFLICT (area) DO UPDATE SET
             fecha_confirmada = GREATEST(control_datos.fecha_confirmada, excluded.fecha_confirmada),
             updated_at = now(),
             updated_by = excluded.updated_by""",
        {"fecha": fecha, "by": updated_by},
    )
