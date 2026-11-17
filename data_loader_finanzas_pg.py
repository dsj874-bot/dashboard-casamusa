"""
Finanzas > Proveedores: programa de pagos a proveedores (migracion 026).

Fuente: informe de SAP "Pago a Proveedores" (una fila por OC + recepcion,
montos CON IVA). Se sube desde /finanzas/proveedores en partes con un
carga_id; /fin marca la carga como completa y borra las anteriores.

Regla de pago (definida con el usuario 2026-11-17):
- vence = fecha de recepcion + plazo del proveedor (tabla plazo_proveedor;
  sin fila = 60 dias; Philips/Signify y Zama 30; Legrand 45).
- "Pagada" no se programa. "Pago parcial" programa el saldo proporcional.
  "Facturada, impaga" y "Recibida, sin facturar" programan el total.
- Proveedores con sin_vinculo (Legrand): SAP no amarra sus facturas a las
  recepciones desde abril-2026, asi que su estado no sirve; se asume pagado
  lo ya vencido y se programa solo lo que vence de hoy en adelante.
"""
import datetime as dt
from collections import defaultdict

import db

PLAZO_DEFECTO = 60

COLUMNAS_SAP = [
    "N° OC", "Fecha OC", "Código Proveedor", "Proveedor", "Total OC", "N° Recepción",
    "Fecha Recepción", "Total Recepción", "N° Facturas", "Total Facturado", "Total Pagado", "Estado de pago",
]
_COLS_DB = ("n_oc, fecha_oc, codigo_proveedor, proveedor, total_oc, n_recepcion, fecha_recepcion, "
            "total_recepcion, n_facturas, total_facturado, total_pagado, estado")
_FECHAS = {1, 6}
_ENTEROS = {0, 5, 8}
_NUMEROS = {4, 7, 9, 10}

TIPOS = ["Facturada, impaga", "Pago parcial (saldo)", "Recibida, sin factura aún", "Sin vínculo en SAP (se asume pagado lo vencido)"]


def _fecha(v):
    if v in (None, ""):
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip()[:10]
    return dt.date.fromisoformat(s)


def _num(v):
    if v in (None, ""):
        return None
    return float(str(v).replace(",", "."))


def normalizar_filas(filas):
    """Filas en el orden de COLUMNAS_SAP -> tuplas para COPY. ValueError legible si no calza."""
    salida = []
    for i, f in enumerate(filas, start=1):
        if len(f) != len(COLUMNAS_SAP):
            raise ValueError(f"La fila {i} no tiene las {len(COLUMNAS_SAP)} columnas del informe.")
        try:
            t = []
            for j, v in enumerate(f):
                if j in _FECHAS:
                    t.append(_fecha(v))
                elif j in _ENTEROS:
                    n = _num(v)
                    t.append(None if n is None else int(n))
                elif j in _NUMEROS:
                    t.append(_num(v))
                else:
                    t.append(None if v in (None, "") else str(v).strip())
        except (TypeError, ValueError) as e:
            raise ValueError(f"La fila {i} tiene un dato que no se pudo leer ({e}).")
        if t[0] is None or t[11] is None:
            raise ValueError(f"La fila {i} no trae N° OC o estado de pago.")
        salida.append(tuple(t))
    return salida


def iniciar_carga(archivo, usuario):
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pago_proveedor_carga (archivo, cargado_por) VALUES (%s, %s) RETURNING carga_id",
                        ((archivo or "")[:200], usuario))
            cid = cur.fetchone()["carga_id"]
        conn.commit()
    return cid


def cargar_parte(carga_id, filas):
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            with cur.copy(f"COPY pago_proveedor_linea (carga_id, {_COLS_DB}) FROM STDIN") as cp:
                for f in filas:
                    cp.write_row((carga_id,) + f)
        conn.commit()
    return len(filas)


def terminar_carga(carga_id, filas):
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM pago_proveedor_linea WHERE carga_id = %s", (carga_id,))
            n = cur.fetchone()["n"]
            if n == 0:
                raise ValueError("La carga no trae filas.")
            cur.execute("UPDATE pago_proveedor_carga SET completa = true, filas = %s WHERE carga_id = %s", (n, carga_id))
            cur.execute("DELETE FROM pago_proveedor_carga WHERE carga_id <> %s", (carga_id,))
        conn.commit()
    return n


def ultima_carga():
    return db.query_one("""SELECT carga_id, archivo, filas, cargado_por, cargado_en FROM pago_proveedor_carga
                            WHERE completa ORDER BY carga_id DESC LIMIT 1""")


# ══════════════════════════════════════════════════════
#  PLAZOS
# ══════════════════════════════════════════════════════
def get_plazos():
    return db.query_all("SELECT codigo_proveedor, nombre, dias, sin_vinculo, updated_by, updated_at FROM plazo_proveedor ORDER BY nombre")


def guardar_plazo(codigo, nombre, dias, sin_vinculo, usuario):
    dias = int(dias)
    if not 0 <= dias <= 365:
        raise ValueError("El plazo debe estar entre 0 y 365 días.")
    db.execute("""INSERT INTO plazo_proveedor (codigo_proveedor, nombre, dias, sin_vinculo, updated_by, updated_at)
                  VALUES (%s, %s, %s, %s, %s, now())
                  ON CONFLICT (codigo_proveedor) DO UPDATE SET nombre = excluded.nombre, dias = excluded.dias,
                      sin_vinculo = excluded.sin_vinculo, updated_by = excluded.updated_by, updated_at = now()""",
               (codigo, (nombre or "")[:200], dias, bool(sin_vinculo), usuario))


def borrar_plazo(codigo):
    db.execute("DELETE FROM plazo_proveedor WHERE codigo_proveedor = %s", (codigo,))


# ══════════════════════════════════════════════════════
#  PANEL
# ══════════════════════════════════════════════════════
def _lunes(d):
    return d - dt.timedelta(days=d.weekday())


def get_panel(hoy=None, proveedores=None):
    hoy = hoy or dt.date.today()
    carga = ultima_carga()
    plazos = {p["codigo_proveedor"]: p for p in get_plazos()}
    if not carga:
        return {"carga": None, "plazos": list(plazos.values())}
    filas = db.query_all("""SELECT n_oc, fecha_oc, codigo_proveedor, proveedor, n_recepcion, fecha_recepcion,
                                   total_recepcion, total_facturado, total_pagado, estado
                              FROM pago_proveedor_linea
                             WHERE carga_id = %s AND estado <> 'Sin recepción' AND fecha_recepcion IS NOT NULL""",
                            (carga["carga_id"],))
    todos_prov = sorted(({"proveedor": n, "codigo": c} for c, n in {(f["codigo_proveedor"], f["proveedor"]) for f in filas if f["proveedor"]}),
                        key=lambda x: x["proveedor"])
    filtro = set(proveedores or [])
    pend = []
    for f in filas:
        if filtro and f["proveedor"] not in filtro:
            continue
        rec = float(f["total_recepcion"] or 0)
        fac = float(f["total_facturado"] or 0)
        pag = float(f["total_pagado"] or 0)
        e = f["estado"]
        pl = plazos.get(f["codigo_proveedor"])
        dias = pl["dias"] if pl else PLAZO_DEFECTO
        sin_vinc = bool(pl and pl["sin_vinculo"])
        vence = f["fecha_recepcion"] + dt.timedelta(days=dias)
        if e == "Pagada":
            continue
        if e == "Pago parcial":
            monto = rec * max(0.0, 1 - pag / fac) if fac else rec
            tipo = TIPOS[1]
        elif e == "Facturada, impaga":
            monto, tipo = rec, TIPOS[0]
        else:  # Recibida, sin facturar (u otro estado no pagado)
            monto, tipo = rec, TIPOS[2]
            if sin_vinc:
                if vence < hoy:
                    continue
                tipo = TIPOS[3]
        if monto <= 1:
            continue
        pend.append({
            "proveedor": f["proveedor"], "codigo_proveedor": f["codigo_proveedor"], "n_oc": f["n_oc"],
            "n_recepcion": f["n_recepcion"], "fecha_recepcion": f["fecha_recepcion"].isoformat(), "plazo": dias,
            "vence": vence.isoformat(), "dias_vencido": (hoy - vence).days if vence < hoy else 0,
            "estado": e, "tipo": tipo, "monto": round(monto),
        })

    lunes_hoy = _lunes(hoy)
    sem = defaultdict(lambda: defaultdict(float))
    prov_sem = defaultdict(lambda: defaultdict(float))
    meses = set()
    prov_mes = defaultdict(lambda: defaultdict(float))
    for p in pend:
        v = dt.date.fromisoformat(p["vence"])
        k = "vencido" if v < hoy else _lunes(v).isoformat()
        sem[k][p["tipo"]] += p["monto"]
        prov_sem[k][p["proveedor"]] += p["monto"]
        m = "vencido" if v < hoy else v.strftime("%Y-%m")
        meses.add(m)
        prov_mes[p["proveedor"]][m] += p["monto"]
    claves = sorted(sem, key=lambda k: "0" if k == "vencido" else k)
    semanas = []
    for k in claves:
        tops = sorted(prov_sem[k].items(), key=lambda x: -x[1])[:4]
        semanas.append({"semana": k, "por_tipo": {t: round(sem[k].get(t, 0)) for t in TIPOS},
                        "total": round(sum(sem[k].values())), "principales": [{"proveedor": a, "monto": round(b)} for a, b in tops]})
    meses = sorted(meses, key=lambda m: "0" if m == "vencido" else m)
    filas_pm = sorted(({"proveedor": pr, "plazo": next((p["plazo"] for p in pend if p["proveedor"] == pr), PLAZO_DEFECTO),
                        "valores": {m: round(v) for m, v in vals.items()}, "total": round(sum(vals.values()))}
                       for pr, vals in prov_mes.items()), key=lambda x: -x["total"])

    tot = sum(p["monto"] for p in pend)
    venc = [p for p in pend if p["dias_vencido"] > 0]
    fin_semana = lunes_hoy + dt.timedelta(days=6)
    kpis = {
        "pendiente": round(tot),
        "vencido": round(sum(p["monto"] for p in venc)),
        "vencido_facturado": round(sum(p["monto"] for p in venc if p["tipo"] in (TIPOS[0], TIPOS[1]))),
        "semana": round(sum(p["monto"] for p in pend if hoy <= dt.date.fromisoformat(p["vence"]) <= fin_semana)),
        "prox_30": round(sum(p["monto"] for p in pend if hoy <= dt.date.fromisoformat(p["vence"]) <= hoy + dt.timedelta(days=30))),
        "proveedores": len({p["proveedor"] for p in pend}),
    }
    return {
        "hoy": hoy.isoformat(), "carga": {**carga, "cargado_en": carga["cargado_en"].isoformat()},
        "kpis": kpis, "tipos": TIPOS, "semanas": semanas,
        "por_proveedor_mes": {"meses": meses, "filas": filas_pm},
        # Primero las facturas impagas (lo que de verdad se debe), despues lo recibido sin factura
        "vencidos": sorted(venc, key=lambda p: (p["tipo"] not in (TIPOS[0], TIPOS[1]), -p["dias_vencido"], p["proveedor"])),
        "detalle": sorted(pend, key=lambda p: (p["vence"], p["proveedor"])),
        "proveedores": todos_prov,
        "plazos": [{**p, "updated_at": p["updated_at"].isoformat() if p["updated_at"] else None} for p in plazos.values()],
    }


def exportar_excel(hoy=None, proveedores=None):
    """Excel del programa de pagos: por semana, vencido impago, proveedor x mes y detalle."""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    p = get_panel(hoy, proveedores)
    wb = Workbook()
    azul = PatternFill("solid", fgColor="1F4E78")

    def hoja(ws, titulo, cab, filas, anchos, money=()):
        ws.append([titulo]); ws["A1"].font = Font(bold=True, size=13)
        ws.append([])
        ws.append(cab)
        for c in ws[3]:
            c.font = Font(bold=True, color="FFFFFF"); c.fill = azul; c.alignment = Alignment(wrap_text=True, vertical="center")
        for f in filas:
            ws.append(f)
            for i in money:
                ws.cell(ws.max_row, i).number_format = '#,##0'
        for i, w in enumerate(anchos):
            ws.column_dimensions[chr(65 + i)].width = w
        ws.freeze_panes = "A4"

    if not p.get("carga"):
        wb.active.append(["No hay informe de pagos cargado."])
    else:
        sub = f"Programa de pagos a proveedores al {p['hoy']} (montos con IVA)"
        hoja(wb.active, sub, ["Semana (lunes)"] + p["tipos"] + ["Total"],
             [["Vencido" if s["semana"] == "vencido" else s["semana"]] + [s["por_tipo"][t] or None for t in p["tipos"]] + [s["total"]] for s in p["semanas"]],
             [14, 16, 14, 16, 18, 14], money=range(2, 7))
        wb.active.title = "Por semana"
        hoja(wb.create_sheet("Vencido impago"), "Vencido impago",
             ["Proveedor", "Estado SAP", "N° OC", "N° Recepción", "Fecha recepción", "Venció", "Días vencido", "Pendiente"],
             [[v["proveedor"], v["estado"], v["n_oc"], v["n_recepcion"], v["fecha_recepcion"], v["vence"], v["dias_vencido"], v["monto"]] for v in p["vencidos"]],
             [40, 22, 10, 12, 12, 12, 10, 14], money=(8,))
        meses = p["por_proveedor_mes"]["meses"]
        hoja(wb.create_sheet("Por proveedor y mes"), "Por proveedor y mes",
             ["Proveedor", "Plazo (días)"] + ["Vencido" if m == "vencido" else m for m in meses] + ["Total"],
             [[f["proveedor"], f["plazo"]] + [f["valores"].get(m) for m in meses] + [f["total"]] for f in p["por_proveedor_mes"]["filas"]],
             [40, 10] + [13] * (len(meses) + 1), money=range(3, len(meses) + 4))
        hoja(wb.create_sheet("Detalle"), "Detalle pendiente",
             ["Vence", "Proveedor", "Tipo", "Estado SAP", "N° OC", "N° Recepción", "Fecha recepción", "Plazo", "Pendiente"],
             [[d["vence"], d["proveedor"], d["tipo"], d["estado"], d["n_oc"], d["n_recepcion"], d["fecha_recepcion"], d["plazo"], d["monto"]] for d in p["detalle"]],
             [12, 40, 30, 22, 10, 12, 12, 8, 14], money=(9,))
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return buf
