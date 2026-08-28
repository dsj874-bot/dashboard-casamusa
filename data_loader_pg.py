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
    _dias_habiles_mes, _dias_habiles_hasta, CATEGORIAS_VTA,
)

# Mapeo columna Excel (segundo elemento de CATEGORIAS_VTA) -> columna
# Postgres, para poder reusar las mismas claves/labels de CATEGORIAS_VTA
# sin duplicarlas.
_COL_EXCEL_A_PG = {
    "MARCA": "marca", "FAMILIA": "familia", "SUBFAMILIA": "subfamilia",
    "GRUPO": "grupo", "DESCRIPCION": "descripcion", "NOMBRE_CLIENTE": "nombre_cliente",
    "VENDEDOR": "vendedor", "SUCURSAL_LOGICA": "sucursal_logica",
    "TIPO_VENTA": "tipo_venta", "PROCEDENCIA": "procedencia",
    "COND_PAGO": "cond_pago", "PROVEEDOR_POR_DEFECTO": "proveedor_por_defecto",
}

# Columnas de filtro lateral de Vta Acumulada -- mismas claves que
# FILTROS_VTA en data_loader.py.
FILTROS_VTA_COL = {
    "sucursal":    "sucursal_logica",
    "vendedor":    "vendedor",
    "familia":     "familia",
    "marca":       "marca",
    "subfamilia":  "subfamilia",
    "cliente":     "nombre_cliente",
    "descripcion_producto": "descripcion",
    "tipo_venta":  "tipo_venta",
    "procedencia": "procedencia",
    "cond_pago":   "cond_pago",
    "distribuidor": "proveedor_por_defecto",
}


def _filtros_vta_sql(filtros):
    f = filtros or {}
    frag = ""
    params = {}
    for clave, columna in FILTROS_VTA_COL.items():
        valor = f.get(clave)
        if valor and valor not in ("todas", "todos", ""):
            valores = [str(v) for v in valor] if isinstance(valor, (list, tuple, set)) else [str(valor)]
            frag += f" AND {columna} = ANY(%(vta_{clave})s)"
            params[f"vta_{clave}"] = valores
    return frag, params


def _col_grupo_pg(categoria):
    _, col_excel = CATEGORIAS_VTA.get(categoria, ("Marca", "MARCA"))
    return _COL_EXCEL_A_PG.get(col_excel, "marca")

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


def _filtro_canal_sql(filtro_canal):
    """Igual que _filtro_sucursal_sql pero para tipo_venta -- usado
    para forzar el perfil de E-commerce (Elennys Perez) a solo sus
    canales (ver CANALES_ECOMMERCE en app.py)."""
    if not filtro_canal:
        return "", None
    valores = list(filtro_canal) if isinstance(filtro_canal, (list, tuple, set)) else [filtro_canal]
    return " AND tipo_venta = ANY(%(canal)s)", valores


def get_resumen_pg(filtro_sucursal=None, filtro_canal=None):
    frag_suc, suc = _filtro_sucursal_sql(filtro_sucursal)
    frag_canal, canal = _filtro_canal_sql(filtro_canal)
    params = {}
    if suc:
        params["suc"] = suc
    if canal:
        params["canal"] = canal

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
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) {frag_suc} {frag_canal}""",
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


def get_ventas_por_mes_pg(filtro_sucursal=None, filtro_canal=None):
    frag_suc, suc = _filtro_sucursal_sql(filtro_sucursal)
    frag_canal, canal = _filtro_canal_sql(filtro_canal)
    params = {}
    if suc:
        params["suc"] = suc
    if canal:
        params["canal"] = canal

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT mes,
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS v26,
                      coalesce(sum(total) FILTER (WHERE ano = 2025), 0) AS v25
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) {frag_suc} {frag_canal}
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


def get_ventas_por_campo_pg(campo, orden_map=None, top_n=None, filtro_sucursal=None, filtro_canal=None):
    if campo not in COLUMNAS_AGRUPABLES:
        raise ValueError(f"Columna no permitida para agrupar: {campo}")
    campo_col = COLUMNAS_AGRUPABLES[campo]

    frag_suc, suc = _filtro_sucursal_sql(filtro_sucursal)
    frag_canal, canal = _filtro_canal_sql(filtro_canal)
    params = {}
    if suc:
        params["suc"] = suc
    if canal:
        params["canal"] = canal

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
                    FROM v_ventas
                    WHERE {campo_col} IS NOT NULL {frag_suc} {frag_canal}
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


def get_ventas_por_sucursal_pg(filtro_sucursal=None, filtro_canal=None):
    # Sin orden_map -- get_ventas_por_campo_pg ordena por defecto de
    # mayor a menor venta del mes (ver su "else: sort by -v_mes_actual"),
    # que es justo lo que se quiere aca: la sucursal que mas vende
    # arriba, no un orden fijo MT/LC/MR/.../CANAL DIGITAL que deja
    # siempre al final a la que mas vende si no es una sucursal fisica
    # tradicional (pedido explicito del usuario).
    data = get_ventas_por_campo_pg("SUCURSAL_LOGICA", filtro_sucursal=filtro_sucursal, filtro_canal=filtro_canal)
    return {
        "sucursales":   [{**r, "sucursal": r["nombre"]} for r in data["items"]],
        "total":        data["total"],
        "ano_actual":          data["ano_actual"],
        "ano_anterior":        data["ano_anterior"],
        "mes_nombre":          data["mes_nombre"],
        "mes_anterior_nombre": data["mes_anterior_nombre"],
    }


def get_ventas_por_vendedor_pg(filtro_sucursal=None, filtro_canal=None):
    # Se agrupa por VENDEDOR (nombre real SAP), no VENDEDOR_RPT -- ver
    # docstring de data_loader.get_ventas_por_vendedor.
    return get_ventas_por_campo_pg("VENDEDOR", filtro_sucursal=filtro_sucursal, filtro_canal=filtro_canal)


def get_ventas_por_canal_pg(filtro_sucursal=None, filtro_canal=None):
    return get_ventas_por_campo_pg("TIPO_VENTA", filtro_sucursal=filtro_sucursal, filtro_canal=filtro_canal)


def get_ventas_por_procedencia_pg(filtro_sucursal=None, filtro_canal=None):
    return get_ventas_por_campo_pg("PROCEDENCIA", filtro_sucursal=filtro_sucursal, filtro_canal=filtro_canal)


def get_ventas_por_cliente_pg(filtro_sucursal=None, filtro_canal=None):
    return get_ventas_por_campo_pg("NOMBRE_CLIENTE", top_n=15, filtro_sucursal=filtro_sucursal, filtro_canal=filtro_canal)


def get_ventas_por_producto_pg(filtro_sucursal=None, filtro_canal=None):
    data = get_ventas_por_campo_pg("PRODUCTO_KEY", top_n=15, filtro_sucursal=filtro_sucursal, filtro_canal=filtro_canal)
    for r in data["items"]:
        codigo, _, descripcion = r["nombre"].partition("||")
        r["codigo"] = codigo
        r["nombre"] = descripcion
    return data


def get_ventas_por_familia_pg(agrupar_por="familia", filtro_sucursal=None, filtro_canal=None):
    campo = "MARCA" if agrupar_por == "marca" else "FAMILIA"
    top_n = 30 if campo == "MARCA" else None
    return get_ventas_por_campo_pg(campo, top_n=top_n, filtro_sucursal=filtro_sucursal, filtro_canal=filtro_canal)


def get_filtros_proyeccion_pg(filtro_sucursal=None, filtro_canal=None):
    frag_suc, suc = _filtro_sucursal_sql(filtro_sucursal)
    frag_canal, canal = _filtro_canal_sql(filtro_canal)
    params = {}
    if suc:
        params["suc"] = suc
    if canal:
        params["canal"] = canal
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
                    FROM v_ventas
                    WHERE ano = 2026 {frag_suc} {frag_canal}""",
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
                    FROM v_ventas
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
                      count(DISTINCT (doc_sap, folio)) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s) AS nro_docs,
                      coalesce(sum(total) FILTER (WHERE ano = 2025 AND mes = %(mes_actual)s AND dia <= %(dia_actual)s), 0) AS vta_ant
                    FROM v_ventas
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

    # Sucursales ordenadas por venta total del mes (mayor a menor), no
    # por un orden fijo MT/LC/MR/.../CANAL DIGITAL -- ese orden fijo
    # deja siempre al final a la sucursal que mas vende si no es una
    # sucursal fisica tradicional (pedido explicito del usuario, valido
    # para todas las pantallas de reporte por sucursal).
    venta_por_suc = {}
    for x in filas:
        venta_por_suc[x["sucursal"]] = venta_por_suc.get(x["sucursal"], 0.0) + x["vta_mes"]
    orden_suc = {suc: -venta for suc, venta in venta_por_suc.items()}
    filas.sort(key=lambda x: (
        orden_suc.get(x["sucursal"], 0),
        1 if x["is_otros"] else 0,
        -x["vta_mes"],
    ))

    return {"kpis": kpis, "filas": filas}


def sincronizar_ventas_pg(df, fechas, tamano_lote=1500, reintentos=3):
    """Sincroniza en Postgres las filas de `df` que caen en `fechas`:
    delete-by-fecha + insert -- mismo criterio de dedupe por rango de
    fecha que usa el cache local (actualizar_desde_archivo_mensual) y
    el backfill inicial (scripts/backfill_fase1_comercial.py).

    `df` debe traer SUCURSAL_LOGICA/VENDEDOR_RPT ya aplicado (ver el
    callback on_nuevo en data_loader.actualizar_desde_archivo_mensual,
    que relee via get_df_2026() antes de llamar esto).

    Usa COPY (no executemany fila por fila) -- normalmente esto solo
    sincroniza el dia nuevo (pocos cientos de filas, executemany andaba
    bien), pero una resincronizacion de un mes completo (~11000 filas,
    ej. tras sacar la correccion de mal_total 2026-08-28) con
    executemany se cuelga por minutos: cada fila es un round-trip
    cliente-servidor, y la latencia real Chile-Supabase(Oregon) lo hace
    inviable a este volumen (mismo Gotcha que datos_duros_venta_mensual,
    ver Gotcha Postgres #2 en CLAUDE.md).

    tamano_lote mas chico que datos_duros_venta_mensual (1500 vs 20000)
    a proposito: `ventas` tiene 38 columnas (varias de texto largo,
    nombre_cliente/descripcion/etc.) contra las 3 columnas angostas de
    datos_duros_venta_mensual -- medido en la practica: un lote de
    ~11000 filas anchas se tardo tanto que el propio statement_timeout
    de Postgres (2min, aunque en este caso corto a los ~256s) lo
    cancelo a mitad de camino (rendimiento ~20 filas/seg contra las
    ~700-900 filas/seg de las filas angostas). 1500 filas anchas por
    lote se queda con margen debajo de ese limite.

    Commit por lote + reintento con reconexion si un lote falla --
    mismo patron que backfill_ventas() en backfill_fase1_comercial.py.
    Antes esto era un solo commit al final de todo (delete + todos los
    lotes en UNA transaccion larga): si el pooler de Supabase cortaba
    la conexion a mitad de camino (le pasa con transacciones largas,
    ver backfill_ventas), la excepcion sin manejar tumbaba la conexion
    HTTP completa -- el navegador lo ve como "sin conexion", no como
    un error normal con mensaje. Detectado en un intento real de subir
    ventas desde /subir_ventas.

    OJO al depurar esto a mano (matar el proceso Python desde afuera):
    matar el proceso de Windows NO cierra limpiamente la conexion del
    lado de Supabase -- la sesion queda "zombie" (state='active' o
    'idle in transaction (aborted)', bloqueando intentos siguientes).
    Hay que terminarla explicitamente con
    `select pg_terminate_backend(pid)` (ver pg_stat_activity), no basta
    con volver a matar el proceso de SO. Encontrado 2026-08-28."""
    fechas = list(fechas)
    if not fechas:
        return

    cols_sql = ", ".join(VENTAS_COLUMNAS)
    sql = f"copy ventas ({cols_sql}) from stdin"
    filas = [tuple(valor_sql(getattr(r, c)) for c in VENTAS_DF_COLS) for r in df.itertuples(index=False)]

    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("delete from ventas where fecha_conta = ANY(%s)", (fechas,))
    conn.commit()

    try:
        for i in range(0, len(filas), tamano_lote):
            lote = filas[i:i + tamano_lote]
            for intento in range(1, reintentos + 1):
                try:
                    with conn.cursor() as cur:
                        with cur.copy(sql) as copy:
                            for fila in lote:
                                copy.write_row(fila)
                    conn.commit()
                    break
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    if intento == reintentos:
                        raise
                    conn = db.get_connection()
    finally:
        conn.close()


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
                    FROM v_ventas
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
                    FROM v_ventas
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

    # Sucursales ordenadas por venta total del mes (mayor a menor), no
    # por un orden fijo -- mismo criterio que get_proyeccion_pg.
    lista = list(filas.values())
    venta_por_suc = {}
    for x in lista:
        venta_por_suc[x["sucursal"]] = venta_por_suc.get(x["sucursal"], 0.0) + x["vta_mes"]
    orden_suc = {suc: -venta for suc, venta in venta_por_suc.items()}
    lista.sort(key=lambda x: (
        orden_suc.get(x["sucursal"], 0),
        1 if x["is_otros"] else 0,
        -(x["meta"] or 0),
    ))

    return {"kpis": kpis, "filas": lista}


def get_seguimiento_ppto_pg(filtro_sucursal=None, filtro_canal=None):
    frag_suc, suc = _filtro_sucursal_sql(filtro_sucursal)
    frag_canal, canal = _filtro_canal_sql(filtro_canal)
    params = {}
    if suc:
        params["suc"] = suc
    if canal:
        params["canal"] = canal

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
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) {frag_suc} {frag_canal}""",
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
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) AND sucursal_logica IS NOT NULL {frag_suc} {frag_canal}
                    GROUP BY sucursal_logica""",
                params,
            )
            acumulados = {f["sucursal_logica"]: f for f in cur.fetchall()}

            cur.execute(
                f"""SELECT sucursal_logica, ano, mes, coalesce(sum(total), 0) AS v
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) AND sucursal_logica IS NOT NULL {frag_suc} {frag_canal}
                    GROUP BY sucursal_logica, ano, mes""",
                params,
            )
            grid = {(f["sucursal_logica"], f["ano"], f["mes"]): float(f["v"]) for f in cur.fetchall()}

            cur.execute(
                f"""SELECT ano, mes, coalesce(sum(total), 0) AS v
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) {frag_suc} {frag_canal}
                    GROUP BY ano, mes""",
                params,
            )
            totales_grid = {(f["ano"], f["mes"]): float(f["v"]) for f in cur.fetchall()}

    # Ordenadas por venta acumulada (mayor a menor), no por un orden
    # fijo -- mismo criterio que get_proyeccion_pg.
    sucursales = sorted(
        (s for s, f in acumulados.items() if f["n26"] > 0),
        key=lambda s: -float(acumulados[s]["acum_26"]),
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


def get_filtros_vta_acum_pg(filtro_sucursal=None, filtro_canal=None):
    frag_suc, suc = _filtro_sucursal_sql(filtro_sucursal)
    frag_canal, canal = _filtro_canal_sql(filtro_canal)
    params = {}
    if suc:
        params["suc"] = suc
    if canal:
        params["canal"] = canal

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT
                      array_agg(DISTINCT vendedor)       FILTER (WHERE vendedor IS NOT NULL AND trim(vendedor) != '') AS vendedor,
                      array_agg(DISTINCT familia)        FILTER (WHERE familia IS NOT NULL AND trim(familia) != '') AS familia,
                      array_agg(DISTINCT marca)          FILTER (WHERE marca IS NOT NULL AND trim(marca) != '') AS marca,
                      array_agg(DISTINCT subfamilia)     FILTER (WHERE subfamilia IS NOT NULL AND trim(subfamilia) != '') AS subfamilia,
                      array_agg(DISTINCT nombre_cliente) FILTER (WHERE nombre_cliente IS NOT NULL AND trim(nombre_cliente) != '') AS cliente,
                      array_agg(DISTINCT descripcion)    FILTER (WHERE descripcion IS NOT NULL AND trim(descripcion) != '') AS descripcion_producto,
                      array_agg(DISTINCT tipo_venta)     FILTER (WHERE tipo_venta IS NOT NULL AND trim(tipo_venta) != '') AS tipo_venta,
                      array_agg(DISTINCT procedencia)    FILTER (WHERE procedencia IS NOT NULL AND trim(procedencia) != '') AS procedencia,
                      array_agg(DISTINCT cond_pago)      FILTER (WHERE cond_pago IS NOT NULL AND trim(cond_pago) != '') AS cond_pago,
                      array_agg(DISTINCT proveedor_por_defecto) FILTER (WHERE proveedor_por_defecto IS NOT NULL AND trim(proveedor_por_defecto) != '') AS distribuidor,
                      array_agg(DISTINCT sucursal_logica) FILTER (WHERE sucursal_logica IS NOT NULL) AS sucursal
                    FROM v_ventas
                    WHERE ano = 2026 {frag_suc} {frag_canal}""",
                params,
            )
            r = cur.fetchone()

    orden = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    filtros_ok = {
        "vendedor":    sorted(r["vendedor"] or []),
        "familia":     sorted(r["familia"] or []),
        "marca":       sorted(r["marca"] or []),
        "subfamilia":  sorted(r["subfamilia"] or []),
        "cliente":     sorted(r["cliente"] or []),
        "descripcion_producto": sorted(r["descripcion_producto"] or []),
        "tipo_venta":  sorted(r["tipo_venta"] or []),
        "procedencia": sorted(r["procedencia"] or []),
        "cond_pago":   sorted(r["cond_pago"] or []),
        "distribuidor": sorted(r["distribuidor"] or []),
        "sucursal":    sorted(r["sucursal"] or [], key=lambda s: orden.get(s, 99)),
    }
    return {
        "categorias": {k: v[0] for k, v in CATEGORIAS_VTA.items()},
        "filtros":    filtros_ok,
    }


def get_vta_acum_pg(filtros=None):
    f = filtros or {}
    frag_filtros, params = _filtros_vta_sql(f)
    categoria = f.get("categoria", "marca")
    col_grupo = _col_grupo_pg(categoria)

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            fecha_datos = _fecha_datos_pg(cur)
            mes_actual = fecha_datos.month
            dia_actual = fecha_datos.day
            mes_anterior = mes_actual - 1 if mes_actual > 1 else 12
            params.update({"mes_actual": mes_actual, "dia_actual": dia_actual, "mes_anterior": mes_anterior})

            inicio_ano    = date(2026, 1, 1)
            doy           = (fecha_datos - inicio_ano).days + 1
            meses_elapsed = doy * 12 / 365.0

            cur.execute(
                f"""SELECT
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS v_ano_26,
                      coalesce(sum(total) FILTER (
                          WHERE ano = 2025 AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                      ), 0) AS v_ano_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS v_mes_26,
                      coalesce(sum(total) FILTER (WHERE ano = 2025 AND mes = %(mes_actual)s AND dia <= %(dia_actual)s), 0) AS v_mes_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_anterior)s AND dia <= %(dia_actual)s), 0) AS v_mes_ant
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) {frag_filtros}""",
                params,
            )
            r = cur.fetchone()
            v_ano_26  = float(r["v_ano_26"])
            v_ano_25  = float(r["v_ano_25"])
            v_mes_26  = float(r["v_mes_26"])
            v_mes_25  = float(r["v_mes_25"])
            v_mes_ant = float(r["v_mes_ant"])

            kpis = {
                "v_ano_actual":        round(v_ano_26, 0),
                "v_ano_anterior":      round(v_ano_25, 0),
                "var_ano":             var_pct(v_ano_26, v_ano_25),
                "v_mes_actual":        round(v_mes_26, 0),
                "v_mes_ant_ano":       round(v_mes_25, 0),
                "var_mes_ano":         var_pct(v_mes_26, v_mes_25),
                "v_mes_ant_mes":       round(v_mes_ant, 0),
                "var_mes_mes":         var_pct(v_mes_26, v_mes_ant),
                "fecha_datos":         fecha_datos.strftime("%d/%m/%Y"),
                "mes_nombre":          MESES.get(mes_actual, ""),
                "mes_anterior_nombre": MESES.get(mes_anterior, ""),
                "ano_actual":          2026,
                "ano_anterior":        2025,
            }

            # Union de categorias presentes en 2025 O 2026 (n26 sirve para
            # solo LISTAR las que tienen dato en 2026, igual que
            # grp26.sort_values().index en la version pandas; los totales
            # de la fila "Total general" si suman TODAS, 2026-only o no --
            # igual que grp25_ytd.sum()/grp25_full.sum() en la version
            # original, que no estan acotados a las categorias de grp26).
            cur.execute(
                f"""SELECT {col_grupo} AS cat,
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS vta,
                      coalesce(sum(total) FILTER (
                          WHERE ano = 2025 AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                      ), 0) AS vta25_ytd,
                      coalesce(sum(total) FILTER (WHERE ano = 2025), 0) AS vta25_full,
                      count(*) FILTER (WHERE ano = 2026) AS n26
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) AND {col_grupo} IS NOT NULL {frag_filtros}
                    GROUP BY {col_grupo}""",
                params,
            )
            filas_raw = cur.fetchall()

    total_26     = sum(float(fr["vta"])        for fr in filas_raw)
    v25_ytd_tot  = sum(float(fr["vta25_ytd"])  for fr in filas_raw)
    v25_full_tot = sum(float(fr["vta25_full"]) for fr in filas_raw)

    filas_data = sorted((fr for fr in filas_raw if fr["n26"] > 0), key=lambda fr: -float(fr["vta"]))

    filas = []
    for fr in filas_data:
        vta      = float(fr["vta"])
        vta25    = float(fr["vta25_ytd"])
        vta25_fy = float(fr["vta25_full"])
        prom   = round(vta   / meses_elapsed, 0) if meses_elapsed > 0 else 0
        prom25 = round(vta25 / meses_elapsed, 0) if meses_elapsed > 0 else 0
        proy   = round(vta   / meses_elapsed * 12, 0) if meses_elapsed > 0 else 0
        filas.append({
            "categoria":          str(fr["cat"]),
            "vta_acum":           round(vta,     0),
            "vta_acum_ant":       round(vta25,   0),
            "pct_crec":           var_pct(vta, vta25),
            "pct_mkt_share":      round(vta / total_26 * 100, 1) if total_26 > 0 else 0,
            "promedio_venta":     prom,
            "promedio_venta_ant": prom25,
            "proyeccion":         proy,
            "vta_cierre_ant":     round(vta25_fy, 0),
        })

    total = {
        "categoria":          "Total general",
        "vta_acum":           round(total_26,    0),
        "vta_acum_ant":       round(v25_ytd_tot, 0),
        "pct_crec":           var_pct(total_26, v25_ytd_tot),
        "pct_mkt_share":      100.0,
        "promedio_venta":     round(total_26    / meses_elapsed, 0) if meses_elapsed > 0 else 0,
        "promedio_venta_ant": round(v25_ytd_tot / meses_elapsed, 0) if meses_elapsed > 0 else 0,
        "proyeccion":         round(total_26    / meses_elapsed * 12, 0) if meses_elapsed > 0 else 0,
        "vta_cierre_ant":     round(v25_full_tot, 0),
    }

    return {
        "kpis":            kpis,
        "total":           total,
        "filas":           filas,
        "categoria":       categoria,
        "categoria_label": CATEGORIAS_VTA.get(categoria, ("Marca",))[0],
    }


def get_vta_mes_mg_acum_pg(filtros=None):
    f = filtros or {}
    frag_filtros, params = _filtros_vta_sql(f)
    categoria = f.get("categoria", "marca")
    col_grupo = _col_grupo_pg(categoria)

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
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_anterior)s), 0) AS v_mes_ant
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) {frag_filtros}""",
                params,
            )
            r = cur.fetchone()
            v_ano_26  = float(r["v_ano_26"])
            v_ano_25  = float(r["v_ano_25"])
            v_mes_26  = float(r["v_mes_26"])
            v_mes_25  = float(r["v_mes_25"])
            v_mes_ant = float(r["v_mes_ant"])

            kpis = {
                "v_ano_actual":        round(v_ano_26, 0),
                "v_ano_anterior":      round(v_ano_25, 0),
                "var_ano":             var_pct(v_ano_26, v_ano_25),
                "v_mes_actual":        round(v_mes_26, 0),
                "v_mes_ant_ano":       round(v_mes_25, 0),
                "var_mes_ano":         var_pct(v_mes_26, v_mes_25),
                "v_mes_ant_mes":       round(v_mes_ant, 0),
                "var_mes_mes":         var_pct(v_mes_26, v_mes_ant),
                "fecha_datos":         fecha_datos.strftime("%d/%m/%Y"),
                "mes_nombre":          MESES.get(mes_actual, ""),
                "mes_anterior_nombre": MESES.get(mes_anterior, ""),
                "ano_actual":          2026,
                "ano_anterior":        2025,
            }

            cur.execute(f"SELECT DISTINCT mes FROM v_ventas WHERE ano = 2026 {frag_filtros}", params)
            meses_con_datos = sorted(row["mes"] for row in cur.fetchall())
            nombres_meses = [MESES.get(m, str(m)) for m in meses_con_datos]

            cur.execute(
                f"""SELECT mes, coalesce(sum(total), 0) AS v
                    FROM v_ventas WHERE ano = 2026 {frag_filtros} GROUP BY mes""",
                params,
            )
            total_por_mes = {row["mes"]: round(float(row["v"]), 0) for row in cur.fetchall()}

            cur.execute(
                f"""SELECT {col_grupo} AS cat, mes, coalesce(sum(total), 0) AS v
                    FROM v_ventas WHERE ano = 2026 AND {col_grupo} IS NOT NULL {frag_filtros}
                    GROUP BY {col_grupo}, mes""",
                params,
            )
            grp_mes = {(row["cat"], row["mes"]): float(row["v"]) for row in cur.fetchall()}

            cur.execute(
                f"""SELECT {col_grupo} AS cat,
                      coalesce(sum(total), 0) AS vta, coalesce(sum(utilidad_bruta), 0) AS mg
                    FROM v_ventas WHERE ano = 2026 AND {col_grupo} IS NOT NULL {frag_filtros}
                    GROUP BY {col_grupo} ORDER BY vta DESC""",
                params,
            )
            grp_total_rows = cur.fetchall()

            cur.execute(
                f"SELECT coalesce(sum(total), 0) AS t, coalesce(sum(utilidad_bruta), 0) AS m FROM v_ventas WHERE ano = 2026 {frag_filtros}",
                params,
            )
            tot_row = cur.fetchone()
            tot_vta = float(tot_row["t"])
            tot_mg  = float(tot_row["m"])

    filas_mensual = []
    for row in grp_total_rows:
        cat = row["cat"]
        fila = {"categoria": str(cat)}
        for m in meses_con_datos:
            fila[f"m{m}"] = round(grp_mes.get((cat, m), 0.0), 0)
        filas_mensual.append(fila)

    total_mensual = {"categoria": "Total general"}
    for m in meses_con_datos:
        total_mensual[f"m{m}"] = total_por_mes.get(m, 0.0)

    filas_acum = []
    for row in grp_total_rows:
        vta = float(row["vta"])
        mg  = float(row["mg"])
        pct = round(mg / vta * 100, 1) if vta else 0.0
        filas_acum.append({"categoria": str(row["cat"]), "vta_acum": round(vta, 0), "mg_acum": round(mg, 0), "pct_mg": pct})

    total_acum = {
        "categoria": "Total general",
        "vta_acum":  round(tot_vta, 0),
        "mg_acum":   round(tot_mg,  0),
        "pct_mg":    round(tot_mg / tot_vta * 100, 1) if tot_vta else 0.0,
    }

    return {
        "kpis":            kpis,
        "meses":           meses_con_datos,
        "nombres_meses":   nombres_meses,
        "total_mensual":   total_mensual,
        "filas_mensual":   filas_mensual,
        "total_acum":      total_acum,
        "filas_acum":      filas_acum,
        "categoria_label": CATEGORIAS_VTA.get(categoria, ("Marca",))[0],
    }


def get_vta_mg_mensual_pg(filtros=None):
    f = filtros or {}
    frag_filtros, params = _filtros_vta_sql(f)
    categoria = f.get("categoria", "marca")
    col_grupo = _col_grupo_pg(categoria)

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
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_anterior)s), 0) AS v_mes_ant
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) {frag_filtros}""",
                params,
            )
            r = cur.fetchone()
            v_ano_26  = float(r["v_ano_26"])
            v_ano_25  = float(r["v_ano_25"])
            v_mes_26  = float(r["v_mes_26"])
            v_mes_25  = float(r["v_mes_25"])
            v_mes_ant = float(r["v_mes_ant"])

            kpis = {
                "v_ano_actual":        round(v_ano_26, 0),
                "v_ano_anterior":      round(v_ano_25, 0),
                "var_ano":             var_pct(v_ano_26, v_ano_25),
                "v_mes_actual":        round(v_mes_26, 0),
                "v_mes_ant_ano":       round(v_mes_25, 0),
                "var_mes_ano":         var_pct(v_mes_26, v_mes_25),
                "v_mes_ant_mes":       round(v_mes_ant, 0),
                "var_mes_mes":         var_pct(v_mes_26, v_mes_ant),
                "fecha_datos":         fecha_datos.strftime("%d/%m/%Y"),
                "mes_nombre":          MESES.get(mes_actual, ""),
                "mes_anterior_nombre": MESES.get(mes_anterior, ""),
                "ano_actual":          2026,
                "ano_anterior":        2025,
            }

            cur.execute(f"SELECT DISTINCT mes FROM v_ventas WHERE ano = 2026 {frag_filtros}", params)
            meses_con_datos = sorted(row["mes"] for row in cur.fetchall())
            nombres_meses = [MESES.get(m, str(m)) for m in meses_con_datos]

            cur.execute(
                f"""SELECT {col_grupo} AS cat, mes,
                      coalesce(sum(total), 0) AS vta, coalesce(sum(utilidad_bruta), 0) AS mg
                    FROM v_ventas WHERE ano = 2026 AND {col_grupo} IS NOT NULL {frag_filtros}
                    GROUP BY {col_grupo}, mes""",
                params,
            )
            grp_mes = {(row["cat"], row["mes"]): (float(row["vta"]), float(row["mg"])) for row in cur.fetchall()}

            cur.execute(
                f"""SELECT {col_grupo} AS cat,
                      coalesce(sum(total), 0) AS vta, coalesce(sum(utilidad_bruta), 0) AS mg
                    FROM v_ventas WHERE ano = 2026 AND {col_grupo} IS NOT NULL {frag_filtros}
                    GROUP BY {col_grupo} ORDER BY vta DESC""",
                params,
            )
            grp_total_rows = cur.fetchall()

            cur.execute(
                f"""SELECT mes,
                      coalesce(sum(total), 0) AS vta, coalesce(sum(utilidad_bruta), 0) AS mg
                    FROM v_ventas WHERE ano = 2026 {frag_filtros} GROUP BY mes""",
                params,
            )
            tot_mes = {row["mes"]: (float(row["vta"]), float(row["mg"])) for row in cur.fetchall()}

            cur.execute(
                f"SELECT coalesce(sum(total), 0) AS t, coalesce(sum(utilidad_bruta), 0) AS m FROM v_ventas WHERE ano = 2026 {frag_filtros}",
                params,
            )
            tot_row = cur.fetchone()
            tot_vta = float(tot_row["t"])
            tot_mg  = float(tot_row["m"])

    filas_mensual = []
    for row in grp_total_rows:
        cat = row["cat"]
        fila = {"categoria": str(cat)}
        for m in meses_con_datos:
            v, g = grp_mes.get((cat, m), (0.0, 0.0))
            fila[f"vta_{m}"] = round(v, 0)
            fila[f"mg_{m}"]  = round(g, 0)
            fila[f"pct_{m}"] = round(g / v * 100, 1) if v else 0.0
        filas_mensual.append(fila)

    total_mensual = {"categoria": "Total general"}
    for m in meses_con_datos:
        v, g = tot_mes.get(m, (0.0, 0.0))
        total_mensual[f"vta_{m}"] = round(v, 0)
        total_mensual[f"mg_{m}"]  = round(g, 0)
        total_mensual[f"pct_{m}"] = round(g / v * 100, 1) if v else 0.0

    total_acum = {
        "categoria": "Total general",
        "vta_acum":  round(tot_vta, 0),
        "mg_acum":   round(tot_mg,  0),
        "pct_mg":    round(tot_mg / tot_vta * 100, 1) if tot_vta else 0.0,
    }

    filas_acum = []
    for row in grp_total_rows:
        v = float(row["vta"])
        g = float(row["mg"])
        filas_acum.append({
            "categoria": str(row["cat"]),
            "vta_acum":  round(v, 0),
            "mg_acum":   round(g, 0),
            "pct_mg":    round(g / v * 100, 1) if v else 0.0,
        })

    return {
        "kpis":            kpis,
        "meses":           meses_con_datos,
        "nombres_meses":   nombres_meses,
        "total_mensual":   total_mensual,
        "filas_mensual":   filas_mensual,
        "total_acum":      total_acum,
        "filas_acum":      filas_acum,
        "categoria_label": CATEGORIAS_VTA.get(categoria, ("Marca",))[0],
    }


def get_vta_mg_pg(filtros=None):
    """Equivalente Postgres de get_vta_mg() en data_loader.py."""
    f = filtros or {}
    frag_filtros, params = _filtros_vta_sql(f)
    categoria = f.get("categoria", "marca")
    col_grupo = _col_grupo_pg(categoria)

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            fecha_datos = _fecha_datos_pg(cur)
            mes_actual = fecha_datos.month
            dia_actual = fecha_datos.day
            mes_anterior = mes_actual - 1 if mes_actual > 1 else 12
            params.update({"mes_actual": mes_actual, "dia_actual": dia_actual, "mes_anterior": mes_anterior})

            inicio_ano    = date(2026, 1, 1)
            doy           = (fecha_datos - inicio_ano).days + 1
            meses_elapsed = doy * 12 / 365.0

            cur.execute(
                f"""SELECT
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS v_ano_26,
                      coalesce(sum(total) FILTER (
                          WHERE ano = 2025 AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                      ), 0) AS v_ano_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS v_mes_26,
                      coalesce(sum(total) FILTER (WHERE ano = 2025 AND mes = %(mes_actual)s AND dia <= %(dia_actual)s), 0) AS v_mes_25,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_anterior)s AND dia <= %(dia_actual)s), 0) AS v_mes_ant
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) {frag_filtros}""",
                params,
            )
            r = cur.fetchone()
            v_ano_26  = float(r["v_ano_26"])
            v_ano_25  = float(r["v_ano_25"])
            v_mes_26  = float(r["v_mes_26"])
            v_mes_25  = float(r["v_mes_25"])
            v_mes_ant = float(r["v_mes_ant"])

            kpis = {
                "v_ano_actual":        round(v_ano_26, 0),
                "v_ano_anterior":      round(v_ano_25, 0),
                "var_ano":             var_pct(v_ano_26, v_ano_25),
                "v_mes_actual":        round(v_mes_26, 0),
                "v_mes_ant_ano":       round(v_mes_25, 0),
                "var_mes_ano":         var_pct(v_mes_26, v_mes_25),
                "v_mes_ant_mes":       round(v_mes_ant, 0),
                "var_mes_mes":         var_pct(v_mes_26, v_mes_ant),
                "fecha_datos":         fecha_datos.strftime("%d/%m/%Y"),
                "mes_nombre":          MESES.get(mes_actual, ""),
                "mes_anterior_nombre": MESES.get(mes_anterior, ""),
                "ano_actual":          2026,
                "ano_anterior":        2025,
            }

            cur.execute(
                f"""SELECT {col_grupo} AS cat,
                      coalesce(sum(total) FILTER (WHERE ano = 2026), 0) AS vta,
                      coalesce(sum(total) FILTER (
                          WHERE ano = 2025 AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                      ), 0) AS vta25_ytd,
                      coalesce(sum(utilidad_bruta) FILTER (WHERE ano = 2026), 0) AS mg,
                      coalesce(sum(utilidad_bruta) FILTER (
                          WHERE ano = 2025 AND (mes < %(mes_actual)s OR (mes = %(mes_actual)s AND dia <= %(dia_actual)s))
                      ), 0) AS mg25_ytd,
                      coalesce(sum(total) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS vta_mes,
                      coalesce(sum(total) FILTER (WHERE ano = 2025 AND mes = %(mes_actual)s AND dia <= %(dia_actual)s), 0) AS vta_mes_ant,
                      coalesce(sum(utilidad_bruta) FILTER (WHERE ano = 2026 AND mes = %(mes_actual)s), 0) AS mg_mes,
                      count(*) FILTER (WHERE ano = 2026) AS n26
                    FROM v_ventas
                    WHERE ano IN (2025, 2026) AND {col_grupo} IS NOT NULL {frag_filtros}
                    GROUP BY {col_grupo}""",
                params,
            )
            filas_raw = cur.fetchall()

    total_26        = sum(float(fr["vta"])         for fr in filas_raw)
    vta_acum_ant_tot = sum(float(fr["vta25_ytd"])  for fr in filas_raw)
    mg_acum_tot      = sum(float(fr["mg"])         for fr in filas_raw)
    mg_acum_ant_tot  = sum(float(fr["mg25_ytd"])   for fr in filas_raw)
    mg_mes_tot       = sum(float(fr["mg_mes"])     for fr in filas_raw)

    def _fila(cat, vta, vta_ant, mg, mg_ant, v_mes, v_mes_ant, m_mes):
        prom        = round(vta    / meses_elapsed, 0) if meses_elapsed > 0 else 0
        prom_ant    = round(vta_ant/ meses_elapsed, 0) if meses_elapsed > 0 else 0
        mg_prom     = round(mg     / meses_elapsed, 0) if meses_elapsed > 0 else 0
        mg_prom_ant = round(mg_ant / meses_elapsed, 0) if meses_elapsed > 0 else 0
        return {
            "categoria":     str(cat),
            "vta_mes":       round(v_mes, 0),
            "vta_mes_ant":   round(v_mes_ant, 0),
            "vta_acum":      round(vta, 0),
            "vta_acum_ant":  round(vta_ant, 0),
            "pct_crec":      var_pct(vta, vta_ant),
            "pct_mkt_share": round(vta / total_26 * 100, 1) if total_26 > 0 else 0,
            "pct_mg_acum":   round(mg / vta * 100, 1) if vta else 0.0,
            "vta_prom":      prom,
            "vta_prom_ant":  prom_ant,
            "mg_mes":        round(m_mes, 0),
            "pct_mg":        round(m_mes / v_mes * 100, 1) if v_mes else 0.0,
            "mg_acum":       round(mg, 0),
            "mg_prom":       mg_prom,
            "mg_prom_ant":   mg_prom_ant,
            "pct_crec_mg":   var_pct(mg, mg_ant),
        }

    filas_data = sorted((fr for fr in filas_raw if fr["n26"] > 0), key=lambda fr: -float(fr["vta"]))
    filas = [
        _fila(
            fr["cat"], float(fr["vta"]), float(fr["vta25_ytd"]),
            float(fr["mg"]), float(fr["mg25_ytd"]),
            float(fr["vta_mes"]), float(fr["vta_mes_ant"]), float(fr["mg_mes"]),
        )
        for fr in filas_data
    ]

    total = _fila(
        "Total general", total_26, vta_acum_ant_tot,
        mg_acum_tot, mg_acum_ant_tot,
        v_mes_26, v_mes_25, mg_mes_tot,
    )
    total["pct_mkt_share"] = 100.0

    return {
        "kpis":            kpis,
        "total":           total,
        "filas":           filas,
        "categoria":       categoria,
        "categoria_label": CATEGORIAS_VTA.get(categoria, ("Marca",))[0],
    }


def asignar_vendedor_home(vendedor, sucursal, vigente_desde=None, updated_by="admin"):
    """Asigna o cambia la sucursal 'home' de un vendedor en vendedor_home.
    Como sucursal_logica/vendedor_rpt se calculan al consultar (ver
    v_ventas, migrations/003_vendedor_home.sql), esto reacomoda TODA la
    historia de venta del vendedor al instante -- no hace falta
    resincronizar ni una fila de ventas.

    vigente_desde (opcional): fecha desde la cual aplica esta sucursal
    -- uso para traspasos (alguien que vendia por la bodega compartida
    SI-STK sin sucursal propia y luego empezo a vender por el codigo
    propio de otra sucursal); antes de esa fecha, sus ventas por SI-STK
    caen al default en vez de reatribuirse a el."""
    db.execute(
        """INSERT INTO vendedor_home (vendedor, sucursal, vigente_desde, updated_by)
           VALUES (%(vendedor)s, %(sucursal)s, %(desde)s, %(by)s)
           ON CONFLICT (vendedor) DO UPDATE SET
             sucursal = excluded.sucursal,
             vigente_desde = excluded.vigente_desde,
             updated_at = now(),
             updated_by = excluded.updated_by""",
        {"vendedor": vendedor, "sucursal": sucursal, "desde": vigente_desde, "by": updated_by},
    )


def quitar_vendedor_home(vendedor):
    """Saca a un vendedor de vendedor_home -- toda su venta (pasada y
    futura, si sigue apareciendo con ese nombre en el SAP) cae en
    'OTROS' via v_ventas. Mismo efecto que un vendedor que dejo la
    empresa (ver CLAUDE.md, casos EMA SEPULVEDA TUREN / IGOR MOYA)."""
    db.execute("DELETE FROM vendedor_home WHERE vendedor = %s", (vendedor,))


def reemplazar_vendedor(nombre_viejo, nombre_nuevo, sucursal, vigente_desde=None, updated_by="admin"):
    """Reemplazo de personal en el mismo puesto (ej. alguien deja la
    empresa y otra persona toma su cartera): nombre_viejo deja de tener
    sucursal home (su historia cae en 'OTROS' automaticamente),
    nombre_nuevo se asigna como home de `sucursal`, y las metas/NE x
    Facturar que tenia asignadas nombre_viejo pasan a nombre_nuevo
    (mismo puesto, mismo objetivo/negocio pendiente). No toca ninguna
    fila de ventas.

    Paso manual aparte (no automatizado aqui, es un archivo local, no
    Postgres): correr generar_plantilla_ne.py despues de actualizar
    data_loader.VEND_HOME si se quiere que la PLANTILLA de
    NE_x_Facturar.xlsx (la que edita el gerente comercial) tambien
    muestre el nombre nuevo en vez de solo la fila ya renombrada en
    Postgres."""
    quitar_vendedor_home(nombre_viejo)
    asignar_vendedor_home(nombre_nuevo, sucursal, vigente_desde, updated_by)
    db.execute("UPDATE metas SET vendedor = %s WHERE vendedor = %s", (nombre_nuevo, nombre_viejo))
    db.execute("UPDATE ne_x_facturar SET vendedor = %s, updated_at = now() WHERE vendedor = %s", (nombre_nuevo, nombre_viejo))


def _roster_por_sucursal(cur):
    """(sucursal -> [vendedores]) desde vendedor_home, ordenado por
    ORDEN_SUCURSALES y alfabetico dentro de cada sucursal. Fuente unica
    del roster para las tablas web de NE/Metas -- un vendedor nuevo
    (agregado via asignar_vendedor_home) aparece solo, sin regenerar
    nada a mano."""
    cur.execute("SELECT vendedor, sucursal FROM vendedor_home")
    por_suc = {}
    for f in cur.fetchall():
        por_suc.setdefault(f["sucursal"], []).append(f["vendedor"])
    orden_suc = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    return {suc: sorted(vends) for suc, vends in sorted(por_suc.items(), key=lambda kv: orden_suc.get(kv[0], 99))}


def get_ne_x_facturar_pg(filtro_sucursal=None):
    """Roster completo (vendedor_home + una fila 'OTROS' por sucursal,
    igual que generar_plantilla_ne.py) con el monto NE actual -- 0 si
    nunca se cargo nada para ese vendedor.

    filtro_sucursal (Jefe de Sucursal, ej. German=MT) reduce el roster
    a solo esa sucursal -- cada Jefe carga sus propios NE, no ve ni
    puede tocar los de otra sucursal."""
    permitidas = None
    if filtro_sucursal:
        permitidas = set(filtro_sucursal) if isinstance(filtro_sucursal, (list, tuple, set)) else {filtro_sucursal}

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            roster = _roster_por_sucursal(cur)
            cur.execute("SELECT sucursal, vendedor, monto_ne FROM ne_x_facturar")
            montos = {(f["sucursal"], f["vendedor"]): float(f["monto_ne"]) for f in cur.fetchall()}

    filas = []
    for suc, vendedores in roster.items():
        if permitidas is not None and suc not in permitidas:
            continue
        for vend in vendedores:
            filas.append({"sucursal": suc, "vendedor": vend, "monto_ne": montos.get((suc, vend), 0.0)})
        filas.append({"sucursal": suc, "vendedor": "OTROS", "monto_ne": montos.get((suc, "OTROS"), 0.0)})
    return filas


def guardar_ne_x_facturar_pg(filas, updated_by="admin", sucursales_permitidas=None):
    """filas: [{sucursal, vendedor, monto_ne}, ...] -- upsert completo.

    sucursales_permitidas (Jefe de Sucursal) descarta -- del lado del
    servidor, no solo en la pantalla -- cualquier fila que no sea de
    su propia sucursal, aunque llegue en el request."""
    if sucursales_permitidas is not None:
        permitidas = set(sucursales_permitidas) if isinstance(sucursales_permitidas, (list, tuple, set)) else {sucursales_permitidas}
        filas = [f for f in filas if f["sucursal"] in permitidas]

    sql = """INSERT INTO ne_x_facturar (sucursal, vendedor, monto_ne, updated_by)
             VALUES (%(sucursal)s, %(vendedor)s, %(monto_ne)s, %(by)s)
             ON CONFLICT (sucursal, vendedor) DO UPDATE SET
               monto_ne = excluded.monto_ne, updated_at = now(), updated_by = excluded.updated_by"""
    params = [
        {"sucursal": f["sucursal"], "vendedor": f["vendedor"], "monto_ne": f["monto_ne"], "by": updated_by}
        for f in filas
    ]
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, params)
        conn.commit()
    return len(filas)


def get_metas_roster_pg(ano, mes):
    """Roster de vendedor_home (sin fila OTROS, igual que metas.xlsx
    hoy) con la meta guardada para (ano, mes) -- 0 si no hay nada
    cargado aun para ese mes."""
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            roster = _roster_por_sucursal(cur)
            cur.execute("SELECT sucursal, vendedor, meta FROM metas WHERE ano = %s AND mes = %s", (ano, mes))
            metas_actual = {(f["sucursal"], f["vendedor"]): float(f["meta"]) for f in cur.fetchall()}

    filas = []
    for suc, vendedores in roster.items():
        for vend in vendedores:
            filas.append({"sucursal": suc, "vendedor": vend, "meta": metas_actual.get((suc, vend), 0.0)})
    return filas


def guardar_metas_pg(ano, mes, filas):
    """filas: [{sucursal, vendedor, meta}, ...] -- upsert completo para
    ese (ano, mes)."""
    sql = """INSERT INTO metas (ano, mes, sucursal, vendedor, meta)
             VALUES (%(ano)s, %(mes)s, %(sucursal)s, %(vendedor)s, %(meta)s)
             ON CONFLICT (ano, mes, sucursal, vendedor) DO UPDATE SET meta = excluded.meta"""
    params = [
        {"ano": ano, "mes": mes, "sucursal": f["sucursal"], "vendedor": f["vendedor"], "meta": f["meta"]}
        for f in filas
    ]
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, params)
        conn.commit()


def get_roster_vendedor_home_pg():
    """Contenido actual de vendedor_home, ordenado por sucursal
    (ORDEN_SUCURSALES) y alfabetico -- para mostrar en la pantalla de
    gestion de vendedores."""
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT vendedor, sucursal, vigente_desde FROM vendedor_home")
            filas = cur.fetchall()
    orden_suc = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    filas = sorted(filas, key=lambda f: (orden_suc.get(f["sucursal"], 99), f["vendedor"]))
    return [
        {
            "vendedor": f["vendedor"],
            "sucursal": f["sucursal"],
            "vigente_desde": f["vigente_desde"].isoformat() if f["vigente_desde"] else None,
        }
        for f in filas
    ]


def get_vendedores_con_venta_pg():
    """Todo nombre de VENDEDOR (crudo, tal cual SAP) que aparece en
    ventas, con su venta total y filas -- para el buscador/autocomplete
    de la pantalla de gestion de vendedores. Un nombre elegido de aqui
    hace match exacto garantizado (evita errores de tipeo al mover/
    reemplazar a alguien); un nombre que NO aparece aqui es un
    vendedor nuevo sin ventas registradas todavia."""
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT vendedor, sum(total) AS venta, count(*) AS n_filas
                    FROM ventas WHERE vendedor IS NOT NULL
                    GROUP BY vendedor ORDER BY vendedor"""
            )
            filas = cur.fetchall()
    return [
        {"vendedor": f["vendedor"], "venta": float(f["venta"]), "n_filas": f["n_filas"]}
        for f in filas
    ]


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
