"""
Fotos historicas del inventario por producto (migracion 024).

inventario_stock es solo la ultima foto. Para poder cruzar la venta de una
fecha pasada con el stock de ese momento se guarda una foto cada lunes y el
ultimo dia de cada mes (decision del usuario 2026-10-23, "opcion A": ~60 MB
al año en vez de ~400 MB con fotos diarias). Solo filas con stock o transito
distinto de cero, en las 6 sucursales y E-commerce.

Para fechas sin foto (antes de la primera, o entre fotos) se estima el stock
de la EMPRESA hacia atras desde la foto siguiente (o el inventario actual):
    stock(t) = stock(foto) - recepciones(t, foto] + ventas(t, foto]
Solo a nivel empresa: los traspasos entre sucursales no estan en los datos.
No incluye ajustes de inventario ni mermas.
"""
from datetime import date, timedelta

import db

BODEGAS_FOTO = ["San Isidro", "Las Condes", "Manuel Rodríguez", "Matta", "Chicureo", "Maipú", "E-commerce"]
# Si por un feriado no hubo inventario un lunes, se toma igual la foto
# cuando la ultima ya tiene esta cantidad de dias.
MAX_DIAS_SIN_FOTO = 7


def _fin_de_mes(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1) == d


def guardar_foto_si_corresponde_pg(forzar=False):
    """La llama el cron diario. Fotografia el inventario cargado si su dia
    es lunes, fin de mes, o si la ultima foto ya tiene 7 dias o mas.
    Idempotente: si ya hay foto de ese dia no hace nada."""
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT (max(cargado_en) AT TIME ZONE 'America/Santiago')::date AS d FROM inventario_control""")
            dia = cur.fetchone()["d"]
            if dia is None:
                return {"foto": None, "motivo": "no hay inventario cargado"}
            cur.execute("SELECT max(fecha) AS f FROM inventario_foto")
            ultima = cur.fetchone()["f"]
            if ultima == dia:
                return {"foto": dia.isoformat(), "motivo": "ya existia"}
            motivo = ("forzada" if forzar else "lunes" if dia.weekday() == 0 else "fin de mes" if _fin_de_mes(dia)
                      else "primera foto" if ultima is None
                      else "%d dias sin foto" % (dia - ultima).days if (dia - ultima).days >= MAX_DIAS_SIN_FOTO else None)
            if not motivo:
                return {"foto": None, "motivo": "no corresponde (%s)" % dia.isoformat()}
            cur.execute(
                """INSERT INTO inventario_foto (fecha, codigo, bodega, stock, transito)
                   SELECT %(d)s, codigo, bodega, coalesce(stock, 0), coalesce(transito, 0)
                     FROM inventario_stock
                    WHERE bodega = ANY(%(b)s) AND (coalesce(stock, 0) <> 0 OR coalesce(transito, 0) <> 0)
                   ON CONFLICT DO NOTHING""",
                {"d": dia, "b": BODEGAS_FOTO},
            )
            n = cur.rowcount
        conn.commit()
    return {"foto": dia.isoformat(), "motivo": motivo, "filas": n}


def stock_en_fecha_pg(codigos, fecha):
    """Stock de cada codigo en `fecha`.
    - Si hay una foto en los 7 dias previos (o ese mismo dia): stock por
      sucursal de esa foto ("foto").
    - Si no: stock de la EMPRESA estimado hacia atras desde la foto siguiente
      o desde el inventario actual ("estimado").
    Devuelve {codigo: {"tipo", "fecha_base", "empresa", "por_sucursal"}}."""
    codigos = [int(c) for c in codigos]
    if not codigos:
        return {}
    res = {}
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT max(fecha) AS f FROM inventario_foto
                    WHERE fecha BETWEEN %(desde)s AND %(f)s""",
                {"desde": fecha - timedelta(days=MAX_DIAS_SIN_FOTO), "f": fecha},
            )
            foto = cur.fetchone()["f"]
            if foto:
                cur.execute(
                    """SELECT codigo, bodega, stock, transito FROM inventario_foto
                        WHERE fecha = %(f)s AND codigo = ANY(%(c)s)""",
                    {"f": foto, "c": codigos},
                )
                for r in cur.fetchall():
                    d = res.setdefault(r["codigo"], {"tipo": "foto", "fecha_base": foto.isoformat(), "empresa": 0.0, "por_sucursal": {}})
                    d["empresa"] += float(r["stock"])
                    d["por_sucursal"][r["bodega"]] = float(r["stock"])
                for c in codigos:
                    res.setdefault(c, {"tipo": "foto", "fecha_base": foto.isoformat(), "empresa": 0.0, "por_sucursal": {}})
                return res
            # estimado: desde la foto siguiente, o desde el inventario actual
            cur.execute("SELECT min(fecha) AS f FROM inventario_foto WHERE fecha > %s", (fecha,))
            sig = cur.fetchone()["f"]
            if sig:
                cur.execute("""SELECT codigo, sum(stock) AS st FROM inventario_foto
                                WHERE fecha = %(f)s AND codigo = ANY(%(c)s) GROUP BY codigo""", {"f": sig, "c": codigos})
                base = {r["codigo"]: float(r["st"]) for r in cur.fetchall()}
                fecha_base = sig
            else:
                cur.execute("""SELECT codigo, sum(coalesce(stock, 0)) AS st FROM inventario_stock
                                WHERE bodega = ANY(%(b)s) AND codigo = ANY(%(c)s) GROUP BY codigo""",
                            {"b": BODEGAS_FOTO, "c": codigos})
                base = {r["codigo"]: float(r["st"]) for r in cur.fetchall()}
                cur.execute("""SELECT (max(cargado_en) AT TIME ZONE 'America/Santiago')::date AS d FROM inventario_control""")
                fecha_base = cur.fetchone()["d"]
            cur.execute("""SELECT codigo, sum(cantidad) AS q FROM recepciones
                            WHERE codigo = ANY(%(c)s) AND fecha_recepcion > %(t)s AND fecha_recepcion <= %(b)s GROUP BY codigo""",
                        {"c": codigos, "t": fecha, "b": fecha_base})
            rec = {r["codigo"]: float(r["q"]) for r in cur.fetchall()}
            cur.execute("""SELECT codigo_cm AS codigo, sum(cantidad) AS q FROM ventas
                            WHERE codigo_cm = ANY(%(c)s) AND fecha_conta > %(t)s AND fecha_conta <= %(b)s GROUP BY codigo_cm""",
                        {"c": codigos, "t": fecha, "b": fecha_base})
            ven = {r["codigo"]: float(r["q"]) for r in cur.fetchall()}
    for c in codigos:
        res[c] = {"tipo": "estimado", "fecha_base": fecha_base.isoformat() if fecha_base else None,
                  "empresa": max(0.0, base.get(c, 0.0) - rec.get(c, 0.0) + ven.get(c, 0.0)), "por_sucursal": None}
    return res
