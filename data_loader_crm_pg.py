"""
CRM de proyectos (migracion 027, 2026-11-26). Piloto: SOLO las
oportunidades de Jorge Santana (VENDEDORES_PILOTO, decision del usuario).
El siguiente paso previsto es San Isidro completo (SUCURSALES_PILOTO:
vendedores de CMD, SE y CANAL DIGITAL en vendedor_home).

Que hace:
- sincronizar_pg(): arma las oportunidades desde cotizacion_linea. Toda
  cotizacion de $5 MM o mas de los ultimos 60 dias y SIN factura se agrega a la oportunidad
  abierta del mismo cliente + vendedor si la ultima version tiene menos de
  60 dias; si no, abre una nueva. Si alguna cotizacion tiene pedido o
  factura, la oportunidad pasa sola a "ganada". Corre al terminar cada
  subida de cotizaciones y en el cron diario.
- Alertas (se calculan desde las fechas, no se guardan a mano):
    * compromiso_vencido: la proxima accion llego a su fecha sin gestion.
    * sin_gestion: 7 dias habiles sin movimiento (oportunidad de $20 MM o
      mas) o 10 dias (de 5 a 20 MM). El reloj parte en la ultima version,
      la ultima gestion o el dia en que la oportunidad entro al CRM (asi el
      arranque del piloto no dispara alertas viejas).
    * sin_dueno: el vendedor ya no esta en la empresa.
  Escalera (decision del usuario): nivel 0 vendedor -> 1 jefe de sucursal
  -> 2 lider de ventas (Jorge Santana) -> 3 Gerente General (Enrique Musa).
  San Isidro no tiene jefe de sucursal: salta directo a Jorge. Un
  destinatario que ya estaba (ej. Jorge en sus propias oportunidades) no
  se repite.
- registrar_alertas_pg(): deja la huella en crm_alerta y manda el correo
  resumen diario (si estan las variables CRM_SMTP_*; si no, solo queda en
  Musa360).
"""
import datetime as dt
import os
import smtplib
import time
from collections import defaultdict
from email.mime.text import MIMEText
from html import escape
from zoneinfo import ZoneInfo

import db

MONTO_MIN = 5_000_000
MONTO_PROYECTO = 20_000_000
MONTO_GRANDE = 100_000_000
DIAS_COTIZACIONES = 60       # solo cotizaciones de los ultimos 60 dias (decision del usuario 2026-11-26)
DIAS_AGRUPAR = 60
DIAS_CERRADAS_VISIBLES = 60     # ganadas/perdidas en tablero y vista gerencia (el usuario: 60, no 90)
COTIZACIONES_EXCLUIDAS = (627636,)   # RUT digitado como precio (ver project-cotizaciones)

SUCURSALES_PILOTO = {"CMD", "SE", "CANAL DIGITAL"}
# El piloto partio solo con Jorge Santana; desde el 2026-11-26 (pedido del usuario) son todos
# los vendedores de San Isidro (SUCURSALES_PILOTO). Un set con nombres vuelve a acotarlo.
VENDEDORES_PILOTO = set()
# Pedido del usuario 2026-11-26: las oportunidades de TODOS los vendedores de San Isidro, pero la
# pantalla SOLO para la gerencia del CRM (Jorge Santana; tambien E. Musa y dsepulveda). Los vendedores
# no entran y las alertas parten en Jorge. True = cada vendedor del piloto ve y gestiona lo suyo.
ACCESO_VENDEDORES = False
JEFE_SUCURSAL = {
    "MT": "gcarrasco@casamusa.cl", "LC": "sarjona@casamusa.cl", "MR": "evalera@casamusa.cl",
    "CH": "jvillegas@casamusa.cl", "MP": "jvillegas@casamusa.cl",
    # CMD / SE / CANAL DIGITAL: sin jefe de sucursal -> directo al lider de ventas.
}
LIDER_VENTAS = "jsantana@casamusa.cl"
GERENTE_GENERAL = "emusa@casamusa.cl"
OBSERVADORES = {"dsepulveda@casamusa.cl"}       # ven todo y reciben copia del resumen
# Vendedores que se fueron (dotacion nov-2026): sus oportunidades quedan "sin dueño"...
VENDEDORES_RETIRADOS = ("FREDDY FLORES", "MARCELINO TORO", "NORAMBUENA", "TRIZIO")
# ...salvo que tengan reemplazo: la cartera pasa sola al que lo reemplaza (decision del usuario
# 2026-11-27: Marcelo Gatica reemplaza a Freddy Flores, quien habia reemplazado a Marcelino Toro).
REEMPLAZOS = {"FREDDY FLORES MIX": "MARCELO GATICA", "MARCELINO TORO MACHUCA": "MARCELO GATICA"}

# Dias habiles despues del inicio de la alerta para subir a jefe / lider / GG.
ESCALERA_NORMAL = (1, 3, 5)
ESCALERA_GRANDE = (1, 2, 3)      # oportunidades de $100 MM o mas

ETAPAS = {
    "cotizada": ("Cotizada", 10), "seguimiento": ("En seguimiento", 25),
    "negociacion": ("En negociación", 50), "ganada": ("Ganada", 100), "perdida": ("Perdida", 0),
}
TIPOS_GESTION = {"llamada": "Llamada", "visita": "Visita", "correo": "Correo", "whatsapp": "WhatsApp",
                 "reunion": "Reunión", "nota": "Nota"}
MOTIVOS_PERDIDA = ["Precio", "Plazo de entrega", "Sin stock", "Se fue con la competencia",
                   "Proyecto suspendido", "Sin respuesta", "Otro"]
CAMPOS_EDITABLES = ("etapa", "probabilidad", "fecha_cierre_est", "obra", "comuna", "competidor",
                    "motivo_perdida", "notas", "dueno")

# vendedor (nombre ERP) -> cuenta de Musa360. La llena app.py desde GERENTES.
CUENTAS_VENDEDOR = {}


def _desde():
    return _hoy() - dt.timedelta(days=DIAS_COTIZACIONES)


def _hoy():
    return dt.datetime.now(ZoneInfo("America/Santiago")).date()


def retirado(vendedor):
    v = (vendedor or "").upper()
    return any(x in v for x in VENDEDORES_RETIRADOS)


# ── vendedor_home (cache corto) ───────────────────────────────────────
_home_cache = {"t": 0, "d": {}}


def sucursales_vendedores():
    if time.time() - _home_cache["t"] > 300:
        _home_cache["d"] = {r["vendedor"]: r["sucursal"] for r in db.query_all(
            "SELECT vendedor, sucursal FROM vendedor_home")}
        _home_cache["t"] = time.time()
    return _home_cache["d"]


def vendedor_en_piloto(vendedor):
    if vendedor in REEMPLAZOS.values():
        return True                      # el reemplazo de un vendedor del piloto entra con su cartera
    if vendedor in REEMPLAZOS:
        return True                      # las cotizaciones del que se fue siguen contando (a nombre del reemplazo)
    if VENDEDORES_PILOTO:
        return vendedor in VENDEDORES_PILOTO
    return sucursales_vendedores().get(vendedor) in SUCURSALES_PILOTO


def vendedores_piloto():
    return sorted(v for v in sucursales_vendedores()
                  if vendedor_en_piloto(v) and not v.upper().startswith("VENTA"))


# ── calendario de dias habiles ─────────────────────────────────────────
class _Calendario:
    def __init__(self, cur, desde, hasta):
        cur.execute("SELECT fecha FROM dias_habiles_cl WHERE es_habil AND fecha BETWEEN %s AND %s", (desde, hasta))
        self.habiles = {r["fecha"] for r in cur.fetchall()}
        cur.execute("SELECT max(fecha) AS m FROM dias_habiles_cl")
        self.cubre_hasta = cur.fetchone()["m"] or dt.date.min

    def es_habil(self, d):
        if d <= self.cubre_hasta:
            return d in self.habiles
        return d.weekday() < 5        # calendario sin cargar: lunes a viernes

    def entre(self, desde, hasta):
        """Dias habiles d con desde < d <= hasta."""
        n, d = 0, desde
        while d < hasta:
            d += dt.timedelta(days=1)
            n += self.es_habil(d)
        return n


# ══════════════════════════════════════════════════════════════════════
#  Sincronizacion desde cotizaciones
# ══════════════════════════════════════════════════════════════════════
def sincronizar_pg():
    """Agrupa en Python y escribe por lotes (executemany va en modo
    pipeline): la base esta en Oregon y una ida y vuelta por cotizacion
    tomaba minutos."""
    cot = db.query_all(
        """SELECT nro_cotizacion AS n, min(fecha) AS fecha, max(codigo_cliente) AS cc,
                  max(nombre_cliente) AS cliente, max(vendedor) AS vendedor, sum(total) AS monto,
                  bool_or(nro_pedido IS NOT NULL OR nro_factura IS NOT NULL) AS conv,
                  sum(total) FILTER (WHERE nro_pedido IS NOT NULL OR nro_factura IS NOT NULL) AS monto_conv,
                  min(coalesce(fecha_factura, fecha_pedido)) FILTER (WHERE nro_pedido IS NOT NULL OR nro_factura IS NOT NULL) AS fconv
             FROM cotizacion_linea
            WHERE fecha >= %s AND NOT (nro_cotizacion = ANY(%s))
            GROUP BY nro_cotizacion
           HAVING sum(total) >= %s
              AND NOT bool_or(nro_factura IS NOT NULL)      -- con factura: ya se vendio, no se sigue
            ORDER BY min(fecha), nro_cotizacion""",
        (_desde(), list(COTIZACIONES_EXCLUIDAS), MONTO_MIN))
    home = sucursales_vendedores()
    cot = [c for c in cot if vendedor_en_piloto(c["vendedor"])]
    vigentes = [c["n"] for c in cot]
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            # Limpieza: salen las cotizaciones que se facturaron o pasaron los 60 dias. Una oportunidad
            # que se queda sin cotizaciones se borra, salvo que tenga gestiones reales del vendedor/lider
            # (se conserva para no perder ese trabajo).
            cur.execute("DELETE FROM crm_oportunidad_cotizacion WHERE NOT (nro_cotizacion = ANY(%s))", (vigentes,))
            cur.execute("""DELETE FROM crm_oportunidad o
                            WHERE NOT EXISTS (SELECT 1 FROM crm_oportunidad_cotizacion c WHERE c.oportunidad_id = o.id)
                              AND NOT EXISTS (SELECT 1 FROM crm_gestion g WHERE g.oportunidad_id = o.id AND g.tipo = ANY(%s))""",
                        (list(TIPOS_GESTION),))
            cur.execute("SELECT nro_cotizacion FROM crm_oportunidad_cotizacion")
            ya = {r["nro_cotizacion"] for r in cur.fetchall()}
            cur.execute("""SELECT id, codigo_cliente, vendedor, ultima_cot FROM crm_oportunidad
                            WHERE etapa NOT IN ('ganada','perdida')""")
            # clave -> ["id" o ("nuevo", k), ultima fecha]
            abiertas = {(r["codigo_cliente"], r["vendedor"]): [r["id"], r["ultima_cot"]] for r in cur.fetchall()}

            existentes = [c for c in cot if c["n"] in ya]
            nuevas_cot = [c for c in cot if c["n"] not in ya]
            grupos = []            # oportunidades nuevas: dict con la primera cotizacion
            enlaces = []           # (cotizacion, destino) destino = id existente o ("nuevo", k)
            vinculadas = 0
            for c in nuevas_cot:
                boleta = (c["cliente"] or "").upper().startswith("CLIENTE BOLETA")
                clave = (f"BOL-{c['n']}" if boleta else c["cc"], c["vendedor"])
                op = abiertas.get(clave)
                if op and op[1] and (c["fecha"] - op[1]).days <= DIAS_AGRUPAR:
                    destino = op[0]
                    op[1] = max(op[1], c["fecha"])
                    vinculadas += 1
                else:
                    destino = ("nuevo", len(grupos))
                    grupos.append({"clave": clave, "c": c})
                    abiertas[clave] = [destino, c["fecha"]]
                enlaces.append((c, destino))

            ids_nuevos = []
            if grupos:
                cur.executemany(
                    """INSERT INTO crm_oportunidad (codigo_cliente, cliente, vendedor, dueno, sucursal,
                                                   monto, primera_cot, ultima_cot, actualizada_por)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'sistema') RETURNING id""",
                    [(g["clave"][0], g["c"]["cliente"], g["c"]["vendedor"],
                      REEMPLAZOS.get(g["c"]["vendedor"], g["c"]["vendedor"]),     # dueño: el reemplazo si se fue
                      home.get(g["c"]["vendedor"]) or home.get(REEMPLAZOS.get(g["c"]["vendedor"])),
                      g["c"]["monto"], g["c"]["fecha"], g["c"]["fecha"]) for g in grupos],
                    returning=True)
                while True:
                    ids_nuevos.append(cur.fetchone()["id"])
                    if not cur.nextset():
                        break
                cur.executemany(
                    """INSERT INTO crm_gestion (oportunidad_id, tipo, nota, usuario) VALUES (%s,'sistema',%s,'sistema')""",
                    [(ids_nuevos[k], f"Oportunidad creada desde la cotización N° {g['c']['n']}.") for k, g in enumerate(grupos)])
            if enlaces:
                cur.executemany(
                    """INSERT INTO crm_oportunidad_cotizacion
                         (nro_cotizacion, oportunidad_id, fecha, monto, convertida, monto_convertido, fecha_conversion)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (nro_cotizacion) DO NOTHING""",
                    [(c["n"], ids_nuevos[d[1]] if isinstance(d, tuple) else d, c["fecha"], c["monto"],
                      c["conv"], c["monto_conv"], c["fconv"]) for c, d in enlaces])
            if existentes:
                cur.executemany(
                    """UPDATE crm_oportunidad_cotizacion
                          SET monto=%s, convertida=%s, monto_convertido=%s, fecha_conversion=%s
                        WHERE nro_cotizacion=%s
                          AND (monto, convertida, monto_convertido, fecha_conversion)
                              IS DISTINCT FROM (%s::numeric, %s::boolean, %s::numeric, %s::date)""",
                    [(c["monto"], c["conv"], c["monto_conv"], c["fconv"], c["n"],
                      c["monto"], c["conv"], c["monto_conv"], c["fconv"]) for c in existentes])
            # Totales de cada oportunidad (una sola sentencia para todas).
            cur.execute("""UPDATE crm_oportunidad o
                              SET monto = a.monto, primera_cot = a.primera, ultima_cot = a.ultima
                             FROM (SELECT oportunidad_id, min(fecha) AS primera, max(fecha) AS ultima,
                                          (array_agg(monto ORDER BY fecha DESC, nro_cotizacion DESC))[1] AS monto
                                     FROM crm_oportunidad_cotizacion GROUP BY 1) a
                            WHERE o.id = a.oportunidad_id
                              AND (o.monto, o.primera_cot, o.ultima_cot) IS DISTINCT FROM (a.monto, a.primera, a.ultima)""")
            # Ganadas automaticas: alguna version con pedido o factura.
            cur.execute("""UPDATE crm_oportunidad o
                              SET etapa='ganada', ganada_en=a.f, cerrada_en=a.f, monto_ganado=a.m,
                                  fecha_compromiso=NULL, actualizada_en=now(), actualizada_por='sistema'
                             FROM (SELECT oportunidad_id, coalesce(min(fecha_conversion), %s) AS f,
                                          sum(coalesce(monto_convertido, 0)) AS m, min(nro_cotizacion) AS nro
                                     FROM crm_oportunidad_cotizacion WHERE convertida GROUP BY 1) a
                            WHERE o.id = a.oportunidad_id AND o.etapa <> 'ganada'
                        RETURNING o.id, a.nro""", (_hoy(),))
            ganadas = cur.fetchall()
            if ganadas:
                cur.executemany(
                    """INSERT INTO crm_gestion (oportunidad_id, tipo, nota, usuario) VALUES (%s,'sistema',%s,'sistema')""",
                    [(g["id"], f"Ganada automáticamente: la cotización N° {g['nro']} tiene pedido o factura en el ERP.")
                     for g in ganadas])
                cur.execute("""UPDATE crm_alerta SET resuelta_en=now()
                                WHERE oportunidad_id = ANY(%s) AND resuelta_en IS NULL""", ([g["id"] for g in ganadas],))
        conn.commit()
    return {"cotizaciones": len(cot), "creadas": len(grupos), "vinculadas": vinculadas, "ganadas": len(ganadas)}


# ══════════════════════════════════════════════════════════════════════
#  Permisos
#  perfil = {"usuario", "ver_todo": bool, "vendedor": str|None, "sucursales": set}
# ══════════════════════════════════════════════════════════════════════
def _filtro(perfil, alias="o"):
    if perfil.get("ver_todo"):
        return "TRUE", []
    conds, params = [], []
    if perfil.get("vendedor"):
        conds.append(f"{alias}.dueno = %s"); params.append(perfil["vendedor"])
    if perfil.get("sucursales"):
        conds.append(f"{alias}.sucursal = ANY(%s)"); params.append(list(perfil["sucursales"]))
    return ("(" + " OR ".join(conds) + ")") if conds else "FALSE", params


def _puede(perfil, op):
    if perfil.get("ver_todo"):
        return True
    return op["dueno"] == perfil.get("vendedor") or op["sucursal"] in (perfil.get("sucursales") or set())


def _fila(o, hoy, cal):
    base = max(x for x in (o["ultima_cot"], o["creada_en"].date(),
                           o["ultima_gestion"].date() if o["ultima_gestion"] else None) if x)
    abierta = o["etapa"] not in ("ganada", "perdida")
    return {
        "id": o["id"], "cliente": o["cliente"], "codigo_cliente": o["codigo_cliente"],
        "vendedor": o["vendedor"], "dueno": o["dueno"], "sucursal": o["sucursal"],
        "sin_dueno": retirado(o["dueno"]), "etapa": o["etapa"], "etapa_txt": ETAPAS[o["etapa"]][0],
        "probabilidad": o["probabilidad"] if o["probabilidad"] is not None else ETAPAS[o["etapa"]][1],
        "monto": float(o["monto"] or 0), "monto_ganado": float(o["monto_ganado"] or 0),
        "primera_cot": o["primera_cot"].isoformat() if o["primera_cot"] else None,
        "ultima_cot": o["ultima_cot"].isoformat() if o["ultima_cot"] else None,
        "versiones": o.get("versiones") or 0, "marcas": o.get("marcas") or [],
        "fecha_cierre_est": o["fecha_cierre_est"].isoformat() if o["fecha_cierre_est"] else None,
        "obra": o["obra"], "comuna": o["comuna"], "competidor": o["competidor"],
        "motivo_perdida": o["motivo_perdida"], "notas": o["notas"],
        "proxima_accion": o["proxima_accion"],
        "fecha_compromiso": o["fecha_compromiso"].isoformat() if o["fecha_compromiso"] else None,
        "ultima_gestion": o["ultima_gestion"].isoformat() if o["ultima_gestion"] else None,
        "dias_sin_mov": cal.entre(base, hoy) if abierta else None,
        "ganada_en": o["ganada_en"].isoformat() if o["ganada_en"] else None,
        "cerrada_en": o["cerrada_en"].isoformat() if o["cerrada_en"] else None,
    }


def _cargar(cur, perfil, where_extra="", params_extra=()):
    w, p = _filtro(perfil)
    cur.execute(
        f"""SELECT o.*, (SELECT count(*) FROM crm_oportunidad_cotizacion c WHERE c.oportunidad_id=o.id) AS versiones,
                   (SELECT array_agg(m ORDER BY t DESC) FROM (
                        SELECT l.marca AS m, sum(l.total) AS t FROM crm_oportunidad_cotizacion c
                          JOIN cotizacion_linea l ON l.nro_cotizacion = c.nro_cotizacion
                         WHERE c.oportunidad_id = o.id AND l.marca IS NOT NULL
                         GROUP BY 1 ORDER BY 2 DESC LIMIT 3) x) AS marcas
              FROM crm_oportunidad o
             WHERE {w} {where_extra}
             ORDER BY o.monto DESC""", p + list(params_extra))
    return cur.fetchall()


def listar_pg(perfil):
    hoy = _hoy()
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            filas = _cargar(cur, perfil, "AND (o.etapa NOT IN ('ganada','perdida') OR o.cerrada_en >= %s)",
                            (hoy - dt.timedelta(days=DIAS_CERRADAS_VISIBLES),))
            cal = _Calendario(cur, hoy - dt.timedelta(days=DIAS_COTIZACIONES + 60), hoy)
    ops = [_fila(o, hoy, cal) for o in filas]
    alertas = alertas_pg(perfil)
    por_op = defaultdict(list)
    for a in alertas:
        por_op[a["oportunidad_id"]].append({"motivo": a["motivo"], "nivel": a["nivel"], "texto": a["texto"]})
    for o in ops:
        o["alertas"] = por_op.get(o["id"], [])
    return {"hoy": hoy.isoformat(), "oportunidades": ops,
            # Solo vendedores con oportunidades abiertas (un vendedor sin cotizaciones abiertas no aparece).
            "vendedores": sorted({o["dueno"] for o in ops if o["etapa"] not in ("ganada", "perdida")})
                          if perfil.get("ver_todo") else [],
            "vendedores_reasignar": sorted({v for v in vendedores_piloto() if not retirado(v)} | set(REEMPLAZOS.values()))
                                    if perfil.get("ver_todo") else [],
            "etapas": [{"k": k, "n": v[0], "p": v[1]} for k, v in ETAPAS.items()],
            "tipos_gestion": TIPOS_GESTION, "motivos_perdida": MOTIVOS_PERDIDA,
            "puede_reasignar": bool(perfil.get("ver_todo"))}


def detalle_pg(op_id, perfil):
    hoy = _hoy()
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            filas = _cargar(cur, perfil, "AND o.id = %s", (op_id,))
            if not filas:
                return None
            cal = _Calendario(cur, hoy - dt.timedelta(days=DIAS_COTIZACIONES + 60), hoy)
            cur.execute("""SELECT nro_cotizacion, fecha, monto, convertida, monto_convertido, fecha_conversion
                             FROM crm_oportunidad_cotizacion WHERE oportunidad_id=%s ORDER BY fecha DESC, nro_cotizacion DESC""",
                        (op_id,))
            cots = cur.fetchall()
            cur.execute("""SELECT tipo, nota, proxima_accion, fecha_compromiso, usuario, creada_en
                             FROM crm_gestion WHERE oportunidad_id=%s ORDER BY creada_en DESC, id DESC""", (op_id,))
            gest = cur.fetchall()
    o = _fila(filas[0], hoy, cal)
    o["cotizaciones"] = [{"nro": c["nro_cotizacion"], "fecha": c["fecha"].isoformat(), "monto": float(c["monto"] or 0),
                          "convertida": c["convertida"], "monto_convertido": float(c["monto_convertido"] or 0)}
                         for c in cots]
    o["gestiones"] = [{"tipo": g["tipo"], "tipo_txt": TIPOS_GESTION.get(g["tipo"], g["tipo"].capitalize()),
                       "nota": g["nota"], "proxima_accion": g["proxima_accion"],
                       "fecha_compromiso": g["fecha_compromiso"].isoformat() if g["fecha_compromiso"] else None,
                       "usuario": g["usuario"], "creada_en": g["creada_en"].isoformat()} for g in gest]
    o["alertas"] = [a for a in alertas_pg(perfil) if a["oportunidad_id"] == op_id]
    return o


def _texto(v, n=500):
    v = (v or "").strip()
    return v[:n] or None


def _fecha(v):
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        raise ValueError("Fecha inválida.")


def actualizar_pg(op_id, perfil, cambios, usuario):
    cambios = {k: v for k, v in (cambios or {}).items() if k in CAMPOS_EDITABLES}
    if "dueno" in cambios and not perfil.get("ver_todo"):
        raise PermissionError("Solo la gerencia puede reasignar.")
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM crm_oportunidad WHERE id=%s FOR UPDATE", (op_id,))
            op = cur.fetchone()
            if not op or not _puede(perfil, op):
                raise PermissionError("No tienes acceso a esta oportunidad.")
            sets, vals, log = [], [], []
            for k, v in cambios.items():
                if k == "etapa":
                    if v not in ETAPAS:
                        raise ValueError("Etapa inválida.")
                    if v == op["etapa"]:
                        continue
                    if v == "perdida" and not _texto(cambios.get("motivo_perdida") or op["motivo_perdida"]):
                        raise ValueError("Para dar una oportunidad por perdida indica el motivo.")
                    sets += ["etapa=%s"]; vals += [v]
                    if v in ("ganada", "perdida"):
                        sets += ["cerrada_en=%s", "fecha_compromiso=NULL"]; vals += [_hoy()]
                        if v == "ganada":
                            sets += ["ganada_en=%s"]; vals += [_hoy()]
                    else:
                        sets += ["cerrada_en=NULL"]
                    log.append(f"Etapa: {ETAPAS[op['etapa']][0]} → {ETAPAS[v][0]}")
                elif k == "probabilidad":
                    p = None if v in (None, "") else max(0, min(100, int(v)))
                    sets += ["probabilidad=%s"]; vals += [p]
                elif k == "fecha_cierre_est":
                    sets += ["fecha_cierre_est=%s"]; vals += [_fecha(v)]
                elif k == "dueno":
                    v = _texto(v, 120)
                    if not v or v == op["dueno"]:
                        continue
                    if not vendedor_en_piloto(v):
                        raise ValueError("Ese vendedor no está en el piloto.")
                    # Un reemplazo (ej. Marcelo Gatica, que figura en Maipu) hereda la sucursal de la oportunidad.
                    suc_nueva = op["sucursal"] if v in REEMPLAZOS.values() else sucursales_vendedores().get(v)
                    sets += ["dueno=%s", "sucursal=%s"]; vals += [v, suc_nueva]
                    log.append(f"Reasignada de {op['dueno'].title()} a {v.title()}")
                else:
                    nuevo = _texto(v, 2000 if k == "notas" else 200)
                    if (op[k] or None) == nuevo:
                        continue
                    sets += [f"{k}=%s"]; vals += [nuevo]
                    if k == "motivo_perdida" and nuevo:
                        log.append(f"Motivo de pérdida: {nuevo}")
                    if k == "competidor" and nuevo:
                        log.append(f"Competidor: {nuevo}")
            if sets:
                cur.execute(f"UPDATE crm_oportunidad SET {', '.join(sets)}, actualizada_en=now(), actualizada_por=%s WHERE id=%s",
                            vals + [usuario, op_id])
            if log:
                cur.execute("INSERT INTO crm_gestion (oportunidad_id, tipo, nota, usuario) VALUES (%s,'cambio',%s,%s)",
                            (op_id, " · ".join(log), usuario))
            if any(s.startswith("dueno") or s.startswith("cerrada_en=%s") for s in sets):
                cur.execute("""UPDATE crm_alerta SET resuelta_en=now()
                                WHERE oportunidad_id=%s AND resuelta_en IS NULL""", (op_id,))
        conn.commit()
    return {"ok": True}


def registrar_gestion_pg(op_id, perfil, datos, usuario):
    tipo = datos.get("tipo")
    if tipo not in TIPOS_GESTION:
        raise ValueError("Elige el tipo de gestión.")
    nota = _texto(datos.get("nota"), 2000)
    if not nota:
        raise ValueError("Escribe qué se hizo.")
    prox = _texto(datos.get("proxima_accion"), 200)
    fcomp = _fecha(datos.get("fecha_compromiso"))
    if fcomp and fcomp < _hoy():
        raise ValueError("La fecha del próximo compromiso no puede ser pasada.")
    if prox and not fcomp:
        raise ValueError("Indica la fecha del próximo compromiso.")
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM crm_oportunidad WHERE id=%s FOR UPDATE", (op_id,))
            op = cur.fetchone()
            if not op or not _puede(perfil, op):
                raise PermissionError("No tienes acceso a esta oportunidad.")
            cur.execute("""INSERT INTO crm_gestion (oportunidad_id, tipo, nota, proxima_accion, fecha_compromiso, usuario)
                           VALUES (%s,%s,%s,%s,%s,%s)""", (op_id, tipo, nota, prox, fcomp, usuario))
            # Registrar una gestion cumple el compromiso anterior y reinicia el reloj.
            cur.execute("""UPDATE crm_oportunidad SET ultima_gestion=now(), proxima_accion=%s, fecha_compromiso=%s,
                                  etapa = CASE WHEN etapa='cotizada' THEN 'seguimiento' ELSE etapa END,
                                  actualizada_en=now(), actualizada_por=%s
                            WHERE id=%s""", (prox, fcomp, usuario, op_id))
            cur.execute("""UPDATE crm_alerta SET resuelta_en=now()
                            WHERE oportunidad_id=%s AND resuelta_en IS NULL AND motivo <> 'sin_dueno'""", (op_id,))
        conn.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════
#  Alertas y escalamiento
# ══════════════════════════════════════════════════════════════════════
NIVELES = ["Vendedor", "Jefe de sucursal", "Líder de ventas", "Gerente General"]


def _cadena(op):
    # Sin acceso de vendedores, el primer aviso (nivel 0) le llega directo al lider de ventas.
    vendedor = CUENTAS_VENDEDOR.get(op["dueno"]) if ACCESO_VENDEDORES and not retirado(op["dueno"]) else LIDER_VENTAS
    return [vendedor, JEFE_SUCURSAL.get(op["sucursal"]), LIDER_VENTAS, GERENTE_GENERAL]


def _destinatarios(op, nivel):
    out = []
    for u in _cadena(op)[:nivel + 1]:
        if u and u not in out:
            out.append(u)
    return out


def _nivel(dias, escalera):
    return 3 if dias >= escalera[2] else 2 if dias >= escalera[1] else 1 if dias >= escalera[0] else 0


def calcular_alertas_pg():
    """Todas las alertas vigentes del piloto (sin filtrar por usuario)."""
    hoy = _hoy()
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, cliente, dueno, sucursal, monto, ultima_cot, ultima_gestion, creada_en,
                                  proxima_accion, fecha_compromiso
                             FROM crm_oportunidad WHERE etapa NOT IN ('ganada','perdida')""")
            ops = cur.fetchall()
            cal = _Calendario(cur, hoy - dt.timedelta(days=DIAS_COTIZACIONES + 60), hoy + dt.timedelta(days=1))
    out = []
    for op in ops:
        monto = float(op["monto"] or 0)
        esc = ESCALERA_GRANDE if monto >= MONTO_GRANDE else ESCALERA_NORMAL
        base = {"oportunidad_id": op["id"], "cliente": op["cliente"], "dueno": op["dueno"],
                "sucursal": op["sucursal"], "monto": monto}
        if retirado(op["dueno"]):
            out.append({**base, "motivo": "sin_dueno", "referencia": op["creada_en"].date(), "nivel": 2,
                        "texto": f"{op['dueno'].title()} ya no está en la empresa: reasignar.",
                        "destinatarios": [u for u in [LIDER_VENTAS] if u]})
            continue
        fc = op["fecha_compromiso"]
        if fc and fc <= hoy:
            dias = cal.entre(fc, hoy)
            nivel = _nivel(dias, esc)
            out.append({**base, "motivo": "compromiso_vencido", "referencia": fc, "nivel": nivel,
                        "texto": (f"Compromiso «{op['proxima_accion'] or 'sin detalle'}» "
                                  + ("vence hoy." if dias == 0 else f"vencido hace {dias} día{'s' if dias > 1 else ''} hábil{'es' if dias > 1 else ''}.")),
                        "destinatarios": _destinatarios(op, nivel)})
            continue
        if fc and fc > hoy:
            continue        # tiene una accion planificada: no se alerta por falta de gestion
        ref = max(x for x in (op["ultima_cot"], op["creada_en"].date(),
                              op["ultima_gestion"].date() if op["ultima_gestion"] else None) if x)
        dias = cal.entre(ref, hoy)
        umbral = 7 if monto >= MONTO_PROYECTO else 10
        if dias >= umbral:
            nivel = _nivel(dias - umbral, esc)
            out.append({**base, "motivo": "sin_gestion", "referencia": ref, "nivel": nivel,
                        "texto": f"{dias} días hábiles sin gestiones ni compromiso.",
                        "destinatarios": _destinatarios(op, nivel)})
    for a in out:
        a["nivel_txt"] = NIVELES[a["nivel"]]
    return out


def alertas_pg(perfil):
    usuario = perfil.get("usuario")
    out = []
    for a in calcular_alertas_pg():
        para_mi = usuario in a["destinatarios"] or (usuario in OBSERVADORES)
        visible = para_mi or perfil.get("ver_todo") or _puede(perfil, a)
        if visible:
            out.append({**a, "referencia": a["referencia"].isoformat(), "para_mi": para_mi})
    out.sort(key=lambda a: (-a["nivel"], -a["monto"]))
    return out


def conteo_alertas_pg(perfil):
    return sum(1 for a in alertas_pg(perfil) if a["para_mi"])


def resumen_pg(perfil):
    datos = listar_pg(perfil)
    ops = datos["oportunidades"]
    ab = [o for o in ops if o["etapa"] not in ("ganada", "perdida")]
    gan = [o for o in ops if o["etapa"] == "ganada"]
    per = [o for o in ops if o["etapa"] == "perdida"]
    v = defaultdict(lambda: {"n": 0, "abierto": 0.0, "ganadas": 0, "perdidas": 0, "alertas": 0, "sin_dueno": False})
    for o in ops:
        x = v[o["dueno"]]
        x["n"] += 1; x["sin_dueno"] = o["sin_dueno"]
        if o["etapa"] == "ganada": x["ganadas"] += 1
        elif o["etapa"] == "perdida": x["perdidas"] += 1
        else: x["abierto"] += o["monto"]
        x["alertas"] += len(o["alertas"])
    motivos = defaultdict(int)
    for o in per:
        motivos[o["motivo_perdida"] or "Sin motivo"] += 1
    return {
        "abierto": sum(o["monto"] for o in ab), "n_abiertas": len(ab),
        "ponderado": sum(o["monto"] * o["probabilidad"] / 100 for o in ab),
        "ganadas": len(gan), "monto_ganado": sum(o["monto_ganado"] or o["monto"] for o in gan),
        "perdidas": len(per), "motivos_perdida": dict(motivos),
        "sin_dueno": [o for o in ab if o["sin_dueno"]],
        "alertas": sum(len(o["alertas"]) for o in ab),
        # Ganadas: lo que efectivamente tiene pedido o factura (igual que la columna del tablero).
        "por_etapa": {k: sum((o["monto_ganado"] or o["monto"]) if k == "ganada" else o["monto"]
                             for o in ops if o["etapa"] == k) for k in ETAPAS},
        "por_vendedor": [{"vendedor": k, **x} for k, x in sorted(v.items(), key=lambda kv: -kv[1]["abierto"])],
    }


def registrar_alertas_pg(enviar_correo=True):
    """Cron diario: deja la huella en crm_alerta (nivel y escalamiento),
    cierra las que ya no aplican y manda el resumen por correo."""
    alertas = calcular_alertas_pg()
    vigentes = {(a["oportunidad_id"], a["motivo"], a["referencia"]) for a in alertas}
    nuevas = escaladas = 0
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, oportunidad_id, motivo, referencia, nivel FROM crm_alerta WHERE resuelta_en IS NULL")
            previas = {(r["oportunidad_id"], r["motivo"], r["referencia"]): r for r in cur.fetchall()}
            for clave, r in previas.items():
                if clave not in vigentes:
                    cur.execute("UPDATE crm_alerta SET resuelta_en=now() WHERE id=%s", (r["id"],))
            for a in alertas:
                clave = (a["oportunidad_id"], a["motivo"], a["referencia"])
                r = previas.get(clave)
                if r is None:
                    cur.execute("""INSERT INTO crm_alerta (oportunidad_id, motivo, referencia, nivel)
                                   VALUES (%s,%s,%s,%s) ON CONFLICT (oportunidad_id, motivo, referencia)
                                   DO UPDATE SET nivel=EXCLUDED.nivel, resuelta_en=NULL""", clave + (a["nivel"],))
                    nuevas += 1
                    a["escalo_hoy"] = True
                elif a["nivel"] > r["nivel"]:
                    cur.execute("UPDATE crm_alerta SET nivel=%s, escalada_en=now() WHERE id=%s", (a["nivel"], r["id"]))
                    escaladas += 1
                    a["escalo_hoy"] = True
        conn.commit()
    correos = _enviar_resumenes(alertas) if enviar_correo else {"enviados": 0, "motivo": "desactivado"}
    return {"alertas": len(alertas), "nuevas": nuevas, "escaladas": escaladas, "correo": correos}


def _enviar_resumenes(alertas):
    user, pwd = os.environ.get("CRM_SMTP_USER"), os.environ.get("CRM_SMTP_PASSWORD")
    if not user or not pwd:
        return {"enviados": 0, "motivo": "sin CRM_SMTP_USER/CRM_SMTP_PASSWORD"}
    por_dest = defaultdict(list)
    for a in alertas:
        for d in set(a["destinatarios"]) | OBSERVADORES:
            if "@" in (d or ""):
                por_dest[d].append(a)
    if not por_dest:
        return {"enviados": 0, "motivo": "sin alertas"}
    host = os.environ.get("CRM_SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("CRM_SMTP_PORT", "587"))
    remitente = os.environ.get("CRM_MAIL_FROM", user)
    enviados, errores = 0, []
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, pwd)
        for dest, lista in por_dest.items():
            lista = sorted(lista, key=lambda a: (-a["nivel"], -a["monto"]))
            msg = MIMEText(_html_resumen(lista), "html", "utf-8")
            msg["Subject"] = f"CRM Musa360: {len(lista)} alerta{'s' if len(lista) > 1 else ''} de seguimiento"
            msg["From"] = remitente
            msg["To"] = dest
            try:
                s.sendmail(remitente, [dest], msg.as_string())
                enviados += 1
            except smtplib.SMTPException as e:
                errores.append(f"{dest}: {e}")
    return {"enviados": enviados, "errores": errores}


def _html_resumen(lista):
    filas = "".join(
        f"""<tr><td style="padding:6px 8px;border-bottom:1px solid #ddd">{'🔺 ' if a.get('escalo_hoy') else ''}{escape(a['nivel_txt'])}</td>
            <td style="padding:6px 8px;border-bottom:1px solid #ddd"><b>{escape((a['cliente'] or '').title())}</b><br>
            <span style="color:#666">{escape(a['dueno'].title())} · {_mm(a['monto'])}</span></td>
            <td style="padding:6px 8px;border-bottom:1px solid #ddd">{escape(a['texto'])}</td></tr>"""
        for a in lista)
    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222">
      <p>Oportunidades de proyectos que necesitan seguimiento hoy. 🔺 = subió de nivel hoy.</p>
      <table style="border-collapse:collapse;font-size:13px"><tr style="text-align:left;color:#666">
      <th style="padding:6px 8px">Nivel</th><th style="padding:6px 8px">Oportunidad</th><th style="padding:6px 8px">Alerta</th></tr>{filas}</table>
      <p><a href="https://www.musa360.cl/crm">Abrir el CRM en Musa360</a></p></div>"""


def _mm(v):
    return "$" + f"{v / 1e6:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".") + " MM"
