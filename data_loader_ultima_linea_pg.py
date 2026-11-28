"""
Área Última Línea (2026-11-25). Acceso: USUARIOS_ULTIMA_LINEA en app.py (toda la gerencia).

Estado de resultado por sucursal/canal con las columnas del Gerente General
(archivo "Ppto 2026 proy CM 261124.xlsx", hoja Ppto26):
  Venta | Margen $ | Rem. directas | Rem. indirectas | Arriendo y GC |
  Gastos directos canal | Resto de gastos | Total gasto | Última línea | % venta
+ punto de equilibrio y holgura.

Reglas acordadas con el usuario:
- Venta y margen REALES de Musa360 (v_ventas, margen ERP = utilidad_bruta).
  Digital = canal digital + casamusa.cl + venta asistida + marketplaces.
- OF (rebates y servicios facturados a proveedores, ej. 30,4 MM de Legrand en ene-2026)
  va como línea aparte "Rebates y otros": suma venta y margen (puro) al total de la
  empresa, sin gastos propios y sin cargar indirectas (decisión del usuario 2026-11-26:
  "entra como facturación y pago a Casa Musa, es margen puro"). No se proyecta a meses
  futuros ni se escala por días hábiles: un rebate no es ritmo de venta.
- Sueldos y gastos: los montos mensuales del GG tal cual (son reales).
  Arriendo/GC y resto de gastos son propios de cada sucursal.
- Solo las remuneraciones indirectas (gerentes y administrativos) se reparten,
  y se reparten por MARGEN (el GG las repartía por venta; se deja como opción).
- Punto de equilibrio = (directas + arriendo + gastos canal + resto)
                        / (margen % x (1 - indirectas/margen total)).
- Holgura = venta / punto de equilibrio - 1.

Los gastos van fijos en este archivo (GASTOS_2026 / PPTO_2027): si el GG los
cambia, editarlos aquí. Pendiente: pasarlos a una tabla editable.
NE x Facturar se suman al cierre proyectado del mes en curso y del año, con el
margen ERP del año de cada sucursal (no el 20% fijo de Proyección).
"""
import datetime as dt
from collections import defaultdict

import db

CANALES = [
    ("MT", "Matta"), ("LC", "Las Condes"), ("MR", "Manuel Rodríguez"), ("SE", "Santiago Empresas"),
    ("CMD", "CMD"), ("CH", "Chicureo"), ("MP", "Maipú"), ("QL", "Quilín"), ("DIG", "Digital"),
    ("OF", "Rebates y otros (OF)"),
]
ESPECIAL = "OF"  # sin gastos, fuera del reparto de indirectas y del equilibrio por sucursal
NOMBRE = dict(CANALES)

# Gastos mensuales del GG, ritmo 2026 (millones $): directas, indirectas, arriendo y GC,
# gastos directos del canal, resto de gastos. Fuente: Ppto26 filas 47-61.
GASTOS_2026 = {
    "MT":  (12.030040, 7.009072, 2.638838, 0.0,      12.946314),
    "LC":  (8.761909,  5.263223, 5.545877, 0.0,      9.721591),
    "MR":  (8.379007,  4.713793, 3.314639, 0.0,      8.706752),
    "SE":  (6.328995,  6.233709, 3.353257, 0.0,      11.514158),
    "CMD": (11.574297, 4.323545, 2.325735, 0.0,      7.985931),
    "CH":  (3.469312,  2.397976, 1.313547, 0.0,      4.429252),
    "MP":  (1.257644,  0.451867, 1.233931, 0.0,      0.834634),
    "DIG": (4.878166,  2.752280, 0.0,      9.333585, 5.083681),
    "OF":  (0.0,       0.0,      0.0,      0.0,      0.0),
}

# Presupuesto 2027 del GG (mensual, millones $): venta, % margen, directas, indirectas,
# arriendo y GC, resto. Fuente: Ppto26 filas 26-40. En 2027 el GG deja los gastos
# directos del canal digital dentro de "resto".
PPTO_2027 = {
    "MT":  (125.125, 0.353, 11.562, 6.452, 2.858, 12.682),
    "LC":  (93.958,  0.376, 8.886,  4.845, 6.006, 9.523),
    "MR":  (84.150,  0.334, 8.781,  4.339, 3.590, 8.529),
    "SE":  (111.283, 0.259, 6.107,  5.739, 3.631, 11.279),
    "CMD": (77.183,  0.519, 11.066, 3.980, 2.519, 7.823),
    "CH":  (45.000,  0.337, 3.469,  2.321, 1.422, 4.561),
    "MP":  (25.000,  0.432, 2.010,  1.289, 1.336, 2.534),
    "QL":  (30.000,  0.400, 1.990,  1.547, 2.212, 3.041),
    "DIG": (50.920,  0.483, 4.897,  2.534, 0.0,   14.640),
}

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

_CASE_CANAL = """CASE WHEN tipo_venta IN ('CASAMUSA.CL','VENTA ASISTIDA','MERCADO LIBRE CM','MERCADO LIBRE SC',
                                        'MKT PLACE','SODIMAC/LEGRAND') OR sucursal_logica = 'CANAL DIGITAL'
                      THEN 'DIG' ELSE sucursal_logica END"""


def _fecha_datos(cur):
    import data_loader_pg
    return data_loader_pg._fecha_datos_pg(cur)


def _ventas_mensuales(anio, hasta):
    """{mes: {canal: (venta, margen)}} en millones, del 1-ene al corte."""
    filas = db.query_all(
        f"""SELECT extract(month FROM fecha_conta)::int AS mes, {_CASE_CANAL} AS canal,
                   sum(total) AS v, sum(utilidad_bruta) AS u
              FROM v_ventas
             WHERE fecha_conta >= %(desde)s AND fecha_conta <= %(hasta)s
             GROUP BY 1, 2""",
        {"desde": dt.date(anio, 1, 1), "hasta": hasta})
    out = defaultdict(dict)
    for f in filas:
        if f["canal"] in GASTOS_2026:
            out[f["mes"]][f["canal"]] = (float(f["v"] or 0) / 1e6, float(f["u"] or 0) / 1e6)
    return out


def _ne_por_canal():
    """NE x Facturar (negocios ganados aún no facturados, /cargar_ne) por canal, en millones."""
    filas = db.query_all(
        """SELECT CASE WHEN sucursal = 'CANAL DIGITAL' THEN 'DIG' ELSE sucursal END AS canal,
                  sum(monto_ne) AS m
             FROM ne_x_facturar GROUP BY 1""")
    return {f["canal"]: float(f["m"] or 0) / 1e6 for f in filas
            if f["canal"] in GASTOS_2026 and (f["m"] or 0) > 0}


def _dias_habiles_mes(cur, corte):
    """(hábiles transcurridos hasta el corte, hábiles del mes)."""
    primero = corte.replace(day=1)
    siguiente = dt.date(corte.year + (corte.month == 12), corte.month % 12 + 1, 1)
    cur.execute(
        """SELECT count(*) FILTER (WHERE fecha <= %(c)s) AS t, count(*) AS m
             FROM dias_habiles_cl WHERE es_habil AND fecha >= %(p)s AND fecha < %(s)s""",
        {"c": corte, "p": primero, "s": siguiente})
    f = cur.fetchone()
    t, m = int(f["t"] or 0), int(f["m"] or 0)
    if m == 0:  # calendario sin cargar: lunes a viernes
        dias = [primero + dt.timedelta(d) for d in range((siguiente - primero).days)]
        habiles = [d for d in dias if d.weekday() < 5]
        t, m = sum(d <= corte for d in habiles), len(habiles)
    return max(t, 1), max(m, 1)


def _calcular(base, reparto):
    """base: {canal: dict(v, m, d, i, a, gdc, r)} -> filas con las columnas del GG."""
    activos = {c: b for c, b in base.items() if b["v"] > 0}
    suc = {c: b for c, b in activos.items() if c != ESPECIAL}   # OF no absorbe indirectas
    pool = sum(b["i"] for b in suc.values())
    tot_m = sum(b["m"] for b in suc.values())
    tot_v = sum(b["v"] for b in suc.values())
    filas = []
    for c, _ in CANALES:
        b = activos.get(c)
        if not b:
            continue
        if c == ESPECIAL:
            ind, denom = 0.0, 0.0
        elif reparto == "venta":
            ind = pool * b["v"] / tot_v if tot_v else 0
            tasa = pool / tot_v if tot_v else 0              # indirectas por $ de venta
            denom = (b["m"] / b["v"]) - tasa
        else:
            ind = pool * b["m"] / tot_m if tot_m else 0
            k = pool / tot_m if tot_m else 0                 # indirectas por $ de margen
            denom = (b["m"] / b["v"]) * (1 - k)
        propio = b["d"] + b["a"] + b["gdc"] + b["r"]
        gasto = propio + ind
        ul = b["m"] - gasto
        pe = propio / denom if denom > 0 else None
        filas.append({
            "canal": c, "nombre": NOMBRE[c], "venta": b["v"], "margen": b["m"],
            "mg_pct": b["m"] / b["v"] * 100 if b["v"] else None,
            "rem_dir": b["d"], "rem_ind": ind, "rem_ind_gg": b["i"], "arriendo": b["a"],
            "gastos_canal": b["gdc"], "resto": b["r"], "total_gasto": gasto,
            "contribucion": b["m"] - propio, "ultima_linea": ul,
            "ul_pct": ul / b["v"] * 100 if b["v"] else None,
            "pe": pe, "holgura": (b["v"] / pe - 1) * 100 if pe else None,
            "especial": c == ESPECIAL,
        })
    tot = {k: sum(f[k] for f in filas) for k in
           ("venta", "margen", "rem_dir", "rem_ind", "arriendo", "gastos_canal", "resto",
            "total_gasto", "contribucion", "ultima_linea")}
    tot["mg_pct"] = tot["margen"] / tot["venta"] * 100 if tot["venta"] else None
    tot["ul_pct"] = tot["ultima_linea"] / tot["venta"] * 100 if tot["venta"] else None
    fijo = tot["total_gasto"]
    tot["pe"] = fijo / (tot["mg_pct"] / 100) if tot["mg_pct"] else None
    tot["holgura"] = (tot["venta"] / tot["pe"] - 1) * 100 if tot["pe"] else None
    return filas, tot


def _base_real(meses_datos, meses, factor_mes=None):
    """Suma venta/margen de los meses pedidos y cobra el gasto del GG por cada mes con venta.
    factor_mes: {mes: fracción de gasto a cobrar} (mes en curso al día)."""
    base = {}
    for c in GASTOS_2026:
        v = m = n = 0.0
        for mes in meses:
            vm = meses_datos.get(mes, {}).get(c)
            if vm and vm[0] > 0:
                v += vm[0]; m += vm[1]
                n += (factor_mes or {}).get(mes, 1.0)
        if v <= 0:
            continue
        d, i, a, gdc, r = GASTOS_2026[c]
        base[c] = dict(v=v, m=m, d=d * n, i=i * n, a=a * n, gdc=gdc * n, r=r * n, meses=n)
    return base


def get_panel(escenario="real", periodo="prom", reparto="margen", con_ne=True):
    reparto = "venta" if reparto == "venta" else "margen"
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            corte = _fecha_datos(cur)
            dh_t, dh_m = _dias_habiles_mes(cur, corte)
    anio = corte.year
    mes_actual = corte.month
    datos = _ventas_mensuales(anio, corte)
    cerrados = [m for m in range(1, mes_actual) if m in datos]
    ne = _ne_por_canal() if con_ne else {}
    ne_total = sum(ne.values())
    # Margen de los NE: el margen ERP del año a la fecha de cada sucursal (decisión
    # del usuario 2026-11-25; Proyección usa 20% fijo, aquí no).
    v_ytd = defaultdict(float); u_ytd = defaultdict(float)
    for md_ in datos.values():
        for c, (v, u) in md_.items():
            v_ytd[c] += v; u_ytd[c] += u
    tot_v = sum(v for c, v in v_ytd.items() if c != ESPECIAL)
    tot_u = sum(u for c, u in u_ytd.items() if c != ESPECIAL)
    mg_ne = {c: (u_ytd[c] / v_ytd[c] if v_ytd[c] > 0 else tot_u / tot_v) for c in ne}

    def proyectar(md):
        """Mes en curso al cierre: venta al día x hábiles del mes / transcurridos + NE por facturar."""
        out = {c: ((v, u) if c == ESPECIAL else (v * dh_m / dh_t, u * dh_m / dh_t)) for c, (v, u) in md.items()}
        for c, n in ne.items():
            v, u = out.get(c, (0.0, 0.0))
            out[c] = (v + n, u + n * mg_ne[c])
        return out

    # Serie mensual (siempre con reparto por margen y gasto mensual completo;
    # el mes en curso, proyectado por días hábiles).
    serie = []
    for mes in sorted(datos):
        md = datos[mes]
        if mes == mes_actual:
            md = proyectar(md)
        f, t = _calcular(_base_real({mes: md}, [mes]), reparto)
        serie.append({"mes": mes, "nombre": MESES[mes - 1], "en_curso": mes == mes_actual,
                      "venta": t["venta"], "ultima_linea": t["ultima_linea"], "pe": t["pe"],
                      "por_canal": {x["canal"]: x["ultima_linea"] for x in f}})

    # Mes en curso: venta al día vs punto de equilibrio del mes.
    md_hoy = datos.get(mes_actual, {})
    mes_curso = None
    if md_hoy:
        v_proj = proyectar(md_hoy)
        _, t_proj = _calcular(_base_real({mes_actual: v_proj}, [mes_actual]), reparto)
        venta_hoy = sum(v for v, _ in md_hoy.values())
        pe = t_proj["pe"]
        faltan = max(dh_m - dh_t, 0)
        mes_curso = {
            "mes": MESES[mes_actual - 1], "venta_al_dia": venta_hoy, "pe": pe,
            "pe_al_dia": pe * dh_t / dh_m if pe else None,
            "proyeccion": t_proj["venta"], "ul_proyectada": t_proj["ultima_linea"],
            "dh_transcurridos": dh_t, "dh_mes": dh_m, "dh_restantes": faltan,
            "proyeccion_sin_ne": t_proj["venta"] - ne_total, "ne": ne_total,
            "falta_para_pe": max(pe - venta_hoy, 0) if pe else None,
            # Lo que falta después de facturar los NE, a repartir en los hábiles que quedan.
            "falta_con_ne": max(pe - venta_hoy - ne_total, 0) if pe else None,
            "por_dia_necesario": (max(pe - venta_hoy - ne_total, 0) / faltan) if pe and faltan else None,
            "ritmo_diario": venta_hoy / dh_t,
        }

    # Año completo: meses cerrados reales + mes en curso proyectado + meses que
    # faltan al promedio de los cerrados (por canal).
    prom_canal = {}
    for c in GASTOS_2026:
        if c == ESPECIAL:
            continue  # los rebates no se proyectan a los meses que faltan
        vals = [datos[m][c] for m in cerrados if c in datos[m] and datos[m][c][0] > 0]
        if vals:
            prom_canal[c] = (sum(v for v, _ in vals) / len(vals), sum(u for _, u in vals) / len(vals))
    datos_anio = {m: dict(datos[m]) for m in cerrados}
    if md_hoy:
        datos_anio[mes_actual] = proyectar(md_hoy)
    for m in range(mes_actual + 1, 13):
        datos_anio[m] = dict(prom_canal)
    f_anio, t_anio = _calcular(_base_real(datos_anio, range(1, 13)), reparto)
    venta_al_dia = sum(v for m in datos for v, _ in datos[m].values())
    anual = {
        "venta_proyectada": t_anio["venta"], "pe": t_anio["pe"], "holgura": t_anio["holgura"],
        "ul_proyectada": t_anio["ultima_linea"], "venta_al_dia": venta_al_dia,
        "falta_para_pe": max(t_anio["pe"] - venta_al_dia, 0) if t_anio["pe"] else None,
        "avance_pe": venta_al_dia / t_anio["pe"] * 100 if t_anio["pe"] else None,
        # Equilibrio "a la fecha": el gasto se devenga parejo por mes (y el mes en curso por días hábiles).
        "pe_al_dia": t_anio["pe"] * (mes_actual - 1 + dh_t / dh_m) / 12 if t_anio["pe"] else None,
        "pe_por_canal": {x["canal"]: x["pe"] for x in f_anio},
    }

    # Período pedido.
    if escenario == "2027":
        base = {}
        for c, (v, mg, d, i, a, r) in PPTO_2027.items():
            base[c] = dict(v=v, m=v * mg, d=d, i=i, a=a, gdc=0.0, r=r, meses=1)
        if periodo in ("acum", "anio"):
            periodo = "anio"
            base = {c: {k: (x * 12 if k != "meses" else 12) for k, x in b.items()} for c, b in base.items()}
        etiqueta = "Presupuesto 2027 · " + ("anual" if periodo == "anio" else "mensual")
        nota_periodo = "Montos del archivo del GG (venta y margen proyectados)."
    elif periodo == "anio":
        base = _base_real(datos_anio, range(1, 13))
        etiqueta = f"Año {anio} completo · proyectado al cierre"
        nota_periodo = (f"Ene–{MESES[cerrados[-1] - 1].lower() if cerrados else ''} real, "
                        f"{MESES[mes_actual - 1].lower()} proyectado por días hábiles"
                        + (" más los NE por facturar" if ne else "")
                        + " y el resto del año al promedio mensual de los meses cerrados.")
    elif periodo == "acum":
        factor = {mes_actual: dh_t / dh_m}
        base = _base_real(datos, sorted(datos), factor)
        etiqueta = f"Acumulado ene–{MESES[mes_actual - 1].lower()} {anio} (al {corte:%d-%m})"
        nota_periodo = f"El gasto de {MESES[mes_actual - 1].lower()} se cobra por los días hábiles transcurridos ({dh_t} de {dh_m})."
    elif periodo.startswith("m"):
        mes = int(periodo[1:])
        md = datos.get(mes, {})
        if mes == mes_actual:
            md = proyectar(md)
            etiqueta = f"{MESES[mes - 1]} {anio} · proyectado al cierre"
            ne_txt = f"{ne_total:.1f}".replace(".", ",")
            nota_periodo = (f"Venta al {corte:%d-%m} proyectada por días hábiles ({dh_t} de {dh_m})"
                            + (f" + NE por facturar (${ne_txt} MM, con el margen del año de cada sucursal)." if ne else "."))
        else:
            etiqueta = f"{MESES[mes - 1]} {anio}"
            nota_periodo = "Mes cerrado."
        base = _base_real({mes: md}, [mes])
    else:
        periodo = "prom"
        base = _base_real(datos, cerrados)
        for c, b in base.items():
            n = (len(cerrados) if c == ESPECIAL else b["meses"]) or 1  # rebates: promedio sobre todos los meses
            base[c] = {k: (x / n if k != "meses" else n) for k, x in b.items()}
        etiqueta = f"Promedio mensual ene–{MESES[cerrados[-1] - 1].lower() if cerrados else ''} {anio}"
        nota_periodo = "Promedio de los meses cerrados con venta (Maipú desde su apertura)."

    filas, total = _calcular(base, reparto)

    # Total del año por sucursal: acumulado a la fecha y proyectado al cierre.
    if escenario == "2027":
        mult = 1 if periodo == "anio" else 12
        for f in filas:
            f["ul_acum"] = None
            f["ul_anio"] = f["ultima_linea"] * mult
        total["ul_acum"] = None
        total["ul_anio"] = total["ultima_linea"] * mult
    else:
        f_acum, t_acum = _calcular(_base_real(datos, sorted(datos), {mes_actual: dh_t / dh_m}), reparto)
        acum = {x["canal"]: x["ultima_linea"] for x in f_acum}
        anio_c = {x["canal"]: x["ultima_linea"] for x in f_anio}
        for f in filas:
            f["ul_acum"] = acum.get(f["canal"])
            f["ul_anio"] = anio_c.get(f["canal"])
        total["ul_acum"] = t_acum["ultima_linea"]
        total["ul_anio"] = t_anio["ultima_linea"]

    # Grafico "Venta vs punto de equilibrio": el usuario elige ahi mismo entre el
    # promedio de los meses cerrados y el mes en curso proyectado (2026-11-28),
    # sin cambiar el periodo de la tabla.
    grafico = None
    if escenario != "2027":
        def _barras(b):
            f_, _ = _calcular(b, reparto)
            return [{"nombre": x["nombre"], "venta": x["venta"], "pe": x["pe"]} for x in f_ if not x.get("especial")]
        b_prom = _base_real(datos, cerrados)
        for c, b in b_prom.items():
            n = (len(cerrados) if c == ESPECIAL else b["meses"]) or 1
            b_prom[c] = {k: (x / n if k != "meses" else n) for k, x in b.items()}
        grafico = {"prom": {"titulo": f"Promedio ene–{MESES[cerrados[-1] - 1].lower() if cerrados else ''}",
                            "barras": _barras(b_prom)}}
        if md_hoy:
            grafico["curso"] = {"titulo": f"{MESES[mes_actual - 1]} proyectado",
                                "barras": _barras(_base_real({mes_actual: proyectar(md_hoy)}, [mes_actual]))}
    return {
        "grafico": grafico,
        "corte": corte.isoformat(), "anio": anio, "escenario": escenario, "periodo": periodo,
        "reparto": reparto, "con_ne": bool(con_ne), "ne_total": ne_total,
        "ne_por_canal": ne, "mg_ne": {c: m * 100 for c, m in mg_ne.items()}, "etiqueta": etiqueta, "nota_periodo": nota_periodo,
        "corte_txt": f"{corte:%d-%m}", "filas": filas, "total": total, "serie": serie, "mes_curso": mes_curso, "anual": anual,
        "meses_disponibles": [{"valor": f"m{m}", "nombre": f"{MESES[m - 1]} {anio}" + (" (en curso)" if m == mes_actual else "")}
                              for m in sorted(datos)],
    }
