"""
Datos de Adquisiciones. Fuente: data/adquisiciones/ -- Compras_2025/
2026.xlsx (Ordenes de Compra creadas) y Recepciones_2025/2026.xlsx
(mercaderia efectivamente recibida). Cada archivo es una foto del año
(no se acumula dia a dia como Ventas, se reemplaza entero cuando
alguien vuelve a exportarlo). Se cruzan por N_OC / N_ORDEN_COMPRA.

Compras_2026.xlsx puede traer varias hojas en el archivo (a veces
incluye calculadoras de costo/margen de un proveedor especifico que
no son compras) -- se busca la hoja que tenga las columnas esperadas
en vez de asumir un nombre fijo.
"""
import os
import numpy as np
import pandas as pd

import data_loader as dl

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR_ADQUISICIONES = os.path.join(BASE_DIR, "data", "adquisiciones")
COMPRAS_2025_XLSX = os.path.join(DATA_DIR_ADQUISICIONES, "Compras_2025.xlsx")
COMPRAS_2026_XLSX = os.path.join(DATA_DIR_ADQUISICIONES, "Compras_2026.xlsx")
RECEPCIONES_2025_XLSX = os.path.join(DATA_DIR_ADQUISICIONES, "Recepciones_2025.xlsx")
RECEPCIONES_2026_XLSX = os.path.join(DATA_DIR_ADQUISICIONES, "Recepciones_2026.xlsx")

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

COLUMNAS_ESPERADAS = {"FECHA_CREACION", "N_ORDEN_COMPRA", "PRECIO_TOTAL"}

# Proveedores excluidos de todo el analisis de Adquisiciones -- sus OC
# quedaron mal emitidas/registradas y no deben contarse en ninguna
# pantalla, actual o futura (se filtra aca, en la carga, para que
# quede aplicado en un solo lugar).
PROVEEDORES_EXCLUIDOS = [
    "ASCABLE-RECAEL, S.A.U",
    "DISTRIBUIDORA DE PAPELES INDUSTRIALES SPA",
    "EXXIS S.A.",
    "PROMOTICK MARKETING CHILE LIMITADA",
    "IMPORTADORA IMPRECIN SPA",
    "ROBERTO CESAR FERNANDEZ SANTOS",
    "TRANSPORTES PIZARRO SPA",
    "CUSATTO SPA",
    "INGEREV SERVICIOS Y COMERCIALIZADORA SPA",
    "SOUTH TELECOM Y NETWORKING LTDA",
    "COMERCIAL METALCONEX LTDA",
]

_cache_compras = {2025: {"df": None, "mod_time": None}, 2026: {"df": None, "mod_time": None}}
_cache_recepciones = {2025: {"df": None, "mod_time": None}, 2026: {"df": None, "mod_time": None}}


def _hoja_con_datos(xl):
    """Busca, entre todas las hojas del archivo, la que tiene la
    estructura real de compras -- no asume que sea la primera ni que
    se llame igual siempre (ver docstring del modulo)."""
    for hoja in xl.sheet_names:
        candidata = xl.parse(hoja)
        if COLUMNAS_ESPERADAS.issubset(candidata.columns):
            return candidata
    raise ValueError(
        f"Ninguna hoja de {xl.io} tiene las columnas esperadas de compras "
        f"({', '.join(sorted(COLUMNAS_ESPERADAS))})."
    )


def _leer_archivo(ano):
    xlsx = COMPRAS_2025_XLSX if ano == 2025 else COMPRAS_2026_XLSX
    if not os.path.exists(xlsx):
        raise FileNotFoundError(f"No se encontro {xlsx}")

    mod_time = os.path.getmtime(xlsx)
    if _cache_compras[ano]["df"] is None or _cache_compras[ano]["mod_time"] != mod_time:
        try:
            xl = pd.ExcelFile(xlsx, engine="calamine")
        except Exception:
            xl = pd.ExcelFile(xlsx)
        df = _hoja_con_datos(xl)

        df["FECHA_CREACION"] = pd.to_datetime(df["FECHA_CREACION"])
        df["MES"] = df["FECHA_CREACION"].dt.month
        df["DIA"] = df["FECHA_CREACION"].dt.day
        df = df[~df["NOMBRE_PROVEEDOR"].isin(PROVEEDORES_EXCLUIDOS)]

        _cache_compras[ano]["df"] = df
        _cache_compras[ano]["mod_time"] = mod_time

    return _cache_compras[ano]["df"]


def get_df_2025():
    return _leer_archivo(2025)


def get_df_2026():
    return _leer_archivo(2026)


def _leer_archivo_recepciones(ano):
    xlsx = RECEPCIONES_2025_XLSX if ano == 2025 else RECEPCIONES_2026_XLSX
    if not os.path.exists(xlsx):
        raise FileNotFoundError(f"No se encontro {xlsx}")

    mod_time = os.path.getmtime(xlsx)
    if _cache_recepciones[ano]["df"] is None or _cache_recepciones[ano]["mod_time"] != mod_time:
        try:
            df = pd.read_excel(xlsx, engine="calamine")
        except Exception:
            df = pd.read_excel(xlsx)

        df["FECHA_RECEPCION"] = pd.to_datetime(df["FECHA_RECEPCION"])
        df["MES"] = df["FECHA_RECEPCION"].dt.month
        df["DIA"] = df["FECHA_RECEPCION"].dt.day
        df = df[~df["NOMBRE_PROVEEDOR"].isin(PROVEEDORES_EXCLUIDOS)]

        _cache_recepciones[ano]["df"] = df
        _cache_recepciones[ano]["mod_time"] = mod_time

    return _cache_recepciones[ano]["df"]


def get_df_recepciones_2025():
    return _leer_archivo_recepciones(2025)


def get_df_recepciones_2026():
    return _leer_archivo_recepciones(2026)


def var_pct(actual, anterior):
    if anterior == 0:
        return 0.0
    return round((float(actual) - float(anterior)) / float(anterior) * 100, 1)


def _fecha_datos():
    """Ultima fecha con datos cargados en 2026 -- a diferencia de
    Ventas, esto no tiene un mecanismo de confirmacion diaria, es
    simplemente el maximo FECHA_CREACION del archivo (una foto que se
    reemplaza cuando alguien sube uno nuevo)."""
    df26 = get_df_2026()
    return df26["FECHA_CREACION"].max()


def _filtrar_tipo(df, tipo_compra):
    return df[df["TIPO_COMPRA"] == tipo_compra] if tipo_compra else df


def get_resumen(tipo_compra=None):
    """KPIs de Adquisiciones -- comprado año actual vs mismo periodo
    año anterior (YTD exacto, hasta el mismo dia del mes), y mes
    actual vs mes calendario anterior. tipo_compra ("STOCK"/"PEDIDO")
    acota todo el calculo a ese tipo -- usado por las pantallas de
    Compras a Pedido / Compras para Stock."""
    df25 = _filtrar_tipo(get_df_2025(), tipo_compra)
    df26 = _filtrar_tipo(get_df_2026(), tipo_compra)
    fecha_datos = _fecha_datos()
    mes_actual = fecha_datos.month
    dia_actual = fecha_datos.day

    compra_ano_26 = float(df26["PRECIO_TOTAL"].sum())
    compra_ano_25 = float(df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]["PRECIO_TOTAL"].sum())

    compra_mes_26 = float(df26[df26["MES"] == mes_actual]["PRECIO_TOTAL"].sum())
    compra_mes_25 = float(df25[(df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual)]["PRECIO_TOTAL"].sum())

    mes_anterior = mes_actual - 1 if mes_actual > 1 else 12
    compra_mes_ant = float(df26[
        (df26["MES"] == mes_anterior) & (df26["DIA"] <= dia_actual)
    ]["PRECIO_TOTAL"].sum())

    oc_ano_26 = int(df26["N_ORDEN_COMPRA"].nunique())
    oc_ano_25 = int(df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]["N_ORDEN_COMPRA"].nunique())

    proveedores_ano_26 = int(df26["NOMBRE_PROVEEDOR"].nunique())

    return {
        "c_ano_actual":       round(compra_ano_26, 0),
        "c_ano_anterior":     round(compra_ano_25, 0),
        "var_ano":            var_pct(compra_ano_26, compra_ano_25),
        "c_mes_actual":       round(compra_mes_26, 0),
        "c_mes_ant_ano":      round(compra_mes_25, 0),
        "var_mes_ano":        var_pct(compra_mes_26, compra_mes_25),
        "c_mes_ant_mes":      round(compra_mes_ant, 0),
        "var_mes_mes":        var_pct(compra_mes_26, compra_mes_ant),
        "oc_ano_actual":      oc_ano_26,
        "oc_ano_anterior":    oc_ano_25,
        "var_oc_ano":         var_pct(oc_ano_26, oc_ano_25),
        "proveedores_activos": proveedores_ano_26,
        "mes_nombre":         MESES.get(mes_actual, ""),
        "mes_anterior_nombre": MESES.get(mes_anterior, ""),
        "ano_actual":         2026,
        "ano_anterior":       2025,
        "fecha_datos":        fecha_datos.strftime("%d-%m-%Y"),
    }


def get_compras_por_mes(tipo_compra=None):
    """Comprado por mes calendario, año actual vs año anterior
    completo -- para el grafico de evolucion mensual."""
    df25 = _filtrar_tipo(get_df_2025(), tipo_compra)
    df26 = _filtrar_tipo(get_df_2026(), tipo_compra)

    meses = []
    for mes in range(1, 13):
        c26 = float(df26[df26["MES"] == mes]["PRECIO_TOTAL"].sum())
        c25 = float(df25[df25["MES"] == mes]["PRECIO_TOTAL"].sum())
        meses.append({
            "mes":        mes,
            "mes_nombre": MESES.get(mes, ""),
            "actual":     round(c26, 0),
            "anterior":   round(c25, 0),
        })

    return {"meses": meses, "ano_actual": 2026, "ano_anterior": 2025}


def get_compras_por_proveedor(tipo_compra=None):
    """Compra por proveedor -- año actual vs mismo periodo (YTD) año
    anterior, N de OC y participacion sobre el total comprado del año
    actual (para ver concentracion de compras)."""
    df25 = _filtrar_tipo(get_df_2025(), tipo_compra)
    df26 = _filtrar_tipo(get_df_2026(), tipo_compra)
    fecha_datos = _fecha_datos()
    mes_actual = fecha_datos.month
    dia_actual = fecha_datos.day

    df25_ytd = df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]

    g26 = df26.groupby("NOMBRE_PROVEEDOR").agg(
        compra=("PRECIO_TOTAL", "sum"),
        n_oc=("N_ORDEN_COMPRA", "nunique"),
        rut=("RUT", "first"),
    )
    g25 = df25_ytd.groupby("NOMBRE_PROVEEDOR")["PRECIO_TOTAL"].sum()

    # Compra por proveedor x mes (año actual) -- para ver estacionalidad
    # de cada proveedor en columnas Ene..Dic en la pantalla.
    g26_mes = df26.groupby(["NOMBRE_PROVEEDOR", "MES"])["PRECIO_TOTAL"].sum()

    total_compra_26 = float(df26["PRECIO_TOTAL"].sum())

    # Union de proveedores de ambos periodos -- uno que solo compro en
    # 2025 (ej. dejo de ser proveedor) igual aparece, con $0 en 2026.
    proveedores = set(df26["NOMBRE_PROVEEDOR"].dropna().unique()) | set(df25_ytd["NOMBRE_PROVEEDOR"].dropna().unique())

    items = []
    for p in proveedores:
        c26 = float(g26["compra"].get(p, 0.0)) if p in g26.index else 0.0
        c25 = float(g25.get(p, 0.0))
        n_oc = int(g26["n_oc"].get(p, 0)) if p in g26.index else 0
        rut = g26["rut"].get(p) if p in g26.index else None
        item = {
            "nombre":        p,
            "rut":           rut,
            "c_ano_actual":  round(c26, 0),
            "c_ano_anterior": round(c25, 0),
            "var_ano":       var_pct(c26, c25),
            "n_oc":          n_oc,
            "participacion": round(c26 / total_compra_26 * 100, 2) if total_compra_26 > 0 else 0.0,
        }
        for mes in range(1, 13):
            item[f"mes_{mes}"] = round(float(g26_mes.get((p, mes), 0.0)), 0)
        items.append(item)

    items.sort(key=lambda x: -x["c_ano_actual"])

    total = {
        "c_ano_actual":   round(total_compra_26, 0),
        "c_ano_anterior": round(float(df25_ytd["PRECIO_TOTAL"].sum()), 0),
        "n_oc":           int(df26["N_ORDEN_COMPRA"].nunique()),
        "participacion":  100.0,
    }
    total["var_ano"] = var_pct(total["c_ano_actual"], total["c_ano_anterior"])
    for mes in range(1, 13):
        total[f"mes_{mes}"] = round(float(df26[df26["MES"] == mes]["PRECIO_TOTAL"].sum()), 0)

    return {
        "items": items,
        "total": total,
        "ano_actual": 2026,
        "ano_anterior": 2025,
    }


def _fecha_datos_recepciones():
    """Ultima fecha con datos cargados de Recepciones 2026 -- foto
    independiente de la de Compras (pueden llegar en momentos
    distintos)."""
    return get_df_recepciones_2026()["FECHA_RECEPCION"].max()


def _filtrar_tipo_oc(df, tipo_compra):
    return df[df["TIPO_OC"] == tipo_compra] if tipo_compra else df


def get_resumen_recepciones(tipo_compra=None):
    """KPIs de Recepciones -- recibido año actual vs mismo periodo
    (YTD) año anterior, y mes actual vs mes calendario anterior.
    Mismo criterio que get_resumen() pero sobre lo efectivamente
    recibido (TOTAL_CLP, FECHA_RECEPCION), no lo pedido. tipo_compra
    ("STOCK"/"PEDIDO") filtra por TIPO_OC -- usado por las pantallas
    de Compras a Pedido / Compras para Stock para comparar comprado
    vs recibido del mismo tipo."""
    df25 = _filtrar_tipo_oc(get_df_recepciones_2025(), tipo_compra)
    df26 = _filtrar_tipo_oc(get_df_recepciones_2026(), tipo_compra)
    fecha_datos = _fecha_datos_recepciones()
    mes_actual = fecha_datos.month
    dia_actual = fecha_datos.day

    recibido_ano_26 = float(df26["TOTAL_CLP"].sum())
    recibido_ano_25 = float(df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]["TOTAL_CLP"].sum())

    recibido_mes_26 = float(df26[df26["MES"] == mes_actual]["TOTAL_CLP"].sum())
    recibido_mes_25 = float(df25[(df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual)]["TOTAL_CLP"].sum())

    mes_anterior = mes_actual - 1 if mes_actual > 1 else 12
    recibido_mes_ant = float(df26[
        (df26["MES"] == mes_anterior) & (df26["DIA"] <= dia_actual)
    ]["TOTAL_CLP"].sum())

    rec_ano_26 = int(df26["N_RECEPCION"].nunique())
    rec_ano_25 = int(df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]["N_RECEPCION"].nunique())

    proveedores_ano_26 = int(df26["NOMBRE_PROVEEDOR"].nunique())

    return {
        "r_ano_actual":       round(recibido_ano_26, 0),
        "r_ano_anterior":     round(recibido_ano_25, 0),
        "var_ano":            var_pct(recibido_ano_26, recibido_ano_25),
        "r_mes_actual":       round(recibido_mes_26, 0),
        "r_mes_ant_ano":      round(recibido_mes_25, 0),
        "var_mes_ano":        var_pct(recibido_mes_26, recibido_mes_25),
        "r_mes_ant_mes":      round(recibido_mes_ant, 0),
        "var_mes_mes":        var_pct(recibido_mes_26, recibido_mes_ant),
        "rec_ano_actual":     rec_ano_26,
        "rec_ano_anterior":   rec_ano_25,
        "var_rec_ano":        var_pct(rec_ano_26, rec_ano_25),
        "proveedores_activos": proveedores_ano_26,
        "mes_nombre":         MESES.get(mes_actual, ""),
        "mes_anterior_nombre": MESES.get(mes_anterior, ""),
        "ano_actual":         2026,
        "ano_anterior":       2025,
        "fecha_datos":        fecha_datos.strftime("%d-%m-%Y"),
    }


def get_recepciones_por_mes(tipo_compra=None):
    """Recibido por mes calendario, año actual vs año anterior
    completo -- para el grafico de evolucion mensual."""
    df25 = _filtrar_tipo_oc(get_df_recepciones_2025(), tipo_compra)
    df26 = _filtrar_tipo_oc(get_df_recepciones_2026(), tipo_compra)

    meses = []
    for mes in range(1, 13):
        r26 = float(df26[df26["MES"] == mes]["TOTAL_CLP"].sum())
        r25 = float(df25[df25["MES"] == mes]["TOTAL_CLP"].sum())
        meses.append({
            "mes":        mes,
            "mes_nombre": MESES.get(mes, ""),
            "actual":     round(r26, 0),
            "anterior":   round(r25, 0),
        })

    return {"meses": meses, "ano_actual": 2026, "ano_anterior": 2025}


def _lead_time_por_oc(compras_df, primera_recepcion):
    """Por cada OC (unica por N_ORDEN_COMPRA): proveedor, fecha de
    creacion y dias hasta la primera recepcion (None si aun no se ha
    recibido nada). primera_recepcion es una Serie N_OC -> fecha
    minima de recepcion, calculada sobre AMBOS años de Recepciones
    combinados -- una OC de fin de año puede recibirse recien al año
    siguiente."""
    oc = compras_df.drop_duplicates("N_ORDEN_COMPRA")[
        ["N_ORDEN_COMPRA", "NOMBRE_PROVEEDOR", "FECHA_CREACION"]
    ].copy()
    oc["fecha_recepcion"] = oc["N_ORDEN_COMPRA"].map(primera_recepcion)
    oc["lead_time_dias"] = (oc["fecha_recepcion"] - oc["FECHA_CREACION"]).dt.days
    # Un lead time negativo es un error de datos (recepcion registrada
    # antes que la OC) -- se descarta en vez de distorsionar el promedio.
    oc.loc[oc["lead_time_dias"] < 0, "lead_time_dias"] = None
    return oc


def get_lead_time_por_proveedor():
    """Lead time real (dias entre FECHA_CREACION de la OC y su primera
    recepcion) por proveedor -- año actual vs año anterior, para ver
    quien cumple los plazos y quien no. Tambien cuenta OC sin ninguna
    recepcion registrada todavia (pendientes)."""
    compras25 = get_df_2025()
    compras26 = get_df_2026()
    recepciones = pd.concat([get_df_recepciones_2025(), get_df_recepciones_2026()])
    primera_recepcion = recepciones.groupby("N_OC")["FECHA_RECEPCION"].min()

    oc25 = _lead_time_por_oc(compras25, primera_recepcion)
    oc26 = _lead_time_por_oc(compras26, primera_recepcion)

    g26 = oc26.groupby("NOMBRE_PROVEEDOR").agg(
        n_oc_total=("N_ORDEN_COMPRA", "count"),
        n_oc_recibidas=("lead_time_dias", "count"),
        lead_promedio=("lead_time_dias", "mean"),
        lead_min=("lead_time_dias", "min"),
        lead_max=("lead_time_dias", "max"),
    )
    g25_promedio = oc25.groupby("NOMBRE_PROVEEDOR")["lead_time_dias"].mean()

    proveedores = set(oc26["NOMBRE_PROVEEDOR"].dropna().unique())

    items = []
    for p in proveedores:
        fila = g26.loc[p]
        n_oc_total = int(fila["n_oc_total"])
        n_oc_recibidas = int(fila["n_oc_recibidas"])
        lead_promedio = round(float(fila["lead_promedio"]), 1) if n_oc_recibidas > 0 else None
        items.append({
            "nombre":            p,
            "n_oc_total":        n_oc_total,
            "n_oc_recibidas":    n_oc_recibidas,
            "n_oc_pendientes":   n_oc_total - n_oc_recibidas,
            "lead_time_actual":  lead_promedio,
            "lead_time_min":     int(fila["lead_min"]) if n_oc_recibidas > 0 else None,
            "lead_time_max":     int(fila["lead_max"]) if n_oc_recibidas > 0 else None,
            "lead_time_anterior": round(float(g25_promedio.get(p)), 1) if p in g25_promedio.index and pd.notna(g25_promedio.get(p)) else None,
        })

    # Proveedores sin ninguna recepcion primero no tienen como ordenar
    # por lead time -- van al final, y entre ellos por volumen de OC.
    items.sort(key=lambda x: (x["lead_time_actual"] is None, -(x["lead_time_actual"] or 0), -x["n_oc_total"]))

    recibidas_total = oc26["lead_time_dias"].dropna()
    lead_time_empresa = round(float(recibidas_total.mean()), 1) if len(recibidas_total) > 0 else None
    recibidas_total_25 = oc25["lead_time_dias"].dropna()
    lead_time_empresa_25 = round(float(recibidas_total_25.mean()), 1) if len(recibidas_total_25) > 0 else None

    return {
        "items": items,
        "lead_time_empresa_actual":   lead_time_empresa,
        "lead_time_empresa_anterior": lead_time_empresa_25,
        "ano_actual": 2026,
        "ano_anterior": 2025,
    }


UMBRAL_ON_TIME_DIAS_HABILES = 5  # mismo estandar Nacional que Plan de Compra


def get_cumplimiento_por_proveedor():
    """OTIF (On Time In Full) por proveedor, linea por linea (OC +
    CODIGO) del año actual:
      - In Full: cantidad recibida == cantidad comprada (exacto).
      - On Time: la primera recepcion de esa linea llega dentro de
        UMBRAL_ON_TIME_DIAS_HABILES dias HABILES (excluye fines de
        semana) desde la fecha de creacion de la OC.
      - OTIF: cumple ambas condiciones.
    Una linea nunca recibida cuenta como incumplimiento en ambos
    criterios (no hay a que darle el beneficio de la duda)."""
    compras26 = get_df_2026()
    recepciones = pd.concat([get_df_recepciones_2025(), get_df_recepciones_2026()])

    linea_compra = compras26.groupby(["N_ORDEN_COMPRA", "CODIGO"]).agg(
        cantidad_comprada=("CANTIDAD_COMPRADA", "sum"),
        proveedor=("NOMBRE_PROVEEDOR", "first"),
        fecha_creacion=("FECHA_CREACION", "first"),
    ).reset_index()

    linea_recepcion = recepciones.groupby(["N_OC", "CODIGO"]).agg(
        cantidad_recibida=("CANTIDAD", "sum"),
        fecha_primera_recepcion=("FECHA_RECEPCION", "min"),
    ).reset_index().rename(columns={"N_OC": "N_ORDEN_COMPRA"})

    m = linea_compra.merge(linea_recepcion, on=["N_ORDEN_COMPRA", "CODIGO"], how="left")
    m["cantidad_recibida"] = m["cantidad_recibida"].fillna(0)

    def _dias_habiles(fila):
        if pd.isna(fila["fecha_primera_recepcion"]):
            return None
        return int(np.busday_count(
            fila["fecha_creacion"].date(), fila["fecha_primera_recepcion"].date()
        ))

    m["dias_habiles"] = m.apply(_dias_habiles, axis=1)
    m["in_full"] = np.isclose(m["cantidad_recibida"], m["cantidad_comprada"])
    m["on_time"] = m["dias_habiles"].apply(lambda d: d is not None and d <= UMBRAL_ON_TIME_DIAS_HABILES)
    m["otif"] = m["in_full"] & m["on_time"]

    g = m.groupby("proveedor").agg(
        n_lineas=("CODIGO", "count"),
        n_in_full=("in_full", "sum"),
        n_on_time=("on_time", "sum"),
        n_otif=("otif", "sum"),
    )

    items = []
    for proveedor, fila in g.iterrows():
        n_lineas = int(fila["n_lineas"])
        items.append({
            "nombre":       proveedor,
            "n_lineas":     n_lineas,
            "pct_in_full":  round(float(fila["n_in_full"]) / n_lineas * 100, 1),
            "pct_on_time":  round(float(fila["n_on_time"]) / n_lineas * 100, 1),
            "pct_otif":     round(float(fila["n_otif"]) / n_lineas * 100, 1),
        })

    items.sort(key=lambda x: (x["pct_otif"], -x["n_lineas"]))

    total_lineas = len(m)
    resumen = {
        "n_lineas":    total_lineas,
        "pct_in_full": round(float(m["in_full"].sum()) / total_lineas * 100, 1) if total_lineas > 0 else 0.0,
        "pct_on_time": round(float(m["on_time"].sum()) / total_lineas * 100, 1) if total_lineas > 0 else 0.0,
        "pct_otif":    round(float(m["otif"].sum()) / total_lineas * 100, 1) if total_lineas > 0 else 0.0,
    }

    return {
        "items": items,
        "resumen": resumen,
        "umbral_dias_habiles": UMBRAL_ON_TIME_DIAS_HABILES,
        "ano_actual": 2026,
    }


def get_oc_pendientes():
    """Lineas de OC (2025+2026 combinado, no solo año actual -- una
    OC vieja sigue pendiente hasta que se recibe o se cierra) donde
    la cantidad recibida es menor a la comprada. Cruza Compras con
    Recepciones linea por linea (OC + CODIGO) en vez de usar el campo
    CANTIDAD_PENDIENTE del propio SAP, que subestima bastante el total
    real (solo marca ~30% de lo que el cruce encuentra -- parece
    quedar desactualizado o solo reflejar un estado puntual)."""
    compras = pd.concat([get_df_2025(), get_df_2026()])
    recepciones = pd.concat([get_df_recepciones_2025(), get_df_recepciones_2026()])
    fecha_hoy = max(_fecha_datos(), _fecha_datos_recepciones())

    linea_compra = compras.groupby(["N_ORDEN_COMPRA", "CODIGO"]).agg(
        cantidad_comprada=("CANTIDAD_COMPRADA", "sum"),
        proveedor=("NOMBRE_PROVEEDOR", "first"),
        descripcion=("DESCRIPCION", "first"),
        fecha_creacion=("FECHA_CREACION", "first"),
        precio_unitario=("PRECIO_UNITARIO", "first"),
        sucursal=("SUCURSAL", "first"),
    ).reset_index()

    linea_recepcion = recepciones.groupby(["N_OC", "CODIGO"])["CANTIDAD"].sum().reset_index().rename(
        columns={"N_OC": "N_ORDEN_COMPRA", "CANTIDAD": "cantidad_recibida"}
    )

    m = linea_compra.merge(linea_recepcion, on=["N_ORDEN_COMPRA", "CODIGO"], how="left")
    m["cantidad_recibida"] = m["cantidad_recibida"].fillna(0)
    m["pendiente"] = (m["cantidad_comprada"] - m["cantidad_recibida"]).clip(lower=0)

    pend = m[m["pendiente"] > 0].copy()
    pend["dias_transcurridos"] = (fecha_hoy - pend["fecha_creacion"]).dt.days
    pend["monto_pendiente"] = pend["pendiente"] * pend["precio_unitario"]

    items = []
    for _, fila in pend.iterrows():
        items.append({
            "n_orden_compra":    int(fila["N_ORDEN_COMPRA"]),
            "proveedor":         fila["proveedor"],
            "codigo":            int(fila["CODIGO"]),
            "descripcion":       fila["descripcion"],
            "sucursal":          fila["sucursal"],
            "fecha_creacion":    fila["fecha_creacion"].strftime("%d-%m-%Y"),
            "dias_transcurridos": int(fila["dias_transcurridos"]),
            "cantidad_comprada": float(fila["cantidad_comprada"]),
            "cantidad_recibida": float(fila["cantidad_recibida"]),
            "cantidad_pendiente": float(fila["pendiente"]),
            "monto_pendiente":   round(float(fila["monto_pendiente"]), 0),
        })

    items.sort(key=lambda x: -x["dias_transcurridos"])

    resumen = {
        "n_lineas":       len(items),
        "n_oc_unicas":    int(pend["N_ORDEN_COMPRA"].nunique()),
        "n_proveedores":  int(pend["proveedor"].nunique()),
        "monto_total":    round(float(pend["monto_pendiente"].sum()), 0),
        "dias_promedio":  round(float(pend["dias_transcurridos"].mean()), 1) if len(pend) > 0 else 0.0,
    }

    return {"items": items, "resumen": resumen}


def get_conversion_pedido_venta():
    """Por mes de recepcion: de lo comprado a Pedido ese mes (en $),
    cuanto se vendio despues y que porcentaje -- TOPADO al 100% por
    codigo+lote (si se compraron 10 unidades de un codigo y despues
    se vendieron 8, son 8/10 = 80%; si se vendieran 15, sigue siendo
    100%, nunca mas de lo que se compro ese lote puntual).

    No hay forma de trazar una OC puntual hasta la venta que la
    origino (no existe un campo que conecte ambos sistemas a ese
    nivel), asi que la venta se atribuye por CODIGO: cada venta se
    imputa al LOTE de recepcion Pedido mas reciente de ese mismo
    codigo que sea anterior o igual a la fecha de venta (merge_asof
    "backward"), y se consume en orden cronologico contra la cantidad
    de ese lote hasta agotarla -- lo que sobra despues de agotar el
    lote NO se cuenta (puede venir de stock por otra via, no de esta
    compra puntual). Esto tambien evita el doble conteo que habria si
    un codigo se recibio mas de una vez: una venta de abril despues
    de recepciones en enero Y marzo se atribuye al lote de marzo (el
    vigente en ese momento), no a ambos meses. Una venta anterior a
    CUALQUIER recepcion Pedido de ese codigo queda fuera del calculo."""
    recepciones = pd.concat([get_df_recepciones_2025(), get_df_recepciones_2026()])
    recepciones_pedido = recepciones[recepciones["TIPO_OC"] == "PEDIDO"]

    # Lotes = una fila por (codigo, fecha de recepcion) -- si llegaron
    # varias lineas el mismo dia para el mismo codigo, se suman.
    lotes = recepciones_pedido.groupby(["CODIGO", "FECHA_RECEPCION"]).agg(
        cantidad_comprada=("CANTIDAD", "sum"),
        monto_comprado=("TOTAL_CLP", "sum"),
    ).reset_index()
    # Precio de COMPRA (costo) por unidad del lote -- lo vendido se
    # valoriza a este precio, no al precio de venta (que trae margen),
    # para que "monto vendido" nunca pueda superar "monto comprado"
    # solo por efecto del margen entre costo y venta.
    lotes["precio_compra_unitario"] = lotes["monto_comprado"] / lotes["cantidad_comprada"]
    # merge_asof exige que la columna "on" este ordenada de forma
    # GLOBAL (no solo dentro de cada grupo "by") en ambos lados.
    lotes = lotes.sort_values("FECHA_RECEPCION").reset_index(drop=True)

    ventas = pd.concat([dl.get_df_2025(), dl.get_df_2026()])
    ventas_rel = ventas[ventas["CODIGO_CM"].isin(lotes["CODIGO"].unique())][
        ["CODIGO_CM", "FECHA_CONTA", "CANTIDAD", "TOTAL"]
    ].sort_values("FECHA_CONTA").reset_index(drop=True)

    # merge_asof empareja cada venta con el lote MAS RECIENTE de ese
    # mismo codigo cuya fecha de recepcion sea <= fecha de la venta.
    match = pd.merge_asof(
        ventas_rel, lotes,
        left_on="FECHA_CONTA", right_on="FECHA_RECEPCION",
        left_by="CODIGO_CM", right_by="CODIGO",
        direction="backward",
    )
    match = match.dropna(subset=["FECHA_RECEPCION"])  # ventas anteriores a cualquier recepcion Pedido

    # Consumo acumulado del lote, en orden cronologico de venta: cada
    # venta solo cuenta la porcion que todavia cabe dentro de lo que
    # ese lote tenia comprado -- lo que excede el lote no se cuenta.
    match = match.sort_values(["CODIGO", "FECHA_RECEPCION", "FECHA_CONTA"])
    cum_previo = match.groupby(["CODIGO", "FECHA_RECEPCION"])["CANTIDAD"].cumsum() - match["CANTIDAD"]
    disponible = (match["cantidad_comprada"] - cum_previo).clip(lower=0)
    match["cantidad_contada"] = np.minimum(match["CANTIDAD"].clip(lower=0), disponible)
    match["monto_contado"] = match["cantidad_contada"] * match["precio_compra_unitario"]

    venta_por_lote = match.groupby(["CODIGO", "FECHA_RECEPCION"]).agg(
        monto_vendido=("monto_contado", "sum"),
    ).reset_index()

    lotes = lotes.merge(venta_por_lote, on=["CODIGO", "FECHA_RECEPCION"], how="left")
    lotes["monto_vendido"] = lotes["monto_vendido"].fillna(0.0)
    # Tope final de resguardo: si una devolucion queda intercalada
    # entre dos ventas del mismo lote, el consumo acumulado puede
    # sobreestimar levemente -- nunca debe superar lo comprado.
    lotes["monto_vendido"] = lotes[["monto_vendido", "monto_comprado"]].min(axis=1)
    lotes["ano"] = lotes["FECHA_RECEPCION"].dt.year
    lotes["mes"] = lotes["FECHA_RECEPCION"].dt.month

    g_mes = lotes.groupby(["ano", "mes"]).agg(
        monto_comprado=("monto_comprado", "sum"),
        monto_vendido=("monto_vendido", "sum"),
    ).reset_index()

    meses_items = []
    for _, fila in g_mes.sort_values(["ano", "mes"]).iterrows():
        comprado = float(fila["monto_comprado"])
        vendido = float(fila["monto_vendido"])
        meses_items.append({
            "ano":             int(fila["ano"]),
            "mes":             int(fila["mes"]),
            "mes_nombre":      MESES.get(int(fila["mes"]), ""),
            "monto_comprado":  round(comprado, 0),
            "monto_vendido":   round(vendido, 0),
            "pct_conversion":  round(vendido / comprado * 100, 1) if comprado > 0 else 0.0,
        })

    total_comprado = float(lotes["monto_comprado"].sum())
    total_vendido = float(lotes["monto_vendido"].sum())
    resumen = {
        "monto_comprado_total": round(total_comprado, 0),
        "monto_vendido_total":  round(total_vendido, 0),
        "pct_conversion_global": round(total_vendido / total_comprado * 100, 1) if total_comprado > 0 else 0.0,
    }

    return {"meses": meses_items, "resumen": resumen}


def get_codigos_6_sin_venta():
    """Codigos que empiezan con "6" (regla de negocio ya usada en
    Series Domiciliarias -- suelen ser SKUs nuevos/menos establecidos)
    que se han recibido (Recepciones 2025+2026, cualquier TIPO_OC) y
    NUNCA se han vendido (venta neta <= 0 en todo el periodo de
    Ventas). Ordenado por fecha de la ultima recepcion, mas reciente
    primero -- para revisar primero lo que acaba de llegar y todavia
    no vende nada."""
    recepciones = pd.concat([get_df_recepciones_2025(), get_df_recepciones_2026()])
    recepciones = recepciones[recepciones["CODIGO"].astype(str).str.startswith("6")]

    por_codigo = recepciones.groupby("CODIGO").agg(
        descripcion=("DESCRIPCION", "first"),
        proveedor=("NOMBRE_PROVEEDOR", "first"),
        cantidad_recibida=("CANTIDAD", "sum"),
        monto_recibido=("TOTAL_CLP", "sum"),
        fecha_ultima_recepcion=("FECHA_RECEPCION", "max"),
        tipo_oc=("TIPO_OC", lambda s: "/".join(sorted(s.unique()))),
    ).reset_index()

    ventas = pd.concat([dl.get_df_2025(), dl.get_df_2026()])
    venta_por_codigo = ventas[ventas["CODIGO_CM"].isin(por_codigo["CODIGO"])].groupby("CODIGO_CM")["CANTIDAD"].sum()

    por_codigo["cantidad_vendida"] = por_codigo["CODIGO"].map(venta_por_codigo).fillna(0.0)
    sin_venta = por_codigo[por_codigo["cantidad_vendida"] <= 0].copy()
    sin_venta = sin_venta.sort_values("fecha_ultima_recepcion", ascending=False)

    fecha_hoy = max(_fecha_datos(), _fecha_datos_recepciones())

    items = []
    for _, fila in sin_venta.iterrows():
        items.append({
            "codigo":              int(fila["CODIGO"]),
            "descripcion":         fila["descripcion"],
            "proveedor":           fila["proveedor"],
            "tipo_oc":             fila["tipo_oc"],
            "fecha_ultima_recepcion": fila["fecha_ultima_recepcion"].strftime("%d-%m-%Y"),
            "dias_desde_recepcion": int((fecha_hoy - fila["fecha_ultima_recepcion"]).days),
            "cantidad_recibida":   float(fila["cantidad_recibida"]),
            "monto_recibido":      round(float(fila["monto_recibido"]), 0),
        })

    resumen = {
        "n_codigos_6_total":    int(por_codigo["CODIGO"].nunique()),
        "n_codigos_sin_venta":  len(items),
        "monto_total_sin_venta": round(float(sin_venta["monto_recibido"].sum()), 0),
    }

    return {"items": items, "resumen": resumen}
