"""
Datos de Inventario. Fuente: data/inventario/Inventario.xlsx -- a
diferencia de Ventas, el inventario NO se acumula: es una foto del
stock a la fecha, y ese archivo se reemplaza entero cada vez que se
vuelve a exportar desde SAP. No hay logica de consolidacion aqui.
"""
import os
import glob
import shutil
import pandas as pd

BASE_DIR             = os.path.dirname(os.path.abspath(__file__))
DATA_DIR_INVENTARIO  = os.path.join(BASE_DIR, "data", "inventario")
INVENTARIO_XLSX      = os.path.join(DATA_DIR_INVENTARIO, "Inventario.xlsx")
DATOS_DUROS_XLSX     = os.path.join(DATA_DIR_INVENTARIO, "Datos_Duros_Inventario.xlsx")

# (nombre a mostrar, columna de stock, columna de transito o None)
BODEGAS = [
    ("Chicureo",                    "STOCK CHICUREO",                    "TRANSITO CHICUREO"),
    ("Las Condes",                  "STOCK LAS CONDES",                  "TRANSITO LAS CONDES"),
    ("Maipú",                       "STOCK MAIPU",                       "TRANSITO MAIPU"),
    ("Manuel Rodríguez",            "STOCK MANUEL RODRIGUEZ",            "TRANSITO MANUEL RODRIGUEZ"),
    ("Matta",                       "STOCK MATTA",                       "TRANSITO MATTA"),
    ("San Isidro",                  "STOCK SAN ISIDRO",                  "TRANSITO SAN ISIDRO"),
    ("Oficina",                     "STOCK OFICINA",                     None),
    ("Mercado Libre Full",          "STOCK MERCADO LIBRE FULL",          None),
    ("Mercado Libre Full Schneider","STOCK MERCADO LIBRE FULL SCHNEIDER",None),
    ("E-commerce",                  "STOCK ECOMMERCE",                   None),
    ("Merma",                       "STOCK MERMA",                       None),
]

# Orden de criticidad para mostrar la clasificacion consolidada
# (CLAS_CSD) -- AAA es la mas critica, SM0 "sin movimiento".
ORDEN_CLASIFICACION = ["AAA", "M05", "M04", "M03", "M02", "M01", "SM0"]

# ID_PROCEDENCIA (codigo numerico de SAP): 3 y 7 = Importado, el
# resto = Nacional -- regla dada por el usuario, no deducida.
IDS_IMPORTADO = {3, 7}

# Bodega -> columna de venta mensual en Datos_Duros_Inventario.xlsx.
# Estas 6 bodegas tienen venta mensual propia (Maipu agregada 2026-08-25,
# antes no tenia dato real); el resto (Oficina, Mercado Libre Full,
# Mercado Libre Full Schneider, E-commerce, Merma) sigue sin columna --
# para esas, venta_mensual y alcance quedan en None (la columna igual
# se muestra en pantalla, solo el valor queda vacio -- pedido explicito
# del usuario).
VENTA_MENSUAL_COL = {
    "Chicureo":         "VENTA MENSUAL CHICUREO",
    "Las Condes":       "VENTA MENSUAL LAS CONDES",
    "Maipú":            "VENTA MENSUAL MAIPU",
    "Manuel Rodríguez": "VENTA MENSUAL MANUEL RODRIGUEZ",
    "Matta":            "VENTA MENSUAL MATTA",
    "San Isidro":       "VENTA MENSUAL SAN ISIDRO",
}
VENTA_MENSUAL_TODAS = "VENTA MENSUAL CONSOLIDADA"

_cache = {"df": None, "mod_time": None, "mod_time_dd": None}


def _bodegas_activas(df):
    """BODEGAS filtrado a solo las que existen en el archivo y tienen
    stock > 0 hoy -- una bodega en $0 (ej. Oficina) se excluye sola de
    todos los reportes, sin necesidad de sacarla a mano de la lista si
    algun dia vuelve a tener stock."""
    return [
        (nombre, stock_col, transito_col)
        for nombre, stock_col, transito_col in BODEGAS
        if stock_col in df.columns and df[stock_col].sum() > 0
    ]


def _leer_hoja_con_datos(path, columnas_esperadas):
    """Lee un Excel buscando la hoja que tiene las columnas esperadas,
    en vez de asumir que siempre es la primera -- si alguien deja una
    hoja de trabajo/borrador adicional en el archivo (ej. un pedido
    armado a mano) y esa queda primera, pandas leeria esa hoja en vez
    de los datos reales. Si ninguna hoja calza, cae de vuelta a la
    primera hoja (comportamiento anterior) para no ocultar un error
    real de formato."""
    try:
        xl = pd.ExcelFile(path, engine="calamine")
    except Exception:
        xl = pd.ExcelFile(path)
    try:
        for nombre_hoja in xl.sheet_names:
            columnas = xl.parse(nombre_hoja, nrows=0).columns
            if all(c in columnas for c in columnas_esperadas):
                return xl.parse(nombre_hoja)
        return xl.parse(xl.sheet_names[0])
    finally:
        # Cerrar el handle del archivo explicitamente -- si no, en
        # Windows un os.remove()/shutil.move() inmediatamente despues
        # (ej. en las rutas /api/subir_*) puede fallar con "el proceso
        # no tiene acceso al archivo porque esta siendo usado por otro
        # proceso" (bug real encontrado 2026-08-26, dependia del timing
        # del garbage collector para no reproducirse).
        xl.close()


def fusionar_datos_duros(df):
    """Cruza FAMILIA/SUBFAMILIA/GRUPO/VENTA MENSUAL * (de
    Datos_Duros_Inventario.xlsx) sobre un df ya leido del export SAP,
    por CODIGO -- opcional, si no existe el archivo simplemente no hay
    apertura por Familia. Extraido de _leer_inventario() para
    reutilizarlo tambien desde /api/subir_inventario (app.py), que lee
    el export subido por la web y no pasa por el cache de disco."""
    if os.path.exists(DATOS_DUROS_XLSX):
        dd = _leer_hoja_con_datos(DATOS_DUROS_XLSX, columnas_esperadas=["CODIGO", "FAMILIA", "SUBFAMILIA"])
        cols_venta = [c for c in dd.columns if c.startswith("VENTA MENSUAL")]
        dd = dd[["CODIGO", "FAMILIA", "SUBFAMILIA", "GRUPO"] + cols_venta].drop_duplicates("CODIGO")
        df = df.merge(dd, on="CODIGO", how="left")
    else:
        df["FAMILIA"] = None
        df["SUBFAMILIA"] = None
        df["GRUPO"] = None
    return df


def _leer_inventario():
    if not os.path.exists(INVENTARIO_XLSX):
        raise FileNotFoundError(f"No se encontro {INVENTARIO_XLSX}")
    mod_time    = os.path.getmtime(INVENTARIO_XLSX)
    mod_time_dd = os.path.getmtime(DATOS_DUROS_XLSX) if os.path.exists(DATOS_DUROS_XLSX) else None

    necesita_recargar = (
        _cache["df"] is None
        or _cache["mod_time"] != mod_time
        or _cache["mod_time_dd"] != mod_time_dd
    )
    if necesita_recargar:
        df = _leer_hoja_con_datos(INVENTARIO_XLSX, columnas_esperadas=["CODIGO", "CUP"])
        df["CUP"] = pd.to_numeric(df["CUP"], errors="coerce").fillna(0)
        df = fusionar_datos_duros(df)

        _cache["df"]          = df
        _cache["mod_time"]    = mod_time
        _cache["mod_time_dd"] = mod_time_dd
    return _cache["df"]


def actualizar_desde_archivo():
    """
    Busca un archivo AAMMDD_Inventario.xlsx (6 digitos de fecha, ej.
    260728_Inventario.xlsx -- el nombre que exporta SAP) en
    data/inventario/. Si lo encuentra, REEMPLAZA Inventario.xlsx entero
    (no se fusiona -- el inventario no es acumulable, es una foto nueva
    cada vez) y marca el original como .done para no volver a
    procesarlo.

    El patron exige 6 digitos al inicio (no solo "*_Inventario.xlsx")
    para no confundirse con otros archivos de la misma carpeta que
    tambien terminan en "_Inventario.xlsx" (ej.
    Datos_Duros_Inventario.xlsx) -- eso paso una vez y el boton
    reemplazo Inventario.xlsx con el archivo equivocado.
    """
    patron = os.path.join(DATA_DIR_INVENTARIO, "[0-9][0-9][0-9][0-9][0-9][0-9]_Inventario.xlsx")
    candidatos = [
        f for f in glob.glob(patron)
        if not f.endswith((".done", ".procesando"))
        and not os.path.basename(f).startswith("~$")
    ]
    if not candidatos:
        return {"ok": False, "msg": "No se encontro un archivo nuevo (ej. AAMMDD_Inventario.xlsx) en data/inventario/."}

    origen = sorted(candidatos)[-1]
    try:
        shutil.copy2(origen, INVENTARIO_XLSX)
    except Exception as e:
        return {"ok": False, "msg": f"Error al actualizar: {e}"}

    _cache["df"]       = None
    _cache["mod_time"] = None

    # Renombrar a .done es opcional (evita reprocesar el mismo archivo
    # de nuevo) -- si el archivo sigue abierto en Excel, Windows no
    # deja renombrarlo. No es motivo de error: lo importante (los
    # datos ya se actualizaron) ya paso.
    try:
        os.rename(origen, origen + ".done")
    except Exception:
        pass

    return {"ok": True, "msg": f"Inventario actualizado desde {os.path.basename(origen)}."}


def get_resumen_inventario():
    """
    TOTAL_GENERAL (columna del archivo) = stock + transito sumados --
    verificado 100% de las filas. Por eso valor_stock_total se calcula
    SOLO con las columnas STOCK (no con TOTAL_GENERAL), y valor_total
    es la suma de stock + transito -- si se usara TOTAL_GENERAL para
    "stock" Y se sumara transito aparte, el transito quedaria contado
    dos veces.
    """
    df = _leer_inventario()
    bodegas_activas = _bodegas_activas(df)

    total_skus     = len(df)
    skus_con_stock = int((df["TOTAL_GENERAL"] > 0).sum())
    skus_sin_stock = total_skus - skus_con_stock

    valor_stock_total = 0.0
    for _, stock_col, _ in bodegas_activas:
        valor_stock_total += float((df[stock_col] * df["CUP"]).sum())

    valor_transito_total = 0.0
    for _, _, transito_col in bodegas_activas:
        if transito_col and transito_col in df.columns:
            valor_transito_total += float((df[transito_col] * df["CUP"]).sum())
    # + transito servicio tecnico, que no tiene bodega de stock propia
    if "TRANSITO SERVICIO TECNICO" in df.columns:
        valor_transito_total += float((df["TRANSITO SERVICIO TECNICO"] * df["CUP"]).sum())

    por_bodega = []
    for nombre, stock_col, _ in bodegas_activas:
        valor = float((df[stock_col] * df["CUP"]).sum())
        por_bodega.append({"bodega": nombre, "valor_stock": round(valor, 0)})
    por_bodega.sort(key=lambda r: -r["valor_stock"])

    return {
        "total_skus":           total_skus,
        "skus_con_stock":       skus_con_stock,
        "skus_sin_stock":       skus_sin_stock,
        "valor_stock_total":    round(valor_stock_total, 0),
        "valor_transito_total": round(valor_transito_total, 0),
        "valor_total":          round(valor_stock_total + valor_transito_total, 0),
        "por_bodega":           por_bodega,
    }


def get_inventario_por_bodega():
    df = _leer_inventario()

    filas = []
    for nombre, stock_col, transito_col in _bodegas_activas(df):
        stock_qty  = float(df[stock_col].sum())
        valor_stock = float((df[stock_col] * df["CUP"]).sum())
        skus_con_stock = int((df[stock_col] > 0).sum())

        transito_qty = None
        valor_transito = None
        if transito_col and transito_col in df.columns:
            transito_qty  = float(df[transito_col].sum())
            valor_transito = float((df[transito_col] * df["CUP"]).sum())

        filas.append({
            "bodega":          nombre,
            "valor_total":     round(valor_stock + (valor_transito or 0), 0),
            "stock_qty":       round(stock_qty, 0),
            "valor_stock":     round(valor_stock, 0),
            "skus_con_stock":  skus_con_stock,
            "transito_qty":    round(transito_qty, 0) if transito_qty is not None else None,
            "valor_transito":  round(valor_transito, 0) if valor_transito is not None else None,
        })

    # Transito Servicio Tecnico no tiene bodega de stock propia (ver
    # BODEGAS) -- se muestra como su propia fila para que el total de
    # transito cuadre con el del Resumen.
    if "TRANSITO SERVICIO TECNICO" in df.columns:
        transito_qty  = float(df["TRANSITO SERVICIO TECNICO"].sum())
        valor_transito = float((df["TRANSITO SERVICIO TECNICO"] * df["CUP"]).sum())
        filas.append({
            "bodega":         "Servicio Técnico (tránsito)",
            "valor_total":    round(valor_transito, 0),
            "stock_qty":      0,
            "valor_stock":    0,
            "skus_con_stock": 0,
            "transito_qty":   round(transito_qty, 0),
            "valor_transito": round(valor_transito, 0),
        })

    filas.sort(key=lambda r: -r["valor_total"])

    total = {
        "bodega":         "TOTAL GENERAL",
        "valor_total":    round(sum(f["valor_total"] for f in filas), 0),
        "stock_qty":      round(sum(f["stock_qty"] for f in filas), 0),
        "valor_stock":    round(sum(f["valor_stock"] for f in filas), 0),
        "skus_con_stock": int((df["TOTAL_GENERAL"] > 0).sum()),
        "transito_qty":   round(sum(f["transito_qty"] or 0 for f in filas), 0),
        "valor_transito": round(sum(f["valor_transito"] or 0 for f in filas), 0),
    }

    return {"filas": filas, "total": total}


def _columnas_bodega(df):
    """Bodegas activas + Servicio Tecnico (solo transito), en el
    formato comun que usan las aperturas por dimension x bodega."""
    columnas = [
        {"bodega": nombre, "stock_col": stock_col, "transito_col": transito_col}
        for nombre, stock_col, transito_col in _bodegas_activas(df)
    ]
    if "TRANSITO SERVICIO TECNICO" in df.columns:
        columnas.append({
            "bodega": "Servicio Técnico (ST-TRANS)",
            "stock_col": None,
            "transito_col": "TRANSITO SERVICIO TECNICO",
        })
    return columnas


def _pivot_dimension_por_bodega(df, columna_dim, valores_dim):
    """
    Apertura generica: una dimension (clasificacion, procedencia, etc,
    ya calculada en columna_dim) x bodega -- cuanto valor (stock y
    transito) hay en cada bodega para cada valor de esa dimension.
    valores_dim: lista ordenada de los valores a mostrar como filas.
    """
    columnas_bodega = _columnas_bodega(df)

    filas = []
    for valor_dim in valores_dim:
        sub = df[df[columna_dim] == valor_dim]
        fila = {"clase": valor_dim, "skus": len(sub), "valores": {}}
        for col in columnas_bodega:
            valor_stock = None
            if col["stock_col"]:
                valor_stock = float((sub[col["stock_col"]] * sub["CUP"]).sum())
            valor_transito = None
            if col["transito_col"] and col["transito_col"] in sub.columns:
                valor_transito = float((sub[col["transito_col"]] * sub["CUP"]).sum())
            fila["valores"][col["bodega"]] = {
                "stock":    round(valor_stock, 0) if valor_stock is not None else None,
                "transito": round(valor_transito, 0) if valor_transito is not None else None,
            }
        fila["total_general"] = round(sum(
            (v["stock"] or 0) + (v["transito"] or 0) for v in fila["valores"].values()
        ), 0)
        filas.append(fila)

    total = {"clase": "TOTAL GENERAL", "skus": len(df), "valores": {}}
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


def get_inventario_por_clasificacion():
    """
    Apertura por clasificacion consolidada (CLAS_CSD) x bodega. Usa
    CLAS_CSD como unica clasificacion por producto (no una distinta
    por bodega) -- asi cada fila de la tabla suma limpio sin
    ambiguedad de "que clasificacion aplica aqui".
    """
    df = _leer_inventario()
    df = df.copy()
    df["_CLAS"] = df["CLAS_CSD"].fillna("SIN CLASIFICAR")

    clases_presentes = [c for c in ORDEN_CLASIFICACION if c in df["_CLAS"].unique()]
    if "SIN CLASIFICAR" in df["_CLAS"].unique():
        clases_presentes.append("SIN CLASIFICAR")

    return _pivot_dimension_por_bodega(df, "_CLAS", clases_presentes)


def get_inventario_por_procedencia():
    """Nacional vs Importado x bodega, segun IDS_IMPORTADO (regla dada
    por el usuario: ID_PROCEDENCIA 3 y 7 = Importado, el resto
    Nacional)."""
    df = _leer_inventario()
    df = df.copy()
    df["_PROCEDENCIA"] = df["ID_PROCEDENCIA"].apply(
        lambda x: "Importado" if x in IDS_IMPORTADO else "Nacional"
    )

    return _pivot_dimension_por_bodega(df, "_PROCEDENCIA", ["Nacional", "Importado"])


def get_inventario_por_familia():
    """
    Apertura por Familia x bodega. FAMILIA viene de un archivo aparte
    (Datos_Duros_Inventario.xlsx, cruzado por CODIGO) -- si ese archivo
    no esta presente, todo cae en "SIN FAMILIA".
    """
    df = _leer_inventario()
    df = df.copy()
    df["_FAMILIA"] = df["FAMILIA"].fillna("SIN FAMILIA")

    # Orden por valor total (stock+transito) de mayor a menor -- a
    # diferencia de clasificacion/procedencia, aqui no hay un orden de
    # negocio fijo, asi que se ordena por relevancia.
    columnas_bodega = _columnas_bodega(df)
    def valor_familia(familia):
        sub = df[df["_FAMILIA"] == familia]
        total = 0.0
        for col in columnas_bodega:
            if col["stock_col"]:
                total += float((sub[col["stock_col"]] * sub["CUP"]).sum())
            if col["transito_col"] and col["transito_col"] in sub.columns:
                total += float((sub[col["transito_col"]] * sub["CUP"]).sum())
        return total

    familias_presentes = sorted(df["_FAMILIA"].unique(), key=valor_familia, reverse=True)

    return _pivot_dimension_por_bodega(df, "_FAMILIA", familias_presentes)


def get_bodegas_disponibles():
    """Lista de bodegas con stock propio (para el selector de la
    pagina Por Marca/Subfamilia) -- no incluye Servicio Tecnico, que
    solo tiene transito y no tiene sentido "abrir por marca" ahi.
    "Todas" va primero: suma stock/transito de todas las bodegas y usa
    VENTA MENSUAL CONSOLIDADA para el alcance."""
    df = _leer_inventario()
    activas = _bodegas_activas(df)
    return [{"bodega": "Todas", "tiene_transito": any(t for _, _, t in activas)}] + [
        {"bodega": nombre, "tiene_transito": bool(transito_col)}
        for nombre, _, transito_col in activas
    ]


def get_inventario_por_marca_subfamilia(bodega, procedencia=None):
    """
    Apertura por Marca (fila principal) con Subfamilia anidada debajo
    de cada marca, para una bodega especifica o "Todas" (suma de todas
    las bodegas) -- opcionalmente filtrada por Nacional/Importado.

    Ademas de stock/transito, incluye Venta Mensual y Alcance (= stock
    / venta mensual, en meses de cobertura). Si la bodega elegida no
    tiene columna de venta mensual propia (Maipu, Oficina, Mercado
    Libre Full, Mercado Libre Full Schneider, E-commerce, Merma),
    venta_mensual y alcance quedan en None -- pero las columnas siguen
    mostrandose siempre en pantalla, solo el valor queda vacio (pedido
    explicito del usuario: "no hacer desaparecer las columnas").
    """
    df = _leer_inventario()
    activas = _bodegas_activas(df)

    if bodega == "Todas":
        stock_cols = [s for _, s, _ in activas]
        transito_cols = [t for _, _, t in activas if t]
        tiene_transito = bool(transito_cols)
        venta_col = VENTA_MENSUAL_TODAS if VENTA_MENSUAL_TODAS in df.columns else None
    else:
        bodega_info = next(((n, s, t) for n, s, t in activas if n == bodega), None)
        if bodega_info is None:
            raise ValueError(f"Bodega no encontrada o sin stock: {bodega}")
        _, stock_col, transito_col = bodega_info
        stock_cols = [stock_col]
        transito_cols = [transito_col] if transito_col else []
        tiene_transito = bool(transito_col)
        venta_col = VENTA_MENSUAL_COL.get(bodega)
        if venta_col and venta_col not in df.columns:
            venta_col = None

    df = df.copy()
    if procedencia and procedencia not in ("todas", "Todas", ""):
        df["_PROCEDENCIA"] = df["ID_PROCEDENCIA"].apply(
            lambda x: "Importado" if x in IDS_IMPORTADO else "Nacional"
        )
        df = df[df["_PROCEDENCIA"] == procedencia]

    df["_MARCA"] = df["MARCA"].fillna("SIN MARCA")
    df["_SUBFAMILIA"] = df["SUBFAMILIA"].fillna("SIN SUBFAMILIA")

    # Columnas auxiliares calculadas UNA vez sobre todo el df (vectorizado)
    # en vez de volver a sumar columna por columna dentro de cada grupo
    # chico (marca/subfamilia) -- eso ultimo es lo que hacia lenta esta
    # pantalla, sobre todo con "Todas" (11 columnas de stock + 6 de
    # transito), porque pandas repetia el mismo trabajo cientos de veces.
    df["_STOCK_VALOR"] = 0.0
    df["_STOCK_QTY"] = 0.0
    for c in stock_cols:
        df["_STOCK_VALOR"] += df[c] * df["CUP"]
        df["_STOCK_QTY"] += df[c]
    df["_TRANSITO_VALOR"] = 0.0
    df["_TRANSITO_QTY"] = 0.0
    for c in transito_cols:
        df["_TRANSITO_VALOR"] += df[c] * df["CUP"]
        df["_TRANSITO_QTY"] += df[c]

    # Venta Mensual se muestra valorizada ($ = unidades x CUP, igual que
    # Stock/Transito) para que sea comparable en pantalla, pero el
    # Alcance se calcula con UNIDADES puras (stock fisico / venta
    # mensual en unidades) -- Venta Mensual viene en unidades
    # (verificado: Chicureo stock=50.179 unid. vs venta mensual=54.077,
    # misma escala; el stock valorizado son $70M). Mezclar $ con
    # unidades daria un "alcance" sin sentido.
    if venta_col:
        venta_qty = df[venta_col].fillna(0)
        df["_VENTA_QTY"] = venta_qty
        df["_VENTA_VALOR"] = venta_qty * df["CUP"]
    else:
        df["_VENTA_QTY"] = 0.0
        df["_VENTA_VALOR"] = 0.0

    # Una sola pasada agregada (marca, subfamilia) -- vectorizado.
    agg = df.groupby(["_MARCA", "_SUBFAMILIA"], as_index=False).agg(
        stock=("_STOCK_VALOR", "sum"),
        transito=("_TRANSITO_VALOR", "sum"),
        stock_qty=("_STOCK_QTY", "sum"),
        transito_qty=("_TRANSITO_QTY", "sum"),
        venta_qty=("_VENTA_QTY", "sum"),
        venta_valor=("_VENTA_VALOR", "sum"),
    )

    def fila_de(stock, transito, stock_qty, transito_qty, venta_qty, venta_valor):
        v_venta = float(venta_valor) if venta_col else None
        alcance = round(stock_qty / venta_qty, 2) if venta_col and venta_qty > 0 else None
        return {
            "stock":         round(float(stock), 0),
            "transito":      round(float(transito), 0),
            "stock_qty":     round(float(stock_qty), 0),
            "transito_qty":  round(float(transito_qty), 0),
            "venta_mensual": round(v_venta, 0) if v_venta is not None else None,
            "venta_qty":     round(float(venta_qty), 0) if venta_col else None,
            "alcance":       alcance,
        }

    marcas = []
    for marca, grupo in agg.groupby("_MARCA"):
        stock_marca = float(grupo["stock"].sum())
        transito_marca = float(grupo["transito"].sum())
        if stock_marca == 0 and transito_marca == 0:
            continue
        fm = fila_de(
            stock_marca, transito_marca,
            float(grupo["stock_qty"].sum()), float(grupo["transito_qty"].sum()),
            float(grupo["venta_qty"].sum()), float(grupo["venta_valor"].sum()),
        )

        subfamilias = []
        for _, row in grupo.iterrows():
            if row["stock"] == 0 and row["transito"] == 0:
                continue
            fs = fila_de(
                row["stock"], row["transito"], row["stock_qty"], row["transito_qty"],
                row["venta_qty"], row["venta_valor"],
            )
            subfamilias.append({"subfamilia": row["_SUBFAMILIA"], **fs})
        subfamilias.sort(key=lambda s: -(s["stock"] + s["transito"]))

        marcas.append({"marca": marca, **fm, "subfamilias": subfamilias})
    marcas.sort(key=lambda m: -(m["stock"] + m["transito"]))

    # El total se recalcula sobre el agregado completo (no sumando los $
    # de cada marca) para que el alcance general use la misma logica
    # unidades/unidades que cada fila -- sumar "alcance" por marca no
    # tendria sentido (no es una cantidad aditiva).
    total = fila_de(
        agg["stock"].sum(), agg["transito"].sum(),
        agg["stock_qty"].sum(), agg["transito_qty"].sum(),
        agg["venta_qty"].sum(), agg["venta_valor"].sum(),
    )

    return {
        "bodega":         bodega,
        "tiene_transito": tiene_transito,
        "tiene_venta":    venta_col is not None,
        "marcas":         marcas,
        "total":          total,
    }

