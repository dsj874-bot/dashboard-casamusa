"""
CRM de clientes (migracion 029, 2026-11-27). Es OTRA mirada del CRM: la de
cotizaciones (data_loader_crm_pg) sigue igual; esta mira al cliente.

Reglas acordadas con el usuario:
- Cartera de San Isidro: clientes que hicieron al menos la mitad de su compra de
  los ultimos 24 meses en CMD, SE o CANAL DIGITAL (sin Cliente Boleta), con
  cualquier vendedor, actual o anterior.
- Dueño: el vendedor que mas le vende en San Isidro. Si ya no esta, su reemplazo
  (data_loader_crm_pg.REEMPLAZOS: Toro y Freddy Flores -> Marcelo Gatica); si no
  tiene reemplazo (Norambuena, Ema Sepulveda...), queda SIN DUEÑO para que Jorge lo
  reparta. Un dueño puesto a mano en el CRM manda.
- Segmento por compra en San Isidro de 24 meses: A >= 5 MM, B 1-5 MM, C < 1 MM.
  Alertas solo para A y B (C seria ruido: 2.800 clientes chicos, sobre todo web).
- Alertas: dejo de comprar (mas de 2,5 veces su intervalo habitual y al menos 60
  dias), bajo su ritmo (ultimos 90 dias < la mitad de su promedio de 12 meses),
  cotiza y no compra (3+ cotizaciones en 60 dias sin ninguna convertida y sin
  compras en ese tiempo), sin dueño y compromiso vencido. Una gestion registrada
  con el cliente las apaga por 21 dias.
- Sin margen ni costo (Marcelo Gatica, que usa el CRM, es vendedor).
- Datos personales del maestro (contacto, telefono, correo): solo en la ficha.
"""
import datetime as dt
import json
import time
from collections import defaultdict
from zoneinfo import ZoneInfo

import db
import data_loader_crm_pg as CRM

SUC_SI = ("CMD", "SE", "CANAL DIGITAL")
DIAS_CARTERA = 730
MONTO_A, MONTO_B = 5_000_000, 1_000_000
SILENCIO_GESTION = 21          # dias que una gestion apaga las alertas del cliente
ESCALERA_CLIENTE = (5, 10, 20)  # dias habiles despues de "dejo de comprar" para subir de nivel
DIAS_PERDIDO = 365             # mas de un año sin comprar: "perdido" (se filtra, no alerta)
# El reloj de las alertas parte el dia que se lanzo el CRM de clientes: si no, los que dejaron de
# comprar hace meses llegarian el primer dia directo al Gerente General.
LANZAMIENTO = dt.date(2026, 11, 27)

_cache = {"t": 0, "d": None}


def _hoy():
    return dt.datetime.now(ZoneInfo("America/Santiago")).date()


def _activo(v):
    """Vendedor que hoy esta en la empresa (vendedor_home), sin cuentas de oficina."""
    return bool(v) and v in CRM.sucursales_vendedores() and not v.upper().startswith("VENTA")


def _dueno_calculado(top):
    if not top or top.startswith("-"):
        return None
    if top in CRM.REEMPLAZOS:
        return CRM.REEMPLAZOS[top]
    return top if _activo(top) else None


def _ventas_cartera(forzar=False):
    """Ventas de 24 meses por cliente de San Isidro + su vendedor principal. Se guarda en
    crm_cartera_cache (la consulta toma ~25 s); vale 26 horas o hasta que el cron la renueve."""
    if not forzar:
        r = db.query_one("SELECT datos FROM crm_cartera_cache WHERE id=1 AND calculado_en > now() - interval '26 hours'")
        if r:
            datos = r["datos"]
            for f in datos:
                for k in ("primera", "ult"):
                    f[k] = dt.date.fromisoformat(f[k]) if f.get(k) else None
            return datos
    hoy = _hoy()
    desde = hoy - dt.timedelta(days=DIAS_CARTERA)
    filas = db.query_all(
        """SELECT codigo_cliente AS cc, max(nombre_cliente) AS cl,
                  sum(total) FILTER (WHERE sucursal_logica = ANY(%(si)s)) AS t24_si,
                  sum(total) FILTER (WHERE fecha_conta > %(h12)s) AS t12,
                  sum(total) FILTER (WHERE fecha_conta <= %(h12)s) AS t12p,
                  sum(total) FILTER (WHERE fecha_conta > %(h90)s) AS t90,
                  min(fecha_conta) AS primera, max(fecha_conta) AS ult,
                  count(DISTINCT fecha_conta) AS dias,
                  count(DISTINCT date_trunc('month', fecha_conta)) AS meses
             FROM v_ventas
            WHERE fecha_conta >= %(d)s AND nombre_cliente NOT ILIKE 'CLIENTE BOLETA%%'
            GROUP BY 1
           HAVING sum(total) > 0
              AND sum(total) FILTER (WHERE sucursal_logica = ANY(%(si)s)) >= 0.5 * sum(total)""",
        {"si": list(SUC_SI), "d": desde, "h12": hoy - dt.timedelta(days=365), "h90": hoy - dt.timedelta(days=90)})
    codigos = [f["cc"] for f in filas]
    top = defaultdict(lambda: (None, 0.0))
    for r in db.query_all(
            """SELECT codigo_cliente AS cc, vendedor AS ve, sum(total) AS t FROM v_ventas
                WHERE fecha_conta >= %s AND sucursal_logica = ANY(%s) AND codigo_cliente = ANY(%s)
                GROUP BY 1, 2""", (desde, list(SUC_SI), codigos)):
        if float(r["t"] or 0) > top[r["cc"]][1]:
            top[r["cc"]] = (r["ve"], float(r["t"] or 0))
    datos = [{"cc": f["cc"], "cl": f["cl"], "t24_si": float(f["t24_si"] or 0), "t12": float(f["t12"] or 0),
              "t12p": float(f["t12p"] or 0), "t90": float(f["t90"] or 0), "primera": f["primera"], "ult": f["ult"],
              "dias": int(f["dias"] or 0), "meses": int(f["meses"] or 0), "topv": top[f["cc"]][0]} for f in filas]
    db.execute("""INSERT INTO crm_cartera_cache (id, calculado_en, datos) VALUES (1, now(), %s::jsonb)
                  ON CONFLICT (id) DO UPDATE SET calculado_en=now(), datos=EXCLUDED.datos""",
               (json.dumps(datos, default=lambda o: o.isoformat()),))
    return datos


def refrescar_cartera_pg():
    """Para el cron diario: recalcula las ventas de la cartera."""
    datos = _ventas_cartera(forzar=True)
    invalidar()
    return {"clientes": len(datos)}


def _cartera():
    """Todos los clientes de San Isidro (cache 10 min en memoria: ~3.200 filas). Lo pesado
    (ventas de 24 meses) sale de crm_cartera_cache; lo del CRM (dueño manual, compromisos,
    gestiones, cotizaciones, oportunidades) se lee fresco."""
    if _cache["d"] is not None and time.time() - _cache["t"] < 600:
        return _cache["d"]
    hoy = _hoy()
    filas = _ventas_cartera()
    codigos = [f["cc"] for f in filas]
    cot = {r["cc"]: r for r in db.query_all(
        """WITH c AS (SELECT nro_cotizacion, max(codigo_cliente) AS cc, sum(total) AS t,
                             bool_or(nro_pedido IS NOT NULL OR nro_factura IS NOT NULL) AS conv
                        FROM cotizacion_linea WHERE fecha >= %s AND codigo_cliente = ANY(%s) GROUP BY 1)
           SELECT cc, count(*) AS n, sum(t) AS t, count(*) FILTER (WHERE conv) AS nconv FROM c GROUP BY 1""",
        (hoy - dt.timedelta(days=60), codigos))}
    opp = {r["cc"]: r for r in db.query_all(
        """SELECT codigo_cliente AS cc, count(*) AS n, sum(monto) AS t FROM crm_oportunidad
            WHERE etapa NOT IN ('ganada','perdida') GROUP BY 1""")}
    crm = {r["codigo_cliente"]: r for r in db.query_all("SELECT * FROM crm_cliente")}
    mae = {r["codigo_cliente"]: r for r in db.query_all(
        "SELECT codigo_cliente, razon_social, comuna, region, condicion_pago, vendedor_sap FROM cliente_maestro WHERE codigo_cliente = ANY(%s)",
        (codigos,))}
    out = []
    for f in filas:
        cc = f["cc"]
        t24si = float(f["t24_si"] or 0)
        seg = "A" if t24si >= MONTO_A else "B" if t24si >= MONTO_B else "C"
        topv = f["topv"]
        manual = (crm.get(cc) or {}).get("dueno")
        dueno = manual or _dueno_calculado(topv)
        dias = int(f["dias"] or 0)
        span = (f["ult"] - f["primera"]).days if f["ult"] and f["primera"] else 0
        intervalo = max(15, span / (dias - 1)) if dias > 1 else None
        c = cot.get(cc) or {}
        o = opp.get(cc) or {}
        m = mae.get(cc) or {}
        dsc = (hoy - f["ult"]).days if f["ult"] else None
        out.append({
            "cc": cc, "cliente": m.get("razon_social") or f["cl"], "segmento": seg,
            "dueno": dueno, "dueno_manual": bool(manual), "vendedor_top": topv, "vendedor_sap": m.get("vendedor_sap"),
            "sin_dueno": dueno is None, "perdido": dsc is not None and dsc > DIAS_PERDIDO,
            "comuna": m.get("comuna"), "region": m.get("region"), "condicion_pago": m.get("condicion_pago"),
            "t24_si": t24si, "t12": float(f["t12"] or 0), "t12p": float(f["t12p"] or 0), "t90": float(f["t90"] or 0),
            "ult": f["ult"].isoformat() if f["ult"] else None, "dias_sin_compra": dsc,
            "meses": int(f["meses"] or 0), "dias_compra": dias, "intervalo": round(intervalo) if intervalo else None,
            "cot_n": int(c.get("n") or 0), "cot_t": float(c.get("t") or 0), "cot_conv": int(c.get("nconv") or 0),
            "opp_n": int(o.get("n") or 0), "opp_t": float(o.get("t") or 0),
            "fecha_compromiso": (crm.get(cc) or {}).get("fecha_compromiso"),
            "proxima_accion": (crm.get(cc) or {}).get("proxima_accion"),
            "ultima_gestion": (crm.get(cc) or {}).get("ultima_gestion"),
        })
    _cache.update(t=time.time(), d=out)
    return out


def invalidar():
    _cache["d"] = None


def _alertas_de(c, hoy, cal):
    """Alertas de un cliente (solo A y B)."""
    if c["segmento"] == "C" or c["perdido"]:
        return []                            # C = ruido; perdido (12+ meses) se filtra, no se alerta
    out = []
    base = {"cc": c["cc"], "cliente": c["cliente"], "dueno": c["dueno"], "segmento": c["segmento"], "monto": c["t24_si"]}
    op = {"dueno": c["dueno"] or "", "sucursal": CRM.sucursales_vendedores().get(c["dueno"]) or "SE"}
    if c["sin_dueno"]:
        out.append({**base, "motivo": "sin_dueno", "nivel": 2, "destinatarios": [CRM.LIDER_VENTAS],
                    "texto": f"Sin dueño: lo atendía {(c['vendedor_top'] or 'nadie').title()}, que ya no está."})
    fc = c["fecha_compromiso"]
    if fc and fc <= hoy:
        dias = cal.entre(fc, hoy)
        nivel = CRM._nivel(dias, CRM.ESCALERA_NORMAL)
        out.append({**base, "motivo": "compromiso_vencido", "nivel": nivel, "destinatarios": CRM._destinatarios(op, nivel),
                    "texto": f"Compromiso «{c['proxima_accion'] or 'sin detalle'}» " + ("vence hoy." if dias == 0 else f"vencido hace {dias} días hábiles.")})
    ug = c["ultima_gestion"]
    if ug and (hoy - ug.date()).days <= SILENCIO_GESTION:
        return out                           # ya lo estan atendiendo
    umbral = max(60, round(2.5 * c["intervalo"])) if c["intervalo"] else None
    if umbral and c["dias_compra"] >= 3 and c["dias_sin_compra"] is not None and umbral < c["dias_sin_compra"] <= DIAS_PERDIDO:
        inicio = max(dt.date.fromisoformat(c["ult"]) + dt.timedelta(days=umbral), LANZAMIENTO)
        nivel = CRM._nivel(cal.entre(inicio, hoy), ESCALERA_CLIENTE)
        out.append({**base, "motivo": "dejo_de_comprar", "nivel": nivel, "destinatarios": CRM._destinatarios(op, nivel),
                    "texto": f"Dejó de comprar: {c['dias_sin_compra']} días sin compras (compraba cada ~{c['intervalo']} días)."})
    elif c["t12"] >= MONTO_B and c["t90"] < 0.5 * c["t12"] / 4:
        out.append({**base, "motivo": "bajo_ritmo", "nivel": 0, "destinatarios": CRM._destinatarios(op, 0),
                    "texto": f"Bajó su ritmo: {CRM._mm(c['t90'])} en 90 días contra {CRM._mm(c['t12'] / 4)} de un trimestre normal."})
    if c["cot_n"] >= 3 and c["cot_conv"] == 0 and (c["dias_sin_compra"] or 0) > 60:
        out.append({**base, "motivo": "cotiza_no_compra", "nivel": 0, "destinatarios": CRM._destinatarios(op, 0),
                    "texto": f"Cotiza y no compra: {c['cot_n']} cotizaciones en 60 días ({CRM._mm(c['cot_t'])}) sin pedido."})
    for a in out:
        a["nivel_txt"] = CRM.NIVELES[a["nivel"]]
    return out


def _cal(hoy):
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            return CRM._Calendario(cur, hoy - dt.timedelta(days=DIAS_CARTERA), hoy + dt.timedelta(days=1))


def _puede(perfil, c):
    return perfil.get("ver_todo") or (perfil.get("vendedor") and c["dueno"] == perfil.get("vendedor"))


def listar_pg(perfil):
    hoy = _hoy()
    cal = _cal(hoy)
    clientes = [c for c in _cartera() if _puede(perfil, c)]
    for c in clientes:
        c["alertas"] = [{"motivo": a["motivo"], "nivel": a["nivel"], "texto": a["texto"]} for a in _alertas_de(c, hoy, cal)]
    duenos = sorted({c["dueno"] for c in clientes if c["dueno"]})
    salida = [{k: (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v) for k, v in c.items()} for c in clientes]
    seg = defaultdict(lambda: {"n": 0, "t": 0.0})
    for c in clientes:
        seg[c["segmento"]]["n"] += 1; seg[c["segmento"]]["t"] += c["t24_si"]
    motivos = defaultdict(int)
    for c in clientes:
        for a in c["alertas"]:
            motivos[a["motivo"]] += 1
    return {"hoy": hoy.isoformat(), "clientes": salida, "duenos": duenos,
            "vendedores_reasignar": sorted({v for v in CRM.vendedores_piloto() if not CRM.retirado(v)} | set(CRM.REEMPLAZOS.values())),
            "segmentos": dict(seg), "alertas_por_motivo": dict(motivos), "tipos_gestion": CRM.TIPOS_GESTION,
            "puede_reasignar": bool(perfil.get("ver_todo"))}


def alertas_pg(perfil):
    hoy = _hoy()
    cal = _cal(hoy)
    u = perfil.get("usuario")
    out = []
    for c in _cartera():
        for a in _alertas_de(c, hoy, cal):
            para_mi = u in a["destinatarios"] or u in CRM.OBSERVADORES
            if para_mi or perfil.get("ver_todo") or _puede(perfil, c):
                out.append({**a, "para_mi": para_mi})
    out.sort(key=lambda a: (-a["nivel"], -a["monto"]))
    return out


def ficha_pg(cc, perfil):
    c = next((x for x in _cartera() if x["cc"] == cc), None)
    if not c or not _puede(perfil, c):
        return None
    hoy = _hoy()
    desde = hoy - dt.timedelta(days=DIAS_CARTERA)
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM cliente_maestro WHERE codigo_cliente=%s", (cc,))
            m = cur.fetchone() or {}
            cur.execute("""SELECT date_trunc('month', fecha_conta)::date AS mes, sum(total) AS t
                             FROM v_ventas WHERE codigo_cliente=%s AND fecha_conta >= %s GROUP BY 1 ORDER BY 1""", (cc, desde))
            serie = {r["mes"]: float(r["t"] or 0) for r in cur.fetchall()}
            cur.execute("""SELECT familia,
                                  sum(total) FILTER (WHERE fecha_conta > %(h12)s) AS t12,
                                  sum(total) FILTER (WHERE fecha_conta <= %(h12)s) AS t12p,
                                  sum(total) FILTER (WHERE fecha_conta > %(h6)s) AS t6
                             FROM v_ventas WHERE codigo_cliente=%(cc)s AND fecha_conta >= %(d)s
                            GROUP BY 1""", {"cc": cc, "d": desde, "h12": hoy - dt.timedelta(days=365), "h6": hoy - dt.timedelta(days=182)})
            fams = cur.fetchall()
            cur.execute("""SELECT fecha_conta AS f, doc_sap, max(sucursal_logica) AS s, max(vendedor) AS ve, sum(total) AS t
                             FROM v_ventas WHERE codigo_cliente=%s AND fecha_conta >= %s
                            GROUP BY 1, 2 ORDER BY 1 DESC, 2 DESC LIMIT 10""", (cc, desde))
            docs = cur.fetchall()
            cur.execute("""SELECT nro_cotizacion AS n, min(fecha) AS f, max(vendedor) AS ve, sum(total) AS t,
                                  bool_or(nro_pedido IS NOT NULL OR nro_factura IS NOT NULL) AS conv
                             FROM cotizacion_linea WHERE codigo_cliente=%s AND fecha >= %s
                            GROUP BY 1 ORDER BY 2 DESC LIMIT 12""", (cc, hoy - dt.timedelta(days=60)))
            cots = cur.fetchall()
            cur.execute("""SELECT id, etapa, monto, ultima_cot, dueno FROM crm_oportunidad
                            WHERE codigo_cliente=%s AND etapa NOT IN ('ganada','perdida') ORDER BY ultima_cot DESC""", (cc,))
            opps = cur.fetchall()
            cur.execute("""SELECT tipo, nota, proxima_accion, fecha_compromiso, usuario, creada_en FROM crm_cliente_gestion
                            WHERE codigo_cliente=%s ORDER BY creada_en DESC, id DESC LIMIT 30""", (cc,))
            gest = cur.fetchall()
            cal = CRM._Calendario(cur, desde, hoy + dt.timedelta(days=1))
    # 24 meses completos, con ceros donde no compro
    meses, d = [], hoy.replace(day=1)
    for _ in range(24):
        meses.append(d); d = (d - dt.timedelta(days=1)).replace(day=1)
    meses.reverse()
    compra = sorted([{"familia": f["familia"] or "—", "t12": float(f["t12"] or 0)} for f in fams if (f["t12"] or 0) > 0],
                    key=lambda x: -x["t12"])[:6]
    dejo = sorted([{"familia": f["familia"] or "—", "t12p": float(f["t12p"] or 0)} for f in fams
                   if float(f["t12p"] or 0) >= 300_000 and float(f["t6"] or 0) <= 0], key=lambda x: -x["t12p"])[:5]
    personal = ("contacto", "telefono", "celular", "correo", "direccion", "rut")
    return {
        **{k: (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v) for k, v in c.items()},
        "maestro": {k: (float(v) if hasattr(v, "is_finite") else v) for k, v in m.items()
                    if k in personal + ("comuna", "ciudad", "region", "condicion_pago", "linea_credito", "saldo_cuenta",
                                        "credito_disponible", "pedidos_pendientes", "congelado", "vendedor_sap")},
        "serie": [{"mes": x.isoformat(), "t": serie.get(x, 0.0)} for x in meses],
        "familias": compra, "familias_dejadas": dejo,
        "documentos": [{"fecha": r["f"].isoformat(), "doc": r["doc_sap"], "sucursal": r["s"], "vendedor": r["ve"], "monto": float(r["t"] or 0)} for r in docs],
        "cotizaciones": [{"nro": r["n"], "fecha": r["f"].isoformat(), "vendedor": r["ve"], "monto": float(r["t"] or 0), "convertida": r["conv"]} for r in cots],
        "oportunidades": [{"id": r["id"], "etapa": CRM.ETAPAS[r["etapa"]][0], "monto": float(r["monto"] or 0),
                           "ultima_cot": r["ultima_cot"].isoformat() if r["ultima_cot"] else None, "dueno": r["dueno"]} for r in opps],
        "gestiones": [{"tipo_txt": CRM.TIPOS_GESTION.get(g["tipo"], g["tipo"].capitalize()), "nota": g["nota"],
                       "proxima_accion": g["proxima_accion"],
                       "fecha_compromiso": g["fecha_compromiso"].isoformat() if g["fecha_compromiso"] else None,
                       "usuario": g["usuario"], "creada_en": g["creada_en"].isoformat()} for g in gest],
        "alertas": [{"motivo": a["motivo"], "nivel": a["nivel"], "nivel_txt": a["nivel_txt"], "texto": a["texto"]}
                    for a in _alertas_de(c, hoy, cal)],
    }


def registrar_gestion_pg(cc, perfil, datos, usuario):
    c = next((x for x in _cartera() if x["cc"] == cc), None)
    if not c or not _puede(perfil, c):
        raise PermissionError("No tienes acceso a este cliente.")
    tipo = datos.get("tipo")
    if tipo not in CRM.TIPOS_GESTION:
        raise ValueError("Elige el tipo de gestión.")
    nota = CRM._texto(datos.get("nota"), 2000)
    if not nota:
        raise ValueError("Escribe qué se hizo.")
    prox = CRM._texto(datos.get("proxima_accion"), 200)
    fcomp = CRM._fecha(datos.get("fecha_compromiso"))
    if fcomp and fcomp < _hoy():
        raise ValueError("La fecha del próximo compromiso no puede ser pasada.")
    if prox and not fcomp:
        raise ValueError("Indica la fecha del próximo compromiso.")
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO crm_cliente_gestion (codigo_cliente, tipo, nota, proxima_accion, fecha_compromiso, usuario)
                           VALUES (%s,%s,%s,%s,%s,%s)""", (cc, tipo, nota, prox, fcomp, usuario))
            cur.execute("""INSERT INTO crm_cliente (codigo_cliente, proxima_accion, fecha_compromiso, ultima_gestion, actualizada_por)
                           VALUES (%s,%s,%s,now(),%s)
                           ON CONFLICT (codigo_cliente) DO UPDATE SET proxima_accion=EXCLUDED.proxima_accion,
                             fecha_compromiso=EXCLUDED.fecha_compromiso, ultima_gestion=now(), actualizada_en=now(),
                             actualizada_por=EXCLUDED.actualizada_por""", (cc, prox, fcomp, usuario))
        conn.commit()
    invalidar()
    return {"ok": True}


def actualizar_pg(cc, perfil, cambios, usuario):
    if not perfil.get("ver_todo"):
        raise PermissionError("Solo la gerencia del CRM puede cambiar el dueño o las notas.")
    c = next((x for x in _cartera() if x["cc"] == cc), None)
    if not c:
        raise PermissionError("Cliente fuera de la cartera de San Isidro.")
    sets, vals, log = [], [], []
    if "dueno" in cambios:
        v = CRM._texto(cambios.get("dueno"), 120)
        if v and not CRM.vendedor_en_piloto(v):
            raise ValueError("Ese vendedor no está en San Isidro.")
        if v != c["dueno"]:
            sets.append("dueno"); vals.append(v)
            log.append(f"Dueño: {(c['dueno'] or 'sin dueño').title()} → {(v or 'calculado').title()}")
    if "notas" in cambios:
        sets.append("notas"); vals.append(CRM._texto(cambios.get("notas"), 2000))
    if not sets:
        return {"ok": True}
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cols = ", ".join(sets)
            ph = ", ".join(["%s"] * len(sets))
            upd = ", ".join(f"{s}=EXCLUDED.{s}" for s in sets)
            cur.execute(f"""INSERT INTO crm_cliente (codigo_cliente, {cols}, actualizada_por) VALUES (%s, {ph}, %s)
                            ON CONFLICT (codigo_cliente) DO UPDATE SET {upd}, actualizada_en=now(), actualizada_por=EXCLUDED.actualizada_por""",
                        [cc] + vals + [usuario])
            if log:
                cur.execute("INSERT INTO crm_cliente_gestion (codigo_cliente, tipo, nota, usuario) VALUES (%s,'cambio',%s,%s)",
                            (cc, " · ".join(log), usuario))
        conn.commit()
    invalidar()
    return {"ok": True}
