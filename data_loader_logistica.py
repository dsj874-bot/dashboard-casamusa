"""
Logistica -- panel de despachos y retiros, leido EN VIVO desde la planilla
de Google Sheets que mantiene el equipo de logistica (pedido del usuario
2026-10-10: "son datos que se van actualizando siempre").

Se lee el export CSV de la hoja (por ahora el link es publico; cuando se
restrinja hay que pasar a una cuenta de servicio de Google). Para no bajar
la planilla una vez por cada grafico, el CSV se guarda en memoria del
proceso CACHE_SEGUNDOS: el panel hace una sola llamada y queda al minuto.

Columnas de la planilla que se usan: Fecha de ingreso, Fecha de Entrega,
Tipo de Operacion (Despacho/Retiro), Cliente / Proveedor (Nombre),
Comuna de Destino, Status, Cumplimiento (Cumplido/Incumplido/En proceso),
Clasificacion no cumplimiento, Comentario adicional, "Columna 6" (es la
sucursal de origen) y "Columna 13" (es el chofer).
"""
import io
import os
import re
import time
import urllib.request
from datetime import date, timedelta

import pandas as pd

SHEET_ID = os.environ.get("LOGISTICA_SHEET_ID", "19DjCzAJdpPEqLhFnzqdMn1tkWG9mnbUClrCJTj6JWiU")
SHEET_GID = os.environ.get("LOGISTICA_SHEET_GID", "1365889865")
CACHE_SEGUNDOS = 60
DIAS_ES = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves", 4: "Viernes", 5: "Sábado", 6: "Domingo"}
MESES_ES = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun", 7: "Jul", 8: "Ago",
            9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}
SUCURSALES = {"SI": "San Isidro", "MT": "Matta", "LC": "Las Condes", "CH": "Chicureo",
              "MR": "Manuel Rodríguez", "MP": "Maipú"}

_cache = {"t": 0.0, "df": None}


def _url_csv():
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={SHEET_GID}"


def _limpiar_cliente(nombre):
    """Junta las variantes de un mismo cliente (TEMPORA / TEMPORA S.A. /
    Tempora SpA): mayusculas, sin puntos ni sufijos de razon social."""
    if not isinstance(nombre, str) or not nombre.strip():
        return "(sin nombre)"
    n = re.sub(r"[.,]", " ", nombre.upper())
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(r"\b(S A|SA|SPA|LTDA|LIMITADA|EIRL|E I R L)$", "", n).strip()
    return n


def leer_planilla(forzar=False):
    """DataFrame normalizado de la planilla (con cache de CACHE_SEGUNDOS)."""
    ahora = time.time()
    if not forzar and _cache["df"] is not None and ahora - _cache["t"] < CACHE_SEGUNDOS:
        return _cache["df"], _cache["t"]
    req = urllib.request.Request(_url_csv(), headers={"User-Agent": "Musa360"})
    with urllib.request.urlopen(req, timeout=40) as r:
        contenido = r.read()
    df = pd.read_csv(io.BytesIO(contenido), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    col = lambda nombre: df[nombre] if nombre in df.columns else pd.Series([None] * len(df))
    out = pd.DataFrame({
        "fecha_ingreso": pd.to_datetime(col("Fecha de ingreso"), dayfirst=True, errors="coerce"),
        "fecha": pd.to_datetime(col("Fecha de Entrega"), dayfirst=True, errors="coerce").dt.normalize(),
        "tipo": col("Tipo de Operación").fillna("").str.strip(),
        "solicitud": col("Tipo de Solicitud").fillna("").str.strip(),
        "cliente": col("Cliente / Proveedor (Nombre)").map(_limpiar_cliente),
        "documento": col("OV / OC").fillna("").str.strip(),
        "comuna": col("Comuna de Destino").fillna("").str.strip().str.title(),
        "status": col("Status").fillna("").str.strip(),
        "cumplimiento": col("Cumplimiento").fillna("").str.strip(),
        "motivo": col("Clasificación no cumplimiento").fillna("").str.strip(),
        "comentario": col("Comentario adicional").fillna("").str.strip(),
        "sucursal": col("Columna 6").fillna("").str.strip().str.upper(),
        "chofer": col("Columna 13").fillna("").str.strip().str.title(),
    })
    # Tipo de operacion vacio: se deduce de la solicitud
    sin_tipo = out["tipo"].eq("")
    out.loc[sin_tipo & out["solicitud"].str.contains("Retiro", case=False), "tipo"] = "Retiro"
    out.loc[sin_tipo & out["solicitud"].str.contains("Despacho", case=False), "tipo"] = "Despacho"
    out = out[out["fecha"].notna()].copy()
    _cache.update({"t": ahora, "df": out})
    return out, ahora


def _pct(a, b):
    return round(a / b * 100, 1) if b else None


def get_panel_logistica(desde=None, hasta=None, sucursal=None, chofer=None, tipo=None, forzar=False):
    df, leido = leer_planilla(forzar)
    hoy = pd.Timestamp(date.today())
    total_planilla = len(df)

    opciones = {
        "sucursal": [{"codigo": s, "nombre": SUCURSALES.get(s, s)} for s in df["sucursal"].value_counts().index if s],
        "chofer": [c for c in df["chofer"].value_counts().index if c],
        "tipo": [t for t in ("Despacho", "Retiro") if (df["tipo"] == t).any()],
        "min_fecha": df["fecha"].min().date().isoformat() if len(df) else None,
        "max_fecha": df["fecha"].max().date().isoformat() if len(df) else None,
    }

    if desde:
        df = df[df["fecha"] >= pd.Timestamp(desde)]
    if hasta:
        df = df[df["fecha"] <= pd.Timestamp(hasta)]
    if sucursal:
        df = df[df["sucursal"].isin(sucursal)]
    if chofer:
        df = df[df["chofer"].isin(chofer)]
    if tipo:
        df = df[df["tipo"].isin(tipo)]

    cerr = df[df["cumplimiento"].isin(["Cumplido", "Incumplido"])].copy()
    cerr["ok"] = cerr["cumplimiento"].eq("Cumplido")
    inc = cerr[~cerr["ok"]]

    def agrupar(campo, orden=None, top=None):
        if cerr.empty:
            return []
        g = cerr.groupby(campo)["ok"].agg(["size", "sum"])
        g = g.sort_values("size", ascending=False)
        if top:
            g = g.head(top)
        filas = [{"nombre": k if k else "(sin dato)", "total": int(v["size"]), "cumplidos": int(v["sum"]),
                  "incumplidos": int(v["size"] - v["sum"]), "pct": _pct(v["sum"], v["size"])} for k, v in g.iterrows()]
        if orden:
            filas.sort(key=lambda f: orden.index(f["nombre"]) if f["nombre"] in orden else 99)
        return filas

    # Serie mensual
    mensual = []
    if not cerr.empty:
        cerr["mes"] = cerr["fecha"].dt.to_period("M")
        for per, g in cerr.groupby("mes"):
            mensual.append({"mes": f"{MESES_ES[per.month]} {str(per.year)[2:]}", "total": int(len(g)),
                            "despachos": int((g["tipo"] == "Despacho").sum()), "retiros": int((g["tipo"] == "Retiro").sum()),
                            "pct": _pct(g["ok"].sum(), len(g))})

    # Capacidad: cumplimiento segun movimientos del dia y por chofer
    capacidad, por_chofer_dia = [], []
    if not cerr.empty:
        dia = cerr.groupby("fecha").agg(n=("ok", "size"), fallas=("ok", lambda s: int((~s).sum())),
                                        choferes=("chofer", lambda s: s[(s != "") & (s != "Otro")].nunique()))
        tramos = [(0, 15, "Hasta 15"), (16, 20, "16 a 20"), (21, 25, "21 a 25"), (26, 10 ** 6, "Más de 25")]
        for a, b, etq in tramos:
            sel = dia[(dia["n"] >= a) & (dia["n"] <= b)]
            if len(sel):
                capacidad.append({"tramo": etq, "dias": int(len(sel)), "movimientos": int(sel["n"].sum()),
                                  "pct": _pct(sel["n"].sum() - sel["fallas"].sum(), sel["n"].sum())})
        dia["por_chofer"] = dia["n"] / dia["choferes"].where(dia["choferes"] > 0)
        for a, b, etq in [(0, 6, "Hasta 6"), (6.01, 8, "7 a 8"), (8.01, 10, "9 a 10"), (10.01, 10 ** 6, "Más de 10")]:
            sel = dia[(dia["por_chofer"] >= a) & (dia["por_chofer"] <= b)]
            if len(sel):
                por_chofer_dia.append({"tramo": etq, "dias": int(len(sel)), "movimientos": int(sel["n"].sum()),
                                       "pct": _pct(sel["n"].sum() - sel["fallas"].sum(), sel["n"].sum())})

    # Dias entre ingreso y entrega (despachos)
    plazo = []
    d = cerr[(cerr["tipo"] == "Despacho") & cerr["fecha_ingreso"].notna()]
    if len(d):
        dias = (d["fecha"] - d["fecha_ingreso"].dt.normalize()).dt.days.clip(lower=0)
        for etq, cond in (("Mismo día", dias == 0), ("Día siguiente", dias == 1), ("2 días", dias == 2), ("3 o más días", dias >= 3)):
            plazo.append({"plazo": etq, "pct": _pct(int(cond.sum()), len(dias)), "n": int(cond.sum())})

    # Pendientes: en proceso / sin cerrar con fecha vencida, y proximos dias
    abiertos = df[~df["cumplimiento"].isin(["Cumplido", "Incumplido"])]
    vencidos = abiertos[abiertos["fecha"] < hoy].sort_values("fecha")
    proximos = df[(df["fecha"] >= hoy) & (df["fecha"] <= hoy + timedelta(days=7))]
    fila_mov = lambda r: {"fecha": r["fecha"].date().isoformat(), "tipo": r["tipo"], "cliente": r["cliente"],
                          "documento": r["documento"], "comuna": r["comuna"], "chofer": r["chofer"], "status": r["status"],
                          "sucursal": r["sucursal"]}

    # Calidad de datos
    calidad = {
        "incumplidos_sin_motivo": int((inc["motivo"] == "").sum()),
        "vencidos_sin_cerrar": int(len(vencidos)),
        "sin_chofer": int((df["chofer"] == "").sum()),
        "sin_sucursal": int((df["sucursal"] == "").sum()),
    }

    ultimos60 = cerr[cerr["fecha"] > cerr["fecha"].max() - timedelta(days=60)] if len(cerr) else cerr
    return {
        "leido_en": time.strftime("%d-%m-%Y %H:%M", time.localtime(leido)),
        "total_planilla": total_planilla,
        "opciones": opciones,
        "kpis": {
            "cerrados": int(len(cerr)),
            "pct": _pct(cerr["ok"].sum(), len(cerr)) if len(cerr) else None,
            "pct_60d": _pct(ultimos60["ok"].sum(), len(ultimos60)) if len(ultimos60) else None,
            "despachos": int((cerr["tipo"] == "Despacho").sum()),
            "retiros": int((cerr["tipo"] == "Retiro").sum()),
            "pct_despachos": _pct(cerr[cerr["tipo"] == "Despacho"]["ok"].sum(), (cerr["tipo"] == "Despacho").sum()),
            "pct_retiros": _pct(cerr[cerr["tipo"] == "Retiro"]["ok"].sum(), (cerr["tipo"] == "Retiro").sum()),
            "incumplidos": int(len(inc)),
            "promedio_diario": round(len(cerr) / cerr["fecha"].nunique(), 1) if len(cerr) else None,
            "vencidos": int(len(vencidos)),
            "proximos_7d": int(len(proximos)),
        },
        "mensual": mensual,
        "capacidad": capacidad,
        "por_chofer_dia": por_chofer_dia,
        "choferes": agrupar("chofer"),
        "sucursales": [dict(f, nombre=SUCURSALES.get(f["nombre"], f["nombre"])) for f in agrupar("sucursal")],
        "dias": [dict(f, nombre=f["nombre"]) for f in (
            sorted([{"nombre": DIAS_ES[k], "orden": k, "total": int(len(g)), "pct": _pct(g["ok"].sum(), len(g))}
                    for k, g in cerr.groupby(cerr["fecha"].dt.dayofweek)], key=lambda x: x["orden"]) if len(cerr) else [])],
        "comunas": agrupar("comuna", top=15),
        "clientes": agrupar("cliente", top=15),
        "motivos": [{"motivo": k or "(sin clasificar)", "n": int(v)} for k, v in inc["motivo"].value_counts().items()],
        "comentarios": [{"comentario": k, "n": int(v)} for k, v in
                        inc[inc["comentario"] != ""]["comentario"].str.lower().value_counts().head(10).items()],
        "plazo": plazo,
        "vencidos": [fila_mov(r) for _, r in vencidos.head(50).iterrows()],
        "proximos": [fila_mov(r) for _, r in proximos.sort_values("fecha").head(100).iterrows()],
        "calidad": calidad,
    }
