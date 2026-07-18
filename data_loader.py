import pandas as pd
import os
import re
import glob
from pathlib import Path
from datetime import datetime, date
import calendar

# ══════════════════════════════════════════════════════
#  CONFIGURACION
# ══════════════════════════════════════════════════════
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
VENTAS_2025    = os.path.join(BASE_DIR, "data", "Ventas_2025.xlsx")
VENTAS_2026    = os.path.join(BASE_DIR, "data", "Ventas_2026.xlsx")
DATA_DIR        = os.path.join(BASE_DIR, "data")
NE_X_FACTURAR  = os.path.join(DATA_DIR, "NE_x_Facturar.xlsx")
MG_NE_PCT      = 0.20  # margen estimado sobre Negocios Ganados aun no facturados
# CACHE_2026_PATH se define despues de detectar pyarrow

# ══════════════════════════════════════════════════════
#  MAPEO DE SUCURSAL LOGICA
#  La venta pertenece a la sucursal SAP donde ocurrio.
#  Unica excepcion: SI-STK se divide por vendedor.
# ══════════════════════════════════════════════════════

# Orden de aparicion en reportes
ORDEN_SUCURSALES = ["MT","LC","MR","SE","CMD","CH","MP","CANAL DIGITAL","OF"]

# Nombres para mostrar
NOMBRE_SUCURSAL = {
    "MT": "MT", "LC": "LC", "MR": "MR",
    "SE": "SE", "CMD": "CMD", "CH": "CH",
    "MP": "MP", "CANAL DIGITAL": "CANAL DIGITAL",
    "OF": "OF",
}

# Vendedores de SI-STK → SE (Santiago Empresas)
VEND_SE = {
    "ANDRES SEPULVEDA URRUTIA",
    "MARCELINO TORO MACHUCA",
    "YANETTE GONZALEZ MARCANO",
    "CARMEN ORELLANA",
}

# Vendedores de SI-STK → CMD (Casa Musa Distribucion)
VEND_CMD = {
    "CAMILA OLIVOS ROJAS",
    "FERNANDO MUSA BOZZO",
    "JAVIER LIZAMA CORNEJO",
    "JORGE SANTANA ANABALON",
    "MARCEL GUEDENEY ALLENDE",
}

# Vendedores de SI-STK → Canal Digital
VEND_CANAL = {
    "DANIEL GATICA",
    "ELENNYS PEREZ GUEDEZ",
    "MARIANNA SALAS PARRA",
}

# ══════════════════════════════════════════════════════
#  VENDEDORES OFICIALES POR SUCURSAL LOGICA
#  Cualquier otro vendedor que venda en esa sucursal
#  aparece agrupado como "OTROS" en la tabla.
# ══════════════════════════════════════════════════════
VEND_HOME = {
    "MT": {
        "PATRICIO YAÑEZ MACHUCA",
        "JUAN HIDALGO NUÑEZ",
        "JUAN REYES AVILA",
        "GERMAN CARRASCO SILVA",
        "GISELLA NORAMBUENA LILLO",
        "VENTAS OFICINA MT",
    },
    "LC": {
        "PATRIZIA DE TRIZIO MARTÍNEZ",
        "PATRICIO OVALLE QUEZADA",
        "PEDRO CASTILLO GONZALEZ",
        "STEVE ARJONA MOYA",
        "VENTAS OFICINA LC",
        "VENTA OFICINA",
    },
    "MR": {
        "ELISMAR VALERA MARTINEZ",
        "GEDEON ZAMBRANO MEZA",
        "ILEN COLMENAREZ",
        "STEFANIA JARA",
        "MILANGELA SANCHEZ",
        "VENTAS OFICINA MR",
    },
    "CH": {
        "FRANCISCA CORREA",
        "MARLENE ESCALONA",
        "VENTAS OFICINA CH",
    },
    "MP": {
        "IGOR MOYA",
        "PEDRO NAVEA",
        "JOSE VILLEGAS RODRIGUEZ",
    },
    "SE": {
        "ANDRES SEPULVEDA URRUTIA",
        "MARCELINO TORO MACHUCA",
        "YANETTE GONZALEZ MARCANO",
        "CARMEN ORELLANA",
    },
    "CMD": {
        "CAMILA OLIVOS ROJAS",
        "FERNANDO MUSA BOZZO",
        "JAVIER LIZAMA CORNEJO",
        "JORGE SANTANA ANABALON",
        "MARCEL GUEDENEY ALLENDE",
    },
    "CANAL DIGITAL": {
        "DANIEL GATICA",
        "ELENNYS PEREZ GUEDEZ",
        "MARIANNA SALAS PARRA",
    },
    "OF": {
        "DAVID SEPULVEDA JIMENEZ",
    },
}

# Set plano de pares (sucursal_logica|vendedor) para lookup rapido
_HOME_PAIRS = {f"{s}|{v}" for s, vends in VEND_HOME.items() for v in vends}

_MAPA_SUC_BASE = {
    "MT-STK": "MT", "LC-STK": "LC", "MR-STK": "MR",
    "CH-STK": "CH", "MP-STK": "MP", "OF-STK": "OF",
    "DM-STK": "CANAL DIGITAL", "SE-STK": "CANAL DIGITAL",
    "SI-STK": "SE",   # SI-STK default → SE; solo SE/CMD/Canal se subdividen por vendedor
}

def _aplicar_sucursal_logica(df):
    """
    Agrega dos columnas al dataframe (vectorizado):
      SUCURSAL_LOGICA : sucursal logica del negocio
      VENDEDOR_RPT    : nombre del vendedor si es home de esa sucursal, "OTROS" si no
    """
    suc  = df["SUCURSAL"].astype(str).str.strip()
    vend = df["VENDEDOR"].astype(str).str.strip()

    # Sucursal logica base
    sl = suc.map(_MAPA_SUC_BASE).fillna(suc)
    si = suc == "SI-STK"

    # Subdivir SI-STK por vendedor
    sl = sl.where(~(si & vend.isin(VEND_SE)),    "SE")
    sl = sl.where(~(si & vend.isin(VEND_CMD)),   "CMD")
    sl = sl.where(~(si & vend.isin(VEND_CANAL)), "CANAL DIGITAL")

    # Vendedor para reportes: nombre real si es home, "OTROS" si no
    pair_key = sl + "|" + vend
    vend_rpt = vend.where(pair_key.isin(_HOME_PAIRS), "OTROS")

    df = df.copy()
    df["SUCURSAL_LOGICA"] = sl
    df["VENDEDOR_RPT"]    = vend_rpt
    return df

MESES = {
    1:"Enero", 2:"Febrero", 3:"Marzo", 4:"Abril",
    5:"Mayo", 6:"Junio", 7:"Julio", 8:"Agosto",
    9:"Septiembre", 10:"Octubre", 11:"Noviembre", 12:"Diciembre"
}

# ══════════════════════════════════════════════════════
#  CACHE INDEPENDIENTE POR AÑO
#  2025: se carga una vez y no cambia
#  2026: se recarga solo si el archivo fue modificado
# ══════════════════════════════════════════════════════
_cache = {
    2025: {"df": None, "mod_time": None},
    2026: {"df": None, "mod_time": None},
}


try:
    import pyarrow  # noqa: F401
    _USE_PARQUET = True
except ImportError:
    _USE_PARQUET = False

def _cache_ext():
    return ".parquet" if _USE_PARQUET else ".pkl"

def _cache_write(df, path):
    if _USE_PARQUET:
        df.to_parquet(path, index=False)
    else:
        df.to_pickle(path)

def _cache_read(path):
    if _USE_PARQUET:
        return pd.read_parquet(path)
    else:
        return pd.read_pickle(path)

def _get_cache_2026_path():
    return os.path.join(DATA_DIR, "Ventas_2026" + _cache_ext())


def _normalizar_df(df):
    """Normaliza columnas comunes al leer un Excel de ventas."""
    df["FECHA_CONTA"] = pd.to_datetime(df["FECHA_CONTA"], errors="coerce")
    df["ANO"] = df["FECHA_CONTA"].dt.year
    df["MES"] = df["FECHA_CONTA"].dt.month
    df["DIA"] = df["FECHA_CONTA"].dt.day
    if "TIPO VENTA" in df.columns:
        df = df.rename(columns={"TIPO VENTA": "TIPO_VENTA"})
    if "CODIGO_PROVEEDOR" in df.columns:
        df["CODIGO_PROVEEDOR"] = df["CODIGO_PROVEEDOR"].astype(str)
    return df


def _detectar_archivo_mensual():
    """
    Busca archivos YYMM_Vta*.xlsx en la carpeta data/.
    Ignora archivos .procesando y .done.
    """
    todos = glob.glob(os.path.join(DATA_DIR, "[0-9][0-9][0-9][0-9]_Vta*.xlsx"))
    validos = [f for f in todos
               if not f.endswith(".procesando") and not f.endswith(".done")]
    return sorted(validos)[0] if validos else None


def _consolidar_mes_2026(filepath):
    """
    Carga el archivo mensual YYMM_Vtas.xlsx, reemplaza ese mes en el
    cache de 2026 y borra el xlsx.
    Retorna True si tuvo exito.
    """
    nombre = Path(filepath).stem
    m = re.match(r"^(\d{2})(\d{2})", nombre)
    if not m:
        print(f"  Archivo no reconocido: {nombre}, se omite.")
        return False

    ano_sufijo = int(m.group(1))
    mes        = int(m.group(2))
    ano        = 2000 + ano_sufijo

    cache_path = _get_cache_2026_path()
    try:
        print(f"  Consolidando {mes:02d}/{ano} desde {os.path.basename(filepath)}...")
        df_nuevo = pd.read_excel(filepath)
        df_nuevo = _normalizar_df(df_nuevo)

        if os.path.exists(cache_path):
            df_cache = _cache_read(cache_path)
        elif os.path.exists(VENTAS_2026):
            print("  Construyendo cache inicial desde Ventas_2026.xlsx...")
            df_cache = pd.read_excel(VENTAS_2026)
            df_cache = _normalizar_df(df_cache)
        else:
            df_cache = pd.DataFrame(columns=df_nuevo.columns)

        if "ANO" in df_cache.columns and "MES" in df_cache.columns:
            df_cache = df_cache[~((df_cache["ANO"] == ano) & (df_cache["MES"] == mes))]

        df_final = pd.concat([df_cache, df_nuevo], ignore_index=True)
        df_final = df_final.sort_values(["ANO", "MES", "DIA"]).reset_index(drop=True)

        _cache_write(df_final, cache_path)

        # Intentar borrar el xlsx; si falla en Windows, renombrar a .done
        try:
            os.remove(filepath)
        except OSError:
            try:
                os.rename(filepath, filepath + ".done")
            except OSError:
                print(f"  Aviso: no se pudo borrar {os.path.basename(filepath)} — borralo manualmente.")

        _cache[2026]["df"]       = None
        _cache[2026]["mod_time"] = None

        print(f"  Listo: {len(df_nuevo)} registros de {mes:02d}/{ano} consolidados.")
        print(f"  Total 2026 en cache: {len(df_final)} registros.")
        return True

    except Exception as e:
        print(f"  Error al consolidar: {e}")
        return False


def _leer_archivo(ano):
    xlsx    = VENTAS_2025 if ano == 2025 else VENTAS_2026
    cache_f = xlsx.replace(".xlsx", _cache_ext())

    if not os.path.exists(xlsx):
        raise FileNotFoundError(f"No se encontro {xlsx}")

    mod_xlsx = os.path.getmtime(xlsx)

    if _cache[ano]["df"] is None or _cache[ano]["mod_time"] != mod_xlsx:
        df = None
        # Intentar cache en disco si es mas nuevo que el xlsx
        if os.path.exists(cache_f) and os.path.getmtime(cache_f) >= mod_xlsx:
            try:
                print(f"  Cargando Ventas_{ano} desde cache rapido...")
                df = _cache_read(cache_f)
            except Exception as e:
                print(f"  Cache invalido ({e}), releyendo xlsx...")
                df = None
        # Leer xlsx si no hay cache valido
        if df is None:
            print(f"  Leyendo Ventas_{ano}.xlsx (primera vez o archivo actualizado)...")
            try:
                df = pd.read_excel(xlsx, engine="calamine")
            except Exception:
                df = pd.read_excel(xlsx)
            df = _normalizar_df(df)
            try:
                _cache_write(df, cache_f)
                print(f"  Cache guardado — proximas cargas seran instantaneas.")
            except Exception as e:
                print(f"  No se pudo guardar cache: {e}")
        df = _aplicar_sucursal_logica(df)
        _cache[ano]["df"]       = df
        _cache[ano]["mod_time"] = mod_xlsx

    return _cache[ano]["df"]





def _leer_ne_x_facturar():
    """
    Lee data/NE_x_Facturar.xlsx -> dict {(sucursal_logica, vendedor): monto_ne}.
    Archivo lo actualiza el gerente comercial ~1 vez por semana. Si no
    existe o no se puede leer, retorna diccionario vacio (monto_ne = 0
    para todos, no rompe la proyeccion).
    """
    if not os.path.exists(NE_X_FACTURAR):
        return {}
    try:
        df = pd.read_excel(NE_X_FACTURAR, sheet_name="NE x Facturar")
    except Exception as e:
        print(f"  No se pudo leer NE_x_Facturar.xlsx: {e}")
        return {}

    montos = {}
    for _, row in df.iterrows():
        suc  = str(row.get("Sucursal", "")).strip()
        vend = str(row.get("Vendedor", "")).strip()
        if not suc or not vend or suc == "nan" or vend == "nan":
            continue
        monto = row.get("Monto NE", 0)
        monto = float(monto) if pd.notna(monto) else 0.0
        montos[(suc, vend)] = montos.get((suc, vend), 0.0) + monto
    return montos


def actualizar_desde_archivo_mensual():
    """
    Detecta YYMM_Vtas*.xlsx en data/, consolida en cache e invalida memoria.
    Llamado SOLO desde el endpoint /admin/actualizar.
    Retorna dict con resultado.
    """
    archivo = _detectar_archivo_mensual()
    if not archivo:
        return {"ok": False, "msg": "No se encontro archivo mensual (ej. 2607_Vtas.xlsx) en la carpeta data/."}

    nombre = Path(archivo).stem
    m = re.match(r"^(\d{2})(\d{2})", nombre)
    if not m:
        return {"ok": False, "msg": f"Nombre de archivo no reconocido: {nombre}. Formato esperado: YYMM_Vtas.xlsx"}

    ano = 2000 + int(m.group(1))
    mes = int(m.group(2))

    cache_path = _get_cache_2026_path()
    try:
        df_nuevo = pd.read_excel(archivo)
        df_nuevo = _normalizar_df(df_nuevo)
        n_filas  = len(df_nuevo)
        vta_nueva = round(float(df_nuevo["TOTAL"].sum()), 0)

        # Base: desde cache o xlsx
        if os.path.exists(cache_path):
            df_cache = _cache_read(cache_path)
        elif os.path.exists(VENTAS_2026):
            df_cache = pd.read_excel(VENTAS_2026)
            df_cache = _normalizar_df(df_cache)
        else:
            df_cache = pd.DataFrame(columns=df_nuevo.columns)

        # Determinar fechas que trae el archivo nuevo
        fechas_nuevas = df_nuevo["FECHA_CONTA"].dt.date.unique()

        # Eliminar del cache solo esas fechas (evita duplicados sin borrar el mes)
        if len(df_cache) > 0 and "FECHA_CONTA" in df_cache.columns:
            df_cache = df_cache[~df_cache["FECHA_CONTA"].dt.date.isin(fechas_nuevas)]

        df_final = pd.concat([df_cache, df_nuevo], ignore_index=True)
        df_final = df_final.sort_values(["ANO","MES","DIA"]).reset_index(drop=True)

        _cache_write(df_final, cache_path)

        # Invalidar cache en memoria
        _cache[2026]["df"]       = None
        _cache[2026]["mod_time"] = None

        # Borrar o marcar como procesado
        try:
            os.remove(archivo)
        except OSError:
            try:
                os.rename(archivo, archivo + ".done")
            except OSError:
                pass

        return {
            "ok":      True,
            "mes":     mes,
            "ano":     ano,
            "filas":   n_filas,
            "vta":     vta_nueva,
            "msg":     f"OK: {n_filas} filas de {mes:02d}/{ano} consolidadas (${vta_nueva:,.0f})",
        }

    except Exception as e:
        return {"ok": False, "msg": f"Error: {str(e)}"}


def get_df_2025():
    return _leer_archivo(2025)

def get_df_2026():
    return _leer_archivo(2026)


def _fecha_datos():
    """
    Retorna la fecha maxima con datos en el archivo 2026 para el mes actual.
    Es la base real para calcular dias habiles transcurridos y proyeccion.
    Si el mes actual no tiene datos aun, retorna ayer.
    """
    df26     = get_df_2026()
    hoy      = datetime.now()
    df_mes   = df26[df26["MES"] == hoy.month]
    if len(df_mes) > 0:
        max_ts = df_mes["FECHA_CONTA"].max()
        return max_ts.date() if hasattr(max_ts, "date") else hoy.date()
    return (hoy - __import__("datetime").timedelta(days=1)).date()


# ══════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════
def fmt_clp(valor):
    return "${:,.0f}".format(float(valor)).replace(",", ".")

def fmt_mm(valor):
    return "${:,.0f} MM".format(float(valor) / 1_000_000).replace(",", ".")

def var_pct(actual, anterior):
    if anterior == 0:
        return 0.0
    return round((float(actual) - float(anterior)) / float(anterior) * 100, 1)


# ══════════════════════════════════════════════════════
#  KPIs RESUMEN GENERAL
# ══════════════════════════════════════════════════════
def get_resumen():
    df25 = get_df_2025()
    df26 = get_df_2026()
    fecha_datos = _fecha_datos()
    mes_actual  = fecha_datos.month
    dia_actual  = fecha_datos.day

    # Venta año acumulada — mismo periodo exacto (hasta dia_actual del mes_actual)
    venta_ano_26 = float(df26["TOTAL"].sum())
    venta_ano_25 = float(df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]["TOTAL"].sum())

    # Venta mes actual vs mismo periodo año anterior
    venta_mes_26   = float(df26[df26["MES"] == mes_actual]["TOTAL"].sum())
    venta_mes_25   = float(df25[(df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual)]["TOTAL"].sum())
    utilidad_mes   = float(df26[df26["MES"] == mes_actual]["UTILIDAD_BRUTA"].sum())
    mg_pct = round(utilidad_mes / venta_mes_26 * 100, 1) if venta_mes_26 > 0 else 0.0

    # Venta mes anterior — mismos dias (del 1 al dia_actual del mes anterior)
    mes_anterior  = mes_actual - 1 if mes_actual > 1 else 12
    venta_mes_ant = float(df26[
        (df26["MES"] == mes_anterior) & (df26["DIA"] <= dia_actual)
    ]["TOTAL"].sum())

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


# ══════════════════════════════════════════════════════
#  VENTAS POR MES — comparativo
# ══════════════════════════════════════════════════════
def get_ventas_por_mes():
    df25 = get_df_2025()
    df26 = get_df_2026()

    meses = []
    for mes in range(1, 13):
        v26 = float(df26[df26["MES"] == mes]["TOTAL"].sum())
        v25 = float(df25[df25["MES"] == mes]["TOTAL"].sum())
        meses.append({
            "mes":        mes,
            "mes_nombre": MESES.get(mes, ""),
            "actual":     round(v26, 0),
            "anterior":   round(v25, 0),
        })

    return {"meses": meses, "ano_actual": 2026, "ano_anterior": 2025}


# ══════════════════════════════════════════════════════
#  VENTAS POR SUCURSAL
# ══════════════════════════════════════════════════════
def get_ventas_por_sucursal():
    df25 = get_df_2025()
    df26 = get_df_2026()
    fecha_datos = _fecha_datos()
    mes_actual  = fecha_datos.month
    dia_actual  = fecha_datos.day

    # Todas las sucursales logicas que aparecen en los datos
    sucursales = df26["SUCURSAL_LOGICA"].dropna().unique().tolist()
    # Ordenar segun orden canonico, el resto al final
    orden = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    sucursales.sort(key=lambda s: orden.get(s, 99))

    resultado = []
    for suc in sucursales:
        s26 = df26[df26["SUCURSAL_LOGICA"] == suc]
        s25 = df25[df25["SUCURSAL_LOGICA"] == suc]

        v_ano_26 = float(s26["TOTAL"].sum())
        v_ano_25 = float(s25[
            (s25["MES"] < mes_actual) |
            ((s25["MES"] == mes_actual) & (s25["DIA"] <= dia_actual))
        ]["TOTAL"].sum())

        mes_26   = s26[s26["MES"] == mes_actual]
        mes_25   = s25[(s25["MES"] == mes_actual) & (s25["DIA"] <= dia_actual)]

        v_mes_26 = float(mes_26["TOTAL"].sum())
        v_mes_25 = float(mes_25["TOTAL"].sum())
        util_mes = float(mes_26["UTILIDAD_BRUTA"].sum())
        mg_mes   = round(util_mes / v_mes_26 * 100, 1) if v_mes_26 > 0 else 0.0

        resultado.append({
            "sucursal":       suc,
            "v_ano_actual":   round(v_ano_26, 0),
            "v_ano_anterior": round(v_ano_25, 0),
            "var_ano":        var_pct(v_ano_26, v_ano_25),
            "v_mes_actual":   round(v_mes_26, 0),
            "v_mes_anterior": round(v_mes_25, 0),
            "var_mes":        var_pct(v_mes_26, v_mes_25),
            "utilidad_mes":   round(util_mes, 0),
            "mg_mes":         mg_mes,
        })

    return {
        "sucursales":   resultado,
        "ano_actual":   2026,
        "ano_anterior": 2025,
        "mes_nombre":   MESES.get(mes_actual, ""),
    }


# ══════════════════════════════════════════════════════
#  FERIADOS CHILENOS 2025-2026
# ══════════════════════════════════════════════════════
FERIADOS_CL = {
    # 2025
    date(2025,  1,  1),  # Año Nuevo
    date(2025,  4, 18),  # Viernes Santo
    date(2025,  4, 19),  # Sábado Santo
    date(2025,  5,  1),  # Día del Trabajo
    date(2025,  5, 21),  # Glorias Navales
    date(2025,  7, 16),  # Virgen del Carmen
    date(2025,  8, 15),  # Asunción de la Virgen
    date(2025,  9, 18),  # Independencia Nacional
    date(2025,  9, 19),  # Glorias del Ejército
    date(2025, 10, 31),  # Iglesias Evangélicas
    date(2025, 11,  1),  # Todos los Santos
    date(2025, 12,  8),  # Inmaculada Concepción
    date(2025, 12, 25),  # Navidad
    # 2026
    date(2026,  1,  1),  # Año Nuevo
    date(2026,  4,  3),  # Viernes Santo
    date(2026,  4,  4),  # Sábado Santo
    date(2026,  5,  1),  # Día del Trabajo
    date(2026,  5, 21),  # Glorias Navales
    date(2026,  6, 29),  # San Pedro y San Pablo
    date(2026,  7, 16),  # Virgen del Carmen    date(2026,  8, 15),  # Asuncion de la Virgen
    date(2026,  9, 18),  # Independencia Nacional
    date(2026,  9, 19),  # Glorias del Ejercito
    date(2026, 10, 12),  # Dia de la Raza
    date(2026, 10, 31),  # Iglesias Evangelicas
    date(2026, 11,  1),  # Todos los Santos
    date(2026, 12,  8),  # Inmaculada Concepcion
    date(2026, 12, 25),  # Navidad
}


# ══════════════════════════════════════════════════════
#  DIAS HABILES (lunes a viernes, excluyendo feriados CL)
# ══════════════════════════════════════════════════════
def _dias_habiles_mes(ano, mes):
    """Total de dias habiles del mes (L-V, sin feriados)."""
    _, ultimo_dia = calendar.monthrange(ano, mes)
    return sum(
        1 for d in range(1, ultimo_dia + 1)
        if date(ano, mes, d).weekday() < 5
        and date(ano, mes, d) not in FERIADOS_CL
    )


def _dias_habiles_hasta(ano, mes, dia):
    """Dias habiles desde el dia 1 hasta 'dia' inclusive (L-V, sin feriados)."""
    return sum(
        1 for d in range(1, dia + 1)
        if date(ano, mes, d).weekday() < 5
        and date(ano, mes, d) not in FERIADOS_CL
    )


# ══════════════════════════════════════════════════════
#  FILTROS PARA PROYECCION
# ══════════════════════════════════════════════════════
def get_filtros_proyeccion():
    df26 = get_df_2026()
    orden = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    sucursales = sorted(
        df26["SUCURSAL_LOGICA"].dropna().unique().tolist(),
        key=lambda s: orden.get(s, 99)
    )
    return {"sucursales": sucursales}


# ══════════════════════════════════════════════════════
#  PROYECCION DE VENTAS
# ══════════════════════════════════════════════════════
def _aplicar_filtros_comunes(df26, df25, filtros, col_tv="TIPO_VENTA"):
    """Aplica filtros de sucursal y tipo de venta a ambos dataframes."""
    f = filtros or {}
    suc = f.get("sucursal", "todas")
    tv  = f.get("tipo_venta", "todas")

    if suc and suc != "todas":
        df26 = df26[df26["SUCURSAL_LOGICA"] == suc]
        df25 = df25[df25["SUCURSAL_LOGICA"] == suc]

    if tv and tv != "todas" and col_tv in df26.columns:
        df26 = df26[df26[col_tv] == tv]
        df25 = df25[df25[col_tv] == tv]

    return df26, df25


def get_proyeccion(filtros=None):
    df25_raw = get_df_2025()
    df26_raw = get_df_2026()
    fecha_datos = _fecha_datos()
    mes_actual  = fecha_datos.month
    dia_actual  = fecha_datos.day

    df26, df25 = _aplicar_filtros_comunes(df26_raw, df25_raw, filtros)

    mes_anterior = mes_actual - 1 if mes_actual > 1 else 12

    v_ano_26 = float(df26["TOTAL"].sum())
    v_ano_25 = float(df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]["TOTAL"].sum())
    v_mes_26 = float(df26[df26["MES"] == mes_actual]["TOTAL"].sum())
    v_mes_25 = float(df25[(df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual)]["TOTAL"].sum())
    v_mes_ant = float(df26[(df26["MES"] == mes_anterior) & (df26["DIA"] <= dia_actual)]["TOTAL"].sum())

    dh_total        = _dias_habiles_mes(2026, mes_actual)
    dh_transcurridos = _dias_habiles_hasta(2026, mes_actual, dia_actual)

    # Proyeccion lineal: vta_acum_ytd * (365 / dia_del_ano)
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

    # Proyeccion lineal del mes: vta_mes * (dh_total / dh_transcurridos)
    factor_mes = dh_total / dh_transcurridos if dh_transcurridos > 0 else 1.0

    # Agrupar por sucursal logica + vendedor rpt
    df_mes = df26[df26["MES"] == mes_actual]
    df_ant = df25[(df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual)]

    # Agregar venta, margen y conteo de documentos
    agg_mes = df_mes.groupby(["SUCURSAL_LOGICA", "VENDEDOR_RPT"]).agg(
        vta_mes=("TOTAL",         "sum"),
        mg_mes =("UTILIDAD_BRUTA","sum"),
        nro_docs=("TOTAL",        "count"),
    ).reset_index()

    agg_ant = df_ant.groupby(["SUCURSAL_LOGICA", "VENDEDOR_RPT"]).agg(
        vta_ant=("TOTAL","sum"),
    ).reset_index()

    # Combinar
    merged = agg_mes.merge(agg_ant, on=["SUCURSAL_LOGICA","VENDEDOR_RPT"], how="outer")
    merged = merged.fillna(0)

    ne_montos = _leer_ne_x_facturar()

    filas = []
    for _, row in merged.iterrows():
        suc     = row["SUCURSAL_LOGICA"]
        vend    = row["VENDEDOR_RPT"]
        vta     = float(row.get("vta_mes", 0))
        mg      = float(row.get("mg_mes",  0))
        docs    = int(row.get("nro_docs", 0))
        vta_ant = float(row.get("vta_ant", 0))
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

    # Proyeccion total del mes en kpis
    total_proy_mes = round(v_mes_26 * factor_mes, 0)
    kpis["proy_lineal"] = total_proy_mes

    orden_suc = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    filas.sort(key=lambda x: (
        orden_suc.get(x["sucursal"], 99),
        1 if x["is_otros"] else 0,
        -x["vta_mes"],
    ))

    return {"kpis": kpis, "filas": filas}


# ══════════════════════════════════════════════════════
#  SEGUIMIENTO DE METAS
# ══════════════════════════════════════════════════════
def _get_metas_df():
    """Lee metas.xlsx (metas mensuales por sucursal y vendedor)."""
    ruta = os.path.join(BASE_DIR, "data", "metas.xlsx")
    if not os.path.exists(ruta):
        return pd.DataFrame(columns=["ANO","MES","SUCURSAL","VENDEDOR","META"])
    df = pd.read_excel(ruta)
    df["META"] = pd.to_numeric(df["META"], errors="coerce").fillna(0)
    return df


def get_seguimiento_metas(filtros=None):
    df25_raw = get_df_2025()
    df26_raw = get_df_2026()
    fecha_datos = _fecha_datos()
    mes_actual  = fecha_datos.month
    dia_actual  = fecha_datos.day

    df26, df25 = _aplicar_filtros_comunes(df26_raw, df25_raw, filtros)

    mes_anterior  = mes_actual - 1 if mes_actual > 1 else 12
    dh_total        = _dias_habiles_mes(2026, mes_actual)
    dh_transcurridos = _dias_habiles_hasta(2026, mes_actual, dia_actual)
    factor_dias = dh_transcurridos / dh_total if dh_total > 0 else 0

    v_ano_26 = float(df26["TOTAL"].sum())
    v_ano_25 = float(df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]["TOTAL"].sum())
    v_mes_26 = float(df26[df26["MES"] == mes_actual]["TOTAL"].sum())
    v_mes_25 = float(df25[(df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual)]["TOTAL"].sum())
    v_mes_ant = float(df26[(df26["MES"] == mes_anterior) & (df26["DIA"] <= dia_actual)]["TOTAL"].sum())

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

    # Metas del mes actual
    metas_df = _get_metas_df()
    metas_mes = metas_df[(metas_df["ANO"] == 2026) & (metas_df["MES"] == mes_actual)]
    meta_dic = {
        (str(r["SUCURSAL"]).strip(), str(r["VENDEDOR"]).strip()): float(r["META"])
        for _, r in metas_mes.iterrows()
    }

    # Ventas del mes por sucursal+vendedor
    df_mes = df26[df26["MES"] == mes_actual]
    grp = df_mes.groupby(["SUCURSAL_LOGICA", "VENDEDOR_RPT"])["TOTAL"].sum()

    # Construir filas
    # Incluir vendedores con meta aunque no tengan venta
    claves = set(grp.index) | set(meta_dic.keys())

    filas = {}
    for (suc, vend) in claves:
        vta = float(grp.get((suc, vend), 0.0))
        meta = meta_dic.get((suc, vend), 0.0)
        meta_acum = round(meta * factor_dias, 0) if meta > 0 else 0
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


# ══════════════════════════════════════════════════════
#  SEGUIMIENTO DE PRESUPUESTO
# ══════════════════════════════════════════════════════
def _get_ppto_df():
    """Lee presupuesto.xlsx (presupuesto anual por sucursal)."""
    ruta = os.path.join(BASE_DIR, "data", "presupuesto.xlsx")
    if not os.path.exists(ruta):
        return pd.DataFrame(columns=["SUCURSAL","PRESUPUESTO_ANUAL"])
    df = pd.read_excel(ruta)
    df["PRESUPUESTO_ANUAL"] = pd.to_numeric(df["PRESUPUESTO_ANUAL"], errors="coerce").fillna(0)
    return df


def get_seguimiento_ppto():
    df25 = get_df_2025()
    df26 = get_df_2026()
    fecha_datos = _fecha_datos()
    mes_actual  = fecha_datos.month
    dia_actual  = fecha_datos.day

    inicio_ano   = date(2026, 1, 1)
    doy          = (fecha_datos - inicio_ano).days + 1
    factor_anual = doy / 365.0

    mes_anterior = mes_actual - 1 if mes_actual > 1 else 12
    v_ano_26 = float(df26["TOTAL"].sum())
    v_ano_25 = float(df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]["TOTAL"].sum())
    v_mes_26 = float(df26[df26["MES"] == mes_actual]["TOTAL"].sum())
    v_mes_25 = float(df25[(df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual)]["TOTAL"].sum())
    v_mes_ant = float(df26[(df26["MES"] == mes_anterior) & (df26["DIA"] <= dia_actual)]["TOTAL"].sum())

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

    ppto_df  = _get_ppto_df()
    ppto_dic = {str(r["SUCURSAL"]).strip(): float(r["PRESUPUESTO_ANUAL"])
                for _, r in ppto_df.iterrows()}

    orden_suc  = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    sucursales = sorted(
        df26["SUCURSAL_LOGICA"].dropna().unique().tolist(),
        key=lambda s: orden_suc.get(s, 99)
    )

    tabla_acum = []
    for suc in sucursales:
        acum_26 = float(df26[df26["SUCURSAL_LOGICA"] == suc]["TOTAL"].sum())
        s25 = df25[df25["SUCURSAL_LOGICA"] == suc]
        acum_25 = float(s25[
            (s25["MES"] < mes_actual) |
            ((s25["MES"] == mes_actual) & (s25["DIA"] <= dia_actual))
        ]["TOTAL"].sum())
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

    def meses_suc(df, suc):
        sub = df[df["SUCURSAL_LOGICA"] == suc]
        return [round(float(sub[sub["MES"] == m]["TOTAL"].sum()), 0) for m in range(1, 13)]

    mensual_25 = {s: meses_suc(df25, s) for s in sucursales}
    mensual_26 = {s: meses_suc(df26, s) for s in sucursales}
    totales_25 = [round(float(df25[df25["MES"] == m]["TOTAL"].sum()), 0) for m in range(1, 13)]
    totales_26 = [round(float(df26[df26["MES"] == m]["TOTAL"].sum()), 0) for m in range(1, 13)]
    ppto_anual_total = sum(ppto_dic.values())
    ppto_mensual     = [round(ppto_anual_total / 12, 0)] * 12

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


# ══════════════════════════════════════════════════════
#  VTA ACUMULADA — análisis multidimensional
# ══════════════════════════════════════════════════════

# Dimensiones de agrupación disponibles → columna en el Excel
CATEGORIAS_VTA = {
    "marca":       ("Marca",                "MARCA"),
    "familia":     ("Familia",              "FAMILIA"),
    "subfamilia":  ("Subfamilia",           "SUBFAMILIA"),
    "grupo":       ("Grupo",                "GRUPO"),
    "descripcion": ("Descripción Producto", "DESCRIPCION"),
    "cliente":     ("Cliente",              "NOMBRE_CLIENTE"),
    "vendedor":    ("Vendedor",             "VENDEDOR"),
    "sucursal":    ("Sucursal",             "SUCURSAL_LOGICA"),
    "tipo_venta":  ("Tipo Venta",           "TIPO_VENTA"),
    "procedencia": ("Procedencia",          "PROCEDENCIA"),
    "cond_pago":   ("Condición Pago",       "COND_PAGO"),
    "proveedor":   ("Proveedor",            "PROVEEDOR_POR_DEFECTO"),
}

# Columnas de filtro lateral (clave → columna)
FILTROS_VTA = {
    "sucursal":   "SUCURSAL_LOGICA",
    "vendedor":   "VENDEDOR",
    "familia":    "FAMILIA",
    "subfamilia": "SUBFAMILIA",
    "tipo_venta": "TIPO_VENTA",
    "procedencia":"PROCEDENCIA",
    "cond_pago":  "COND_PAGO",
}


def get_filtros_vta_acum():
    """Devuelve dimensiones disponibles y valores para filtros laterales."""
    df = get_df_2026()

    # Verificar qué columnas existen realmente
    cats_ok = {
        k: v for k, v in CATEGORIAS_VTA.items()
        if v[1] in df.columns or v[1] == "SUCURSAL_LOGICA"
    }

    filtros_ok = {}
    for fk, col in FILTROS_VTA.items():
        if col in df.columns:
            vals = sorted([str(v) for v in df[col].dropna().unique() if str(v).strip()])
            filtros_ok[fk] = vals

    # Sucursal usa SUCURSAL_LOGICA (ya computada)
    orden = {s: i for i, s in enumerate(ORDEN_SUCURSALES)}
    if "SUCURSAL_LOGICA" in df.columns:
        filtros_ok["sucursal"] = sorted(
            df["SUCURSAL_LOGICA"].dropna().unique().tolist(),
            key=lambda s: orden.get(s, 99)
        )

    return {
        "categorias": {k: v[0] for k, v in cats_ok.items()},
        "filtros":    filtros_ok,
    }


def get_vta_acum(filtros=None):
    """
    Retorna análisis acumulado agrupado por la dimensión elegida.
    filtros: {
        categoria: 'marca' | 'familia' | ... (default: 'marca')
        sucursal, vendedor, familia, subfamilia, tipo_venta,
        procedencia, cond_pago  → valores de filtro
    }
    """
    f           = filtros or {}
    df26_raw    = get_df_2026()
    df25_raw    = get_df_2025()
    fecha_datos = _fecha_datos()
    mes_actual  = fecha_datos.month
    dia_actual  = fecha_datos.day
    mes_anterior = mes_actual - 1 if mes_actual > 1 else 12

    # Meses transcurridos (para promedio y proyección)
    inicio_ano    = date(2026, 1, 1)
    doy           = (fecha_datos - inicio_ano).days + 1
    meses_elapsed = doy * 12 / 365.0  # fracción de meses

    # Columna de agrupación
    categoria = f.get("categoria", "marca")
    _, col_grupo = CATEGORIAS_VTA.get(categoria, ("Marca", "MARCA"))
    if col_grupo not in df26_raw.columns and col_grupo != "SUCURSAL_LOGICA":
        col_grupo = "MARCA"   # fallback seguro

    # Aplicar filtros laterales
    df26 = df26_raw.copy()
    df25 = df25_raw.copy()
    for fk, col in FILTROS_VTA.items():
        val = f.get(fk)
        if val and val not in ("todas", "todos", ""):
            if col in df26.columns:
                df26 = df26[df26[col].astype(str) == str(val)]
                df25 = df25[df25[col].astype(str) == str(val)]

    # Subconjuntos por período
    df25_ytd = df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]

    # ── KPIs generales ─────────────────────────────────
    v_ano_26  = float(df26["TOTAL"].sum())
    v_ano_25  = float(df25_ytd["TOTAL"].sum())
    v_mes_26  = float(df26[df26["MES"] == mes_actual]["TOTAL"].sum())
    v_mes_25  = float(df25[(df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual)]["TOTAL"].sum())
    v_mes_ant = float(df26[(df26["MES"] == mes_anterior) & (df26["DIA"] <= dia_actual)]["TOTAL"].sum())

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

    # ── Agrupaciones ───────────────────────────────────
    grp26      = df26.groupby(col_grupo)["TOTAL"].sum()
    grp25_ytd  = df25_ytd.groupby(col_grupo)["TOTAL"].sum() if col_grupo in df25_ytd.columns else pd.Series(dtype=float)
    grp25_full = df25.groupby(col_grupo)["TOTAL"].sum()     if col_grupo in df25.columns     else pd.Series(dtype=float)

    total_26 = float(grp26.sum())

    filas = []
    for cat in grp26.sort_values(ascending=False).index:
        vta       = float(grp26.get(cat, 0))
        vta25     = float(grp25_ytd.get(cat, 0))
        vta25_fy  = float(grp25_full.get(cat, 0))
        prom      = round(vta    / meses_elapsed, 0) if meses_elapsed > 0 else 0
        prom25    = round(vta25  / meses_elapsed, 0) if meses_elapsed > 0 else 0
        proy      = round(vta    / meses_elapsed * 12, 0) if meses_elapsed > 0 else 0
        filas.append({
            "categoria":          str(cat),
            "vta_acum":           round(vta,     0),
            "vta_acum_ant":       round(vta25,   0),
            "pct_crec":           var_pct(vta, vta25),
            "pct_mkt_share":      round(vta / total_26 * 100, 1) if total_26 > 0 else 0,
            "promedio_venta":     prom,
            "promedio_venta_ant": prom25,
            "proyeccion":         proy,
            "vta_cierre_ant":     round(vta25_fy, 0),
        })

    # Fila total
    v25_ytd_tot  = float(grp25_ytd.sum())
    v25_full_tot = float(grp25_full.sum())
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
        "kpis":           kpis,
        "total":          total,
        "filas":          filas,
        "categoria":      categoria,
        "categoria_label": CATEGORIAS_VTA.get(categoria, ("Marca",))[0],
    }


# ══════════════════════════════════════════════════════
#  Venta Mensual + Margen Acumulado por categoria
# ══════════════════════════════════════════════════════
def get_vta_mes_mg_acum(filtros=None):
    """
    Retorna:
      kpis          -> 3 grupos KPI
      meses         -> lista de numeros de mes con datos [1,2,...,7]
      nombres_meses -> ["ene","feb",...,"jul"]
      total_mensual -> fila total con ventas por mes
      filas_mensual -> filas por categoria con ventas por mes
      total_acum    -> fila total con vta_acum, mg_acum, pct_mg
      filas_acum    -> filas por categoria con vta_acum, mg_acum, pct_mg
      categoria_label -> label legible de la dimension
    """
    f           = filtros or {}
    df26_raw    = get_df_2026()
    df25_raw    = get_df_2025()
    fecha_datos = _fecha_datos()
    mes_actual  = fecha_datos.month
    dia_actual  = fecha_datos.day
    mes_anterior = mes_actual - 1 if mes_actual > 1 else 12

    inicio_ano    = date(2026, 1, 1)
    doy           = (fecha_datos - inicio_ano).days + 1
    meses_elapsed = doy * 12 / 365.0

    # Columna de agrupacion
    categoria = f.get("categoria", "marca")
    _, col_grupo = CATEGORIAS_VTA.get(categoria, ("Marca", "MARCA"))
    if col_grupo not in df26_raw.columns and col_grupo != "SUCURSAL_LOGICA":
        col_grupo = "MARCA"

    # Aplicar filtros laterales (igual que get_vta_acum)
    df26 = df26_raw.copy()
    df25 = df25_raw.copy()
    for fk, col in FILTROS_VTA.items():
        val = f.get(fk)
        if val and val not in ("todas", "todos", ""):
            if col in df26.columns:
                df26 = df26[df26[col].astype(str) == str(val)]
                df25 = df25[df25[col].astype(str) == str(val)]

    # 2025 YTD (mismo periodo)
    df25_ytd = df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]

    # ── KPIs ───────────────────────────────────────────
    v_ano_26  = float(df26["TOTAL"].sum())
    v_ano_25  = float(df25_ytd["TOTAL"].sum())
    v_mes_26  = float(df26[df26["MES"] == mes_actual]["TOTAL"].sum())
    v_mes_25  = float(df25[(df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual)]["TOTAL"].sum())
    if mes_anterior >= 1:
        v_mes_ant = float(df26[df26["MES"] == mes_anterior]["TOTAL"].sum())
    else:
        v_mes_ant = 0.0

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

    # ── Tabla mensual (pivot) ───────────────────────────
    meses_con_datos = sorted(df26["MES"].unique().tolist())
    nombres_meses   = [MESES.get(m, str(m)) for m in meses_con_datos]

    # Totales por mes (fila Total general)
    total_por_mes = {}
    for m in meses_con_datos:
        total_por_mes[m] = round(float(df26[df26["MES"] == m]["TOTAL"].sum()), 0)

    # Agrupaciones por categoria
    grp_mes   = df26.groupby([col_grupo, "MES"])["TOTAL"].sum()
    grp_total = df26.groupby(col_grupo)["TOTAL"].sum().sort_values(ascending=False)

    filas_mensual = []
    for cat in grp_total.index:
        fila = {"categoria": str(cat)}
        for m in meses_con_datos:
            try:
                fila[f"m{m}"] = round(float(grp_mes.get((cat, m), 0)), 0)
            except Exception:
                fila[f"m{m}"] = 0.0
        filas_mensual.append(fila)

    total_mensual = {"categoria": "Total general"}
    for m in meses_con_datos:
        total_mensual[f"m{m}"] = total_por_mes.get(m, 0)

    # ── Tabla acumulada con margen ──────────────────────
    grp_acum = df26.groupby(col_grupo).agg(
        vta_acum=("TOTAL",          "sum"),
        mg_acum =("UTILIDAD_BRUTA", "sum"),
    ).reset_index()
    grp_acum = grp_acum.sort_values("vta_acum", ascending=False)

    filas_acum = []
    for _, row in grp_acum.iterrows():
        vta = float(row["vta_acum"])
        mg  = float(row["mg_acum"])
        pct = round(mg / vta * 100, 1) if vta else 0.0
        filas_acum.append({
            "categoria": str(row[col_grupo]),
            "vta_acum":  round(vta, 0),
            "mg_acum":   round(mg,  0),
            "pct_mg":    pct,
        })

    tot_vta = float(df26["TOTAL"].sum())
    tot_mg  = float(df26["UTILIDAD_BRUTA"].sum())
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


# ══════════════════════════════════════════════════════
#  Venta + Margen por mes, con sub-columnas por mes
# ══════════════════════════════════════════════════════
def get_vta_mg_mensual(filtros=None):
    """
    Igual que get_vta_mes_mg_acum pero cada mes tiene tres valores:
      vta_{m}, mg_{m}, pct_{m}
    para poder mostrar Vta Acum | Mg acum | % Margen por cada mes.
    """
    f           = filtros or {}
    df26_raw    = get_df_2026()
    df25_raw    = get_df_2025()
    fecha_datos = _fecha_datos()
    mes_actual  = fecha_datos.month
    dia_actual  = fecha_datos.day
    mes_anterior = mes_actual - 1 if mes_actual > 1 else 12

    inicio_ano    = date(2026, 1, 1)
    doy           = (fecha_datos - inicio_ano).days + 1

    # Columna de agrupacion
    categoria = f.get("categoria", "marca")
    _, col_grupo = CATEGORIAS_VTA.get(categoria, ("Marca", "MARCA"))
    if col_grupo not in df26_raw.columns and col_grupo != "SUCURSAL_LOGICA":
        col_grupo = "MARCA"

    # Filtros laterales
    df26 = df26_raw.copy()
    df25 = df25_raw.copy()
    for fk, col in FILTROS_VTA.items():
        val = f.get(fk)
        if val and val not in ("todas", "todos", ""):
            if col in df26.columns:
                df26 = df26[df26[col].astype(str) == str(val)]
                df25 = df25[df25[col].astype(str) == str(val)]

    # 2025 YTD
    df25_ytd = df25[
        (df25["MES"] < mes_actual) |
        ((df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual))
    ]

    # ── KPIs (mismo esquema que vta_mes_mg_acum) ───────
    v_ano_26  = float(df26["TOTAL"].sum())
    v_ano_25  = float(df25_ytd["TOTAL"].sum())
    v_mes_26  = float(df26[df26["MES"] == mes_actual]["TOTAL"].sum())
    v_mes_25  = float(df25[(df25["MES"] == mes_actual) & (df25["DIA"] <= dia_actual)]["TOTAL"].sum())
    if mes_anterior >= 1:
        v_mes_ant = float(df26[df26["MES"] == mes_anterior]["TOTAL"].sum())
    else:
        v_mes_ant = 0.0

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

    # ── Tabla mensual con vta + mg + pct por mes ────────
    meses_con_datos = sorted(df26["MES"].unique().tolist())
    nombres_meses   = [MESES.get(m, str(m)) for m in meses_con_datos]

    # Agrupacion doble: categoria x mes  -> vta y mg
    grp_mes = df26.groupby([col_grupo, "MES"]).agg(
        vta=("TOTAL",          "sum"),
        mg =("UTILIDAD_BRUTA", "sum"),
    )
    grp_total = df26.groupby(col_grupo).agg(
        vta=("TOTAL",          "sum"),
        mg =("UTILIDAD_BRUTA", "sum"),
    ).sort_values("vta", ascending=False)

    # Totales globales por mes
    tot_mes = df26.groupby("MES").agg(
        vta=("TOTAL",          "sum"),
        mg =("UTILIDAD_BRUTA", "sum"),
    )

    filas_mensual = []
    for cat in grp_total.index:
        fila = {"categoria": str(cat)}
        for m in meses_con_datos:
            try:
                v = float(grp_mes.loc[(cat, m), "vta"]) if (cat, m) in grp_mes.index else 0.0
                g = float(grp_mes.loc[(cat, m), "mg"])  if (cat, m) in grp_mes.index else 0.0
            except Exception:
                v, g = 0.0, 0.0
            fila[f"vta_{m}"] = round(v, 0)
            fila[f"mg_{m}"]  = round(g, 0)
            fila[f"pct_{m}"] = round(g / v * 100, 1) if v else 0.0
        filas_mensual.append(fila)

    total_mensual = {"categoria": "Total general"}
    for m in meses_con_datos:
        v = float(tot_mes.loc[m, "vta"]) if m in tot_mes.index else 0.0
        g = float(tot_mes.loc[m, "mg"])  if m in tot_mes.index else 0.0
        total_mensual[f"vta_{m}"] = round(v, 0)
        total_mensual[f"mg_{m}"]  = round(g, 0)
        total_mensual[f"pct_{m}"] = round(g / v * 100, 1) if v else 0.0

    # ── Tabla acumulada ─────────────────────────────────
    tot_vta = float(df26["TOTAL"].sum())
    tot_mg  = float(df26["UTILIDAD_BRUTA"].sum())
    total_acum = {
        "categoria": "Total general",
        "vta_acum":  round(tot_vta, 0),
        "mg_acum":   round(tot_mg,  0),
        "pct_mg":    round(tot_mg / tot_vta * 100, 1) if tot_vta else 0.0,
    }

    filas_acum = []
    for cat in grp_total.index:
        v = float(grp_total.loc[cat, "vta"])
        g = float(grp_total.loc[cat, "mg"])
        filas_acum.append({
            "categoria": str(cat),
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
