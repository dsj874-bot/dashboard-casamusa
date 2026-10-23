"""Herramientas del Analista IA (fase 1: ventas y compras).

Cada herramienta es una consulta de solo lectura, parametrizada, que devuelve
un resultado YA CALCULADO y corto (totales y tablas de pocas filas): la IA
solo elige la herramienta y redacta; los numeros salen de Musa360 con los
mismos criterios de las pantallas.

Criterios (iguales a las pantallas de Comercial):
- Venta = v_ventas.total (neto, sin IVA; las notas de credito restan).
- Margen = v_ventas.utilidad_bruta, ya ajustado al reporte del ERP (migracion 015).
- Sucursal = sucursal_logica; vendedor = vendedor_rpt.
- Stock = inventario_stock de las 6 sucursales; venta mensual = la del inventario.
"""
import math
import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import psycopg

import db
import data_loader_inventario_foto as dif

SUCURSALES_VENTA = {
    "LC": "Las Condes", "MR": "Manuel Rodríguez", "MT": "Matta", "CH": "Chicureo", "MP": "Maipú",
    "SE": "San Isidro (empresas)", "CMD": "San Isidro (distribución)", "CANAL DIGITAL": "Canal digital",
    "OF": "Oficina",
}
BODEGAS = ["San Isidro", "Las Condes", "Manuel Rodríguez", "Matta", "Chicureo", "Maipú"]
DIMENSIONES = {
    "sucursal": "sucursal_logica", "vendedor": "vendedor_rpt", "cliente": "nombre_cliente",
    "marca": "marca", "familia": "familia", "subfamilia": "subfamilia", "canal": "tipo_venta",
    "procedencia": "procedencia", "producto": "codigo_cm", "mes": "extract(month FROM fecha_conta)::int",
}
MAX_FILAS = 25
# El margen se ajusta al reporte del ERP desde 2026 (migracion 015); el de
# anos anteriores sale del costo del export y NO es comparable.
ANO_MARGEN_CONFIABLE = 2026


# ══════════════════════════════════════════════════════
#  Utilidades
# ══════════════════════════════════════════════════════
def _fecha(v, defecto):
    if not v:
        return defecto
    return date.fromisoformat(str(v)[:10])


def _menos_un_ano(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 de febrero
        return d.replace(year=d.year - 1, day=28)


def _pct(a, b):
    return round((a / b - 1) * 100, 1) if b else None


def _mg(util, venta):
    return round(util / venta * 100, 1) if venta else None


def fechas_datos():
    """Hasta que dia hay ventas cargadas y cuando se cargo el inventario."""
    r = db.query_one("""SELECT (SELECT max(fecha_conta) FROM ventas) AS ventas_hasta,
                               (SELECT max(cargado_en) FROM inventario_control) AS inventario_cargado""")
    return {"ventas_hasta": r["ventas_hasta"].isoformat() if r["ventas_hasta"] else None,
            "inventario_cargado": r["inventario_cargado"].date().isoformat() if r["inventario_cargado"] else None}


def _filtros_venta(sucursal=None, vendedor=None, marca=None, familia=None, canal=None, cliente=None, codigo=None):
    w, p = [], {}
    if sucursal:
        w.append("sucursal_logica = ANY(%(f_suc)s)")
        p["f_suc"] = [s.upper() for s in (sucursal if isinstance(sucursal, list) else [sucursal])]
    if vendedor:
        w.append("vendedor_rpt ILIKE %(f_vend)s")
        p["f_vend"] = f"%{vendedor}%"
    if marca:
        w.append("marca = ANY(%(f_marca)s)")
        p["f_marca"] = [m.upper() for m in (marca if isinstance(marca, list) else [marca])]
    if familia:
        w.append("familia ILIKE %(f_fam)s")
        p["f_fam"] = f"%{familia}%"
    if canal:
        w.append("tipo_venta ILIKE %(f_canal)s")
        p["f_canal"] = f"%{canal}%"
    if cliente:
        w.append("nombre_cliente ILIKE %(f_cli)s")
        p["f_cli"] = f"%{cliente}%"
    if codigo:
        w.append("codigo_cm = ANY(%(f_cod)s)")
        p["f_cod"] = [int(c) for c in (codigo if isinstance(codigo, list) else [codigo])]
    return (" AND " + " AND ".join(w)) if w else "", p


# ══════════════════════════════════════════════════════
#  VENTAS
# ══════════════════════════════════════════════════════
def ventas_resumen(desde=None, hasta=None, sucursal=None, vendedor=None, marca=None, familia=None,
                   canal=None, cliente=None, codigo=None):
    hoy = date.fromisoformat(fechas_datos()["ventas_hasta"])
    d, h = _fecha(desde, date(hoy.year, 1, 1)), _fecha(hasta, hoy)
    d0, h0 = _menos_un_ano(d), _menos_un_ano(h)
    fw, p = _filtros_venta(sucursal, vendedor, marca, familia, canal, cliente, codigo)
    p.update({"d": d, "h": h, "d0": d0, "h0": h0})
    r = db.query_one(f"""
        SELECT coalesce(sum(total) FILTER (WHERE fecha_conta BETWEEN %(d)s AND %(h)s), 0) AS venta,
               coalesce(sum(utilidad_bruta) FILTER (WHERE fecha_conta BETWEEN %(d)s AND %(h)s), 0) AS util,
               coalesce(sum(cantidad) FILTER (WHERE fecha_conta BETWEEN %(d)s AND %(h)s), 0) AS unidades,
               count(DISTINCT doc_sap) FILTER (WHERE fecha_conta BETWEEN %(d)s AND %(h)s AND total > 0) AS documentos,
               count(DISTINCT codigo_cliente) FILTER (WHERE fecha_conta BETWEEN %(d)s AND %(h)s AND total > 0) AS clientes,
               coalesce(sum(total) FILTER (WHERE fecha_conta BETWEEN %(d0)s AND %(h0)s), 0) AS venta_ant,
               coalesce(sum(utilidad_bruta) FILTER (WHERE fecha_conta BETWEEN %(d0)s AND %(h0)s), 0) AS util_ant
        FROM v_ventas
        WHERE (fecha_conta BETWEEN %(d)s AND %(h)s OR fecha_conta BETWEEN %(d0)s AND %(h0)s) {fw}""", p)
    venta, util, venta0, util0 = (float(r[k]) for k in ("venta", "util", "venta_ant", "util_ant"))
    mg_ok = d0.year >= ANO_MARGEN_CONFIABLE
    return {
        "periodo": f"{d.isoformat()} a {h.isoformat()}",
        "venta": round(venta), "margen": round(util), "margen_pct": _mg(util, venta),
        "unidades": round(float(r["unidades"])), "documentos": r["documentos"], "clientes": r["clientes"],
        "ticket_promedio": round(venta / r["documentos"]) if r["documentos"] else None,
        "mismo_periodo_ano_anterior": {"periodo": f"{d0.isoformat()} a {h0.isoformat()}", "venta": round(venta0),
                                       **({"margen": round(util0), "margen_pct": _mg(util0, venta0)} if mg_ok else
                                          {"margen": "no comparable (antes de 2026 el margen no está ajustado al ERP)"})},
        "variacion_venta_pct": _pct(venta, venta0),
        "variacion_margen_puntos": (round(_mg(util, venta) - _mg(util0, venta0), 1)
                                    if mg_ok and venta and venta0 else None),
    }


def ventas_por(dimension, desde=None, hasta=None, sucursal=None, vendedor=None, marca=None, familia=None,
               canal=None, cliente=None, codigo=None, orden="venta", top=15):
    if dimension not in DIMENSIONES:
        raise ValueError(f"Dimensión no válida. Usa una de: {', '.join(DIMENSIONES)}")
    col = DIMENSIONES[dimension]
    hoy = date.fromisoformat(fechas_datos()["ventas_hasta"])
    d, h = _fecha(desde, date(hoy.year, 1, 1)), _fecha(hasta, hoy)
    d0, h0 = _menos_un_ano(d), _menos_un_ano(h)
    fw, p = _filtros_venta(sucursal, vendedor, marca, familia, canal, cliente, codigo)
    p.update({"d": d, "h": h, "d0": d0, "h0": h0})
    filas = db.query_all(f"""
        SELECT {col} AS valor,
               {"max(descripcion)" if dimension == "producto" else "NULL"} AS descripcion,
               coalesce(sum(total) FILTER (WHERE fecha_conta BETWEEN %(d)s AND %(h)s), 0) AS venta,
               coalesce(sum(utilidad_bruta) FILTER (WHERE fecha_conta BETWEEN %(d)s AND %(h)s), 0) AS util,
               coalesce(sum(cantidad) FILTER (WHERE fecha_conta BETWEEN %(d)s AND %(h)s), 0) AS unidades,
               coalesce(sum(total) FILTER (WHERE fecha_conta BETWEEN %(d0)s AND %(h0)s), 0) AS venta_ant,
               coalesce(sum(utilidad_bruta) FILTER (WHERE fecha_conta BETWEEN %(d0)s AND %(h0)s), 0) AS util_ant
        FROM v_ventas
        WHERE (fecha_conta BETWEEN %(d)s AND %(h)s OR fecha_conta BETWEEN %(d0)s AND %(h0)s) {fw}
        GROUP BY 1""", p)
    items, tv, tu = [], 0.0, 0.0
    for f in filas:
        v, u, v0, u0 = (float(f[k]) for k in ("venta", "util", "venta_ant", "util_ant"))
        tv, tu = tv + v, tu + u
        nombre = str(f["valor"]) if f["valor"] is not None else "(sin dato)"
        if dimension == "mes":
            nombre = f"{d.year}-{int(f['valor']):02d}" if d.year == h.year else f"mes {int(f['valor']):02d}"
            if v == 0 and v0 == 0:
                continue
        if dimension == "sucursal":
            nombre = SUCURSALES_VENTA.get(nombre, nombre)
        items.append({"nombre": nombre + (f" — {f['descripcion']}" if f["descripcion"] else ""),
                      "venta": round(v), "margen": round(u), "margen_pct": _mg(u, v),
                      "unidades": round(float(f["unidades"])), "venta_ano_anterior": round(v0),
                      "variacion_pct": _pct(v, v0), "dif_venta": round(v - v0),
                      "margen_pct_ano_anterior": _mg(u0, v0) if d0.year >= ANO_MARGEN_CONFIABLE else "no comparable"})
    claves = {"venta": lambda x: -x["venta"], "margen": lambda x: -x["margen"],
              "margen_pct": lambda x: -(x["margen_pct"] or -999), "crecimiento": lambda x: -x["dif_venta"],
              "caida": lambda x: x["dif_venta"]}
    if dimension == "mes":
        items.sort(key=lambda x: x["nombre"])
    else:
        items.sort(key=claves.get(orden, claves["venta"]))
    top = max(1, min(int(top or 15), MAX_FILAS)) if dimension != "mes" else 24
    return {"periodo": f"{d.isoformat()} a {h.isoformat()}",
            "comparado_con": f"{d0.isoformat()} a {h0.isoformat()}",
            "total": {"venta": round(tv), "margen": round(tu), "margen_pct": _mg(tu, tv)},
            "n_total": len(items), "items": items[:top]}


def clientes_inactivos(meses_sin_compra=3, meses_historia=12, sucursal=None, vendedor=None, top=20):
    hoy = date.fromisoformat(fechas_datos()["ventas_hasta"])
    corte = hoy - timedelta(days=int(30.4 * float(meses_sin_compra)))
    inicio = corte - timedelta(days=int(30.4 * float(meses_historia)))
    fw, p = _filtros_venta(sucursal, vendedor)
    p.update({"inicio": inicio, "corte": corte, "hoy": hoy})
    filas = db.query_all(f"""
        SELECT max(nombre_cliente) AS cliente, codigo_cliente, max(vendedor_rpt) AS vendedor,
               max(sucursal_logica) AS sucursal,
               sum(total) FILTER (WHERE fecha_conta >= %(inicio)s AND fecha_conta < %(corte)s) AS compra_antes,
               max(fecha_conta) AS ultima_compra
        FROM v_ventas
        WHERE fecha_conta >= %(inicio)s AND codigo_cliente NOT LIKE 'CB-%%' AND codigo_cliente <> 'C9999999-3' {fw}
        GROUP BY codigo_cliente
        HAVING coalesce(sum(total) FILTER (WHERE fecha_conta >= %(corte)s), 0) <= 0
           AND sum(total) FILTER (WHERE fecha_conta >= %(inicio)s AND fecha_conta < %(corte)s) > 0
        ORDER BY 5 DESC LIMIT %(top)s""", {**p, "top": max(1, min(int(top or 20), MAX_FILAS))})
    return {"criterio": f"Compraron entre {inicio} y {corte} y no han comprado desde {corte} (datos hasta {hoy}). "
                        "Excluye clientes boleta/web genéricos.",
            "clientes": [{"cliente": f["cliente"], "vendedor": f["vendedor"],
                          "sucursal": SUCURSALES_VENTA.get(f["sucursal"], f["sucursal"]),
                          "compra_en_el_periodo_previo": round(float(f["compra_antes"])),
                          "ultima_compra": f["ultima_compra"].isoformat()} for f in filas]}


def buscar_valores(campo, texto, top=10):
    """Encuentra el valor exacto de una marca, familia, vendedor, cliente o proveedor a partir de un texto."""
    texto = (texto or "").strip()
    if campo == "marca":
        filas = db.query_all("""
            SELECT p.marca AS valor, count(*) AS productos, max(p.descripcion) AS ejemplo
            FROM productos p
            WHERE p.marca ILIKE %(t)s OR p.descripcion ILIKE %(t2)s
            GROUP BY p.marca ORDER BY count(*) DESC LIMIT %(top)s""",
                             {"t": f"%{texto}%", "t2": f"%{texto}%", "top": top})
        return {"campo": campo, "coincidencias": [dict(f) for f in filas],
                "nota": "Usa el código de marca (columna valor) en las otras herramientas."}
    if campo == "proveedor":
        filas = db.query_all("""SELECT nombre_proveedor AS valor, sum(precio_total) AS comprado
                                FROM compras WHERE nombre_proveedor ILIKE %(t)s
                                GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT %(top)s""", {"t": f"%{texto}%", "top": top})
        return {"campo": campo, "coincidencias": [{"valor": f["valor"], "comprado": round(float(f["comprado"] or 0))} for f in filas]}
    col = {"familia": "familia", "subfamilia": "subfamilia", "vendedor": "vendedor_rpt", "cliente": "nombre_cliente",
           "canal": "tipo_venta"}.get(campo)
    if not col:
        raise ValueError("Campo no válido: marca, familia, subfamilia, vendedor, cliente, canal o proveedor.")
    filas = db.query_all(f"""SELECT {col} AS valor, sum(total) AS venta_12m FROM v_ventas
                             WHERE {col} ILIKE %(t)s AND fecha_conta >= current_date - 365
                             GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT %(top)s""", {"t": f"%{texto}%", "top": top})
    return {"campo": campo, "coincidencias": [{"valor": f["valor"], "venta_12m": round(float(f["venta_12m"] or 0))} for f in filas]}


# ══════════════════════════════════════════════════════
#  COMPRAS E INVENTARIO
# ══════════════════════════════════════════════════════
def _base_stock(where, params, limite=None):
    """Una fila por producto con stock por sucursal, transito, venta mensual,
    OC de stock pendientes, si es prioritario y su clase ABC/XYZ de la empresa."""
    filas = db.query_all(f"""
        WITH st AS (
            SELECT codigo,
                   sum(coalesce(stock,0)) FILTER (WHERE bodega = ANY(%(bod)s)) AS stock,
                   sum(coalesce(transito,0)) FILTER (WHERE bodega = ANY(%(bod)s)) AS transito,
                   sum(coalesce(venta_mensual,0)) FILTER (WHERE bodega = ANY(%(bod)s)) AS vm,
                   jsonb_object_agg(bodega, coalesce(stock,0)) FILTER (WHERE bodega = ANY(%(bod)s)) AS por_suc
            FROM inventario_stock GROUP BY codigo
        ), oc AS (
            SELECT codigo, sum(cantidad_pendiente) AS pend FROM compras
            WHERE cantidad_pendiente > 0 AND tipo_compra = 'STOCK' GROUP BY codigo
        ), cl AS (
            SELECT codigo, abc || xyz AS clase FROM clasificacion_producto WHERE alcance = 'EMPRESA'
        )
        SELECT p.codigo, p.referencia, p.descripcion, p.marca, p.familia, coalesce(p.cup,0) AS cup,
               coalesce(nullif(p.embalaje,0),1) AS embalaje,
               coalesce(st.stock,0) AS stock, coalesce(st.transito,0) AS transito, coalesce(st.vm,0) AS vm,
               st.por_suc, coalesce(oc.pend,0) AS oc_pendiente,
               (o.codigo_obligatorio IS NOT NULL) AS prioritario, cl.clase
        FROM productos p
        LEFT JOIN st ON st.codigo = p.codigo
        LEFT JOIN oc ON oc.codigo = p.codigo
        LEFT JOIN productos_obligatorios o ON o.codigo_obligatorio = p.codigo
        LEFT JOIN cl ON cl.codigo = p.codigo
        WHERE {where}
        {"LIMIT %(lim)s" if limite else ""}""", {**params, "bod": BODEGAS, "lim": limite})
    out = []
    for f in filas:
        stock, tr, vm, oc = (float(f[k]) for k in ("stock", "transito", "vm", "oc_pendiente"))
        disp = stock + tr + oc
        out.append({"codigo": f["codigo"], "referencia": f["referencia"], "descripcion": f["descripcion"],
                    "marca": f["marca"], "familia": f["familia"], "cup": float(f["cup"]), "embalaje": int(f["embalaje"]),
                    "stock": stock, "transito": tr, "oc_pendiente": oc, "venta_mensual": vm,
                    "cobertura_meses": round(disp / vm, 1) if vm > 0 else None,
                    "stock_por_sucursal": {k: float(v) for k, v in (f["por_suc"] or {}).items() if float(v)},
                    "prioritario": f["prioritario"], "clase": f["clase"],
                    "valor_stock": round(stock * float(f["cup"]))})
    return out


def stock_producto(texto=None, codigos=None, marca=None, top=15):
    """Stock, transito, OC pendientes, venta mensual y cobertura de productos puntuales."""
    w, p = [], {}
    if codigos:
        w.append("p.codigo = ANY(%(cods)s)")
        p["cods"] = [int(c) for c in codigos]
    if texto:
        w.append("(p.descripcion ILIKE %(t)s OR p.referencia ILIKE %(t)s OR p.codigo::text = %(tx)s)")
        p.update({"t": f"%{texto}%", "tx": str(texto).strip()})
    if marca:
        w.append("p.marca = %(marca)s")
        p["marca"] = marca.upper()
    if not w:
        raise ValueError("Indica un texto, códigos o una marca.")
    filas = _base_stock(" AND ".join(w), p)
    filas.sort(key=lambda x: -x["venta_mensual"] * x["cup"])
    return {"productos": filas[:max(1, min(int(top or 15), MAX_FILAS))], "encontrados": len(filas)}


def stock_historico(fecha, texto=None, codigos=None, marca=None, top=15):
    """Stock que habia en una fecha pasada, para cruzar la venta de ese momento
    con el inventario de entonces (ej. ver si un producto estaba quebrado).
    Usa la foto del inventario mas cercana dentro de los 7 dias previos (exacta,
    por sucursal); si no hay, estima el stock de la EMPRESA hacia atras
    (stock - recepciones + ventas), aproximado."""
    try:
        f = date.fromisoformat(str(fecha)[:10])
    except ValueError:
        raise ValueError("La fecha debe venir como AAAA-MM-DD.")
    w, p = [], {}
    if codigos:
        w.append("p.codigo = ANY(%(cods)s)")
        p["cods"] = [int(c) for c in codigos]
    if texto:
        w.append("(p.descripcion ILIKE %(t)s OR p.referencia ILIKE %(t)s OR p.codigo::text = %(tx)s)")
        p.update({"t": f"%{texto}%", "tx": str(texto).strip()})
    if marca:
        w.append("p.marca = %(marca)s")
        p["marca"] = marca.upper()
    if not w:
        raise ValueError("Indica un texto, códigos o una marca.")
    prods = db.query_all("SELECT p.codigo, p.descripcion, p.marca, coalesce(p.cup, 0) AS cup FROM productos p WHERE "
                         + " AND ".join(w) + " LIMIT 500", p)
    st = dif.stock_en_fecha_pg([r["codigo"] for r in prods], f)
    filas = []
    for r in prods:
        x = st.get(r["codigo"]) or {}
        filas.append({"codigo": r["codigo"], "descripcion": r["descripcion"], "marca": r["marca"],
                      "stock_empresa": round(x.get("empresa") or 0, 1), "por_sucursal": x.get("por_sucursal"),
                      "valor_cup": round((x.get("empresa") or 0) * float(r["cup"]))})
    filas.sort(key=lambda x: -x["valor_cup"])
    tipo = next(iter(st.values()), {}).get("tipo")
    fb = next(iter(st.values()), {}).get("fecha_base")
    return {"fecha_pedida": f.isoformat(), "tipo": tipo, "fecha_base": fb,
            "nota": ("Foto real del inventario del %s, por sucursal." % fb) if tipo == "foto" else
                    ("Estimado de la EMPRESA hacia atrás desde el inventario del %s (stock - recepciones + ventas). "
                     "Aproximado: no incluye ajustes, mermas ni tomas de inventario; sirve para saber si había o no "
                     "stock, no para cifras exactas. No hay detalle por sucursal." % fb),
            "encontrados": len(filas), "productos": filas[:max(1, min(int(top or 15), MAX_FILAS))]}


def quiebres(marca=None, familia=None, solo_prioritarios=True, meses_umbral=1.0, top=20):
    """Productos que se venden y tienen menos de `meses_umbral` meses de cobertura
    (stock + transito + OC de stock pendientes)."""
    w, p = ["true"], {}
    if marca:
        w.append("p.marca = %(marca)s")
        p["marca"] = marca.upper()
    if familia:
        w.append("p.familia ILIKE %(fam)s")
        p["fam"] = f"%{familia}%"
    if solo_prioritarios:
        w.append("o.codigo_obligatorio IS NOT NULL")
    filas = [f for f in _base_stock(" AND ".join(w), p)
             if f["venta_mensual"] > 0 and (f["cobertura_meses"] or 0) < float(meses_umbral)]
    for f in filas:
        f["venta_mensual_valor"] = round(f["venta_mensual"] * f["cup"])
    filas.sort(key=lambda x: -x["venta_mensual_valor"])
    return {"criterio": f"Cobertura (stock + tránsito + OC pendientes) menor a {meses_umbral} meses"
                        + (", solo prioritarios" if solo_prioritarios else ""),
            "cantidad": len(filas), "venta_mensual_en_riesgo": sum(f["venta_mensual_valor"] for f in filas),
            "productos": filas[:max(1, min(int(top or 20), MAX_FILAS))]}


def sobrestock(marca=None, familia=None, meses_minimo=6.0, top=20):
    """Productos con mas de `meses_minimo` meses de cobertura o con stock y sin venta."""
    w, p = ["true"], {}
    if marca:
        w.append("p.marca = %(marca)s")
        p["marca"] = marca.upper()
    if familia:
        w.append("p.familia ILIKE %(fam)s")
        p["fam"] = f"%{familia}%"
    filas = []
    for f in _base_stock(" AND ".join(w), p):
        if f["stock"] <= 0:
            continue
        if f["venta_mensual"] <= 0:
            f["exceso_valor"] = f["valor_stock"]
            filas.append(f)
        elif f["cobertura_meses"] and f["cobertura_meses"] > float(meses_minimo):
            exceso = f["stock"] - float(meses_minimo) * f["venta_mensual"]
            f["exceso_valor"] = round(max(0.0, exceso) * f["cup"])
            filas.append(f)
    filas.sort(key=lambda x: -x["exceso_valor"])
    return {"criterio": f"Más de {meses_minimo} meses de cobertura, o con stock y sin venta mensual",
            "cantidad": len(filas), "valor_exceso_total": sum(f["exceso_valor"] for f in filas),
            "valor_stock_total": sum(f["valor_stock"] for f in filas),
            "productos": filas[:max(1, min(int(top or 20), MAX_FILAS))]}


def plan_compra(marca=None, familia=None, solo_prioritarios=True, meses_stock=1.5, meses_horizonte=1.0,
                factor_venta=1.0, top=25):
    """Compra sugerida para que al final del horizonte quede `meses_stock` de cobertura:
    venta del horizonte (x factor) + meses_stock x venta mensual - (stock + transito + OC),
    redondeado al embalaje. Valorizado al CUP."""
    if not marca and not familia and not solo_prioritarios:
        raise ValueError("Acota el plan por marca, familia o prioritarios.")
    w, p = ["true"], {}
    if marca:
        w.append("p.marca = %(marca)s")
        p["marca"] = marca.upper()
    if familia:
        w.append("p.familia ILIKE %(fam)s")
        p["fam"] = f"%{familia}%"
    if solo_prioritarios:
        w.append("o.codigo_obligatorio IS NOT NULL")
    excl = {r["codigo"] for r in db.query_all("SELECT codigo FROM productos_no_comprar")}
    lineas = []
    for f in _base_stock(" AND ".join(w), p):
        if f["venta_mensual"] <= 0 or f["codigo"] in excl or f["cup"] <= 0:
            continue
        nec = f["venta_mensual"] * (float(meses_horizonte) * float(factor_venta) + float(meses_stock)) \
            - (f["stock"] + f["transito"] + f["oc_pendiente"])
        if nec <= 0:
            continue
        un = math.ceil(nec / f["embalaje"] - 1e-9) * f["embalaje"]
        lineas.append({"codigo": f["codigo"], "referencia": f["referencia"], "descripcion": f["descripcion"],
                       "comprar_unidades": un, "monto": round(un * f["cup"]), "cobertura_actual": f["cobertura_meses"],
                       "venta_mensual": f["venta_mensual"], "embalaje": f["embalaje"]})
    lineas.sort(key=lambda x: -x["monto"])
    return {"criterio": (f"Que al final de {meses_horizonte} mes(es) quede {meses_stock} meses de stock; venta x {factor_venta}; "
                         "descuenta stock, tránsito y OC de stock pendientes; redondeado al embalaje; valorizado al CUP; "
                         "excluye 'No comprar'" + ("; solo prioritarios" if solo_prioritarios else "")),
            "productos_a_comprar": len(lineas), "monto_total": sum(l["monto"] for l in lineas),
            "unidades_total": sum(l["comprar_unidades"] for l in lineas),
            "lineas": lineas[:max(1, min(int(top or 25), MAX_FILAS))]}


def compras_ordenes(desde=None, hasta=None, proveedor=None, marca=None, agrupar="proveedor", solo_pendientes=False, top=15):
    """Ordenes de compra: comprado y pendiente de recibir, por proveedor, marca o mes."""
    hoy = date.today()
    d, h = _fecha(desde, date(hoy.year, 1, 1)), _fecha(hasta, hoy)
    col = {"proveedor": "nombre_proveedor", "marca": "marca", "mes": "to_char(fecha_creacion, 'YYYY-MM')",
           "tipo": "tipo_compra"}.get(agrupar, "nombre_proveedor")
    w, p = ["fecha_creacion BETWEEN %(d)s AND %(h)s"], {"d": d, "h": h}
    if proveedor:
        w.append("nombre_proveedor ILIKE %(prov)s")
        p["prov"] = f"%{proveedor}%"
    if marca:
        w.append("marca = %(marca)s")
        p["marca"] = marca.upper()
    if solo_pendientes:
        w.append("cantidad_pendiente > 0")
    filas = db.query_all(f"""
        SELECT {col} AS valor, count(DISTINCT n_orden_compra) AS ordenes,
               coalesce(sum(precio_total), 0) AS comprado, coalesce(sum(total_pendiente), 0) AS pendiente
        FROM compras WHERE {' AND '.join(w)} GROUP BY 1 ORDER BY 3 DESC""", p)
    items = [{"nombre": f["valor"], "ordenes": f["ordenes"], "comprado": round(float(f["comprado"])),
              "pendiente_de_recibir": round(float(f["pendiente"]))} for f in filas]
    if agrupar == "mes":
        items.sort(key=lambda x: x["nombre"] or "")
    return {"periodo": f"{d} a {h}", "total_comprado": sum(i["comprado"] for i in items),
            "total_pendiente": sum(i["pendiente_de_recibir"] for i in items),
            "items": items[:max(1, min(int(top or 15), MAX_FILAS)) if agrupar != "mes" else 24]}


# ══════════════════════════════════════════════════════
#  CONSULTA LIBRE (migracion 021): SQL de solo lectura sobre el esquema ia
# ══════════════════════════════════════════════════════
MAX_FILAS_SQL = 50
_PROHIBIDAS = re.compile(r"\b(insert|update|delete|merge|drop|alter|create|grant|revoke|truncate|copy|call|"
                         r"do|set|reset|vacuum|analyze|listen|notify|pg_sleep|lo_import|lo_export|dblink)\b", re.I)


def _a_json(v):
    if isinstance(v, Decimal):
        f = float(v)
        return int(f) if f.is_integer() else round(f, 4)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def consulta_sql(sql):
    """Ejecuta UNA consulta SELECT sobre las vistas del esquema ia, con el usuario
    agente_lectura (solo lectura, 15 s maximo). Devuelve hasta 50 filas."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not re.match(r"(?is)^(select|with)\b", sql):
        raise ValueError("Solo se permiten consultas SELECT (o WITH ... SELECT).")
    if ";" in sql:
        raise ValueError("Una sola consulta por vez (sin ';').")
    if _PROHIBIDAS.search(re.sub(r"'[^']*'", "''", sql)):
        raise ValueError("La consulta tiene una palabra no permitida: solo lectura.")
    if len(sql) > 6000:
        raise ValueError("Consulta demasiado larga.")
    url = os.environ.get("DATABASE_URL_LECTURA")
    if not url:
        raise ValueError("La consulta libre no está configurada (falta DATABASE_URL_LECTURA).")
    try:
        with psycopg.connect(url, prepare_threshold=None, autocommit=False, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(sql)
                cols = [d.name for d in cur.description] if cur.description else []
                filas = cur.fetchmany(MAX_FILAS_SQL + 1)
            conn.rollback()
    except psycopg.errors.QueryCanceled:
        raise ValueError("La consulta tardó más de 15 segundos. Acota el período o agrega filtros.")
    except psycopg.Error as e:
        raise ValueError(f"Error de SQL: {str(e).splitlines()[0]}")
    mas = len(filas) > MAX_FILAS_SQL
    return {"columnas": cols, "filas": [[_a_json(v) for v in f] for f in filas[:MAX_FILAS_SQL]],
            "n_filas": min(len(filas), MAX_FILAS_SQL),
            "aviso": "Hay más de 50 filas: agrupa o filtra más si necesitas el total." if mas else None}


# ══════════════════════════════════════════════════════
#  LOGISTICA: la planilla de despachos y retiros (en vivo)
# ══════════════════════════════════════════════════════
def logistica(desde=None, hasta=None, sucursal=None, chofer=None, tipo=None):
    """Cumplimiento de despachos y retiros desde la planilla de logistica (Google Sheets, en vivo)."""
    import data_loader_logistica as L
    hoy = date.today()
    d = L.get_panel_logistica(desde=desde or date(hoy.year, 1, 1).isoformat(), hasta=hasta or hoy.isoformat(),
                              sucursal=[sucursal] if isinstance(sucursal, str) else sucursal,
                              chofer=[chofer] if isinstance(chofer, str) else chofer,
                              tipo=[tipo] if isinstance(tipo, str) else tipo)
    semanal = [s for s in d.get("semanal", [])
               if (not desde or s["hasta"] >= desde) and (not hasta or s["desde"] <= hasta)][-12:]
    return {
        "leido_en": d.get("leido_en"), "periodo": f"{desde or date(hoy.year, 1, 1)} a {hasta or hoy}",
        "kpis": d.get("kpis"), "mensual": d.get("mensual"), "ultimas_semanas": semanal,
        "capacidad_por_movimientos_del_dia": d.get("capacidad"), "capacidad_por_chofer_dia": d.get("por_chofer_dia"),
        "choferes": d.get("choferes"), "sucursales_origen": d.get("sucursales"), "dias_semana": d.get("dias"),
        "motivos_incumplimiento": d.get("motivos"), "comentarios_frecuentes": (d.get("comentarios") or [])[:6],
        "plazo_de_entrega": d.get("plazo"), "comunas_top": (d.get("comunas") or [])[:8],
        "vencidos_sin_cerrar": len(d.get("vencidos") or []), "calidad_planilla": d.get("calidad"),
    }


# ══════════════════════════════════════════════════════
#  Catalogo para la IA (nombre -> funcion + esquema)
# ══════════════════════════════════════════════════════
_SUC = {"type": "array", "items": {"type": "string", "enum": list(SUCURSALES_VENTA)},
        "description": "Códigos de sucursal lógica. San Isidro = ['SE','CMD']."}
_FECHA = {"type": "string", "description": "Fecha AAAA-MM-DD"}
_FILTROS_VENTA = {
    "desde": _FECHA, "hasta": _FECHA, "sucursal": _SUC,
    "vendedor": {"type": "string", "description": "Nombre o parte del nombre del vendedor"},
    "marca": {"type": "array", "items": {"type": "string"}, "description": "Códigos de marca (ej. BTIC, LEGR). Usa buscar_valores si no conoces el código."},
    "familia": {"type": "string"}, "canal": {"type": "string", "description": "Canal de venta (ej. NORMAL, MERCADO LIBRE)"},
    "cliente": {"type": "string", "description": "Nombre o parte del nombre del cliente"},
    "codigo": {"type": "array", "items": {"type": "integer"}, "description": "Códigos de producto Casa Musa"},
}

HERRAMIENTAS = {
    "ventas_resumen": {
        "fn": ventas_resumen,
        "descripcion": "Venta neta, margen, unidades, documentos y clientes de un período, comparado con el mismo período del año anterior. Acepta filtros por sucursal, vendedor, marca, familia, canal, cliente o producto.",
        "parametros": {"type": "object", "properties": dict(_FILTROS_VENTA)},
    },
    "ventas_por": {
        "fn": ventas_por,
        "descripcion": "Ranking de ventas y margen por una dimensión (sucursal, vendedor, cliente, marca, familia, subfamilia, canal, procedencia, producto o mes), con la variación contra el mismo período del año anterior. 'orden' permite ver los que más crecen ('crecimiento') o más caen ('caida').",
        "parametros": {"type": "object", "properties": {
            "dimension": {"type": "string", "enum": list(DIMENSIONES)}, **_FILTROS_VENTA,
            "orden": {"type": "string", "enum": ["venta", "margen", "margen_pct", "crecimiento", "caida"]},
            "top": {"type": "integer", "description": f"Máximo {MAX_FILAS}"}}, "required": ["dimension"]},
    },
    "clientes_inactivos": {
        "fn": clientes_inactivos,
        "descripcion": "Clientes con nombre que compraban y dejaron de comprar: compraron en los 'meses_historia' anteriores y nada en los últimos 'meses_sin_compra'. Ordenados por lo que compraban.",
        "parametros": {"type": "object", "properties": {
            "meses_sin_compra": {"type": "number"}, "meses_historia": {"type": "number"}, "sucursal": _SUC,
            "vendedor": {"type": "string"}, "top": {"type": "integer"}}},
    },
    "buscar_valores": {
        "fn": buscar_valores,
        "descripcion": "Busca el valor exacto (código de marca, nombre de familia, vendedor, cliente, canal o proveedor) a partir de un texto aproximado. Úsala antes de filtrar si no estás seguro del valor.",
        "parametros": {"type": "object", "properties": {
            "campo": {"type": "string", "enum": ["marca", "familia", "subfamilia", "vendedor", "cliente", "canal", "proveedor"]},
            "texto": {"type": "string"}}, "required": ["campo", "texto"]},
    },
    "stock_producto": {
        "fn": stock_producto,
        "descripcion": "Stock por sucursal, tránsito, OC de stock pendientes, venta mensual, cobertura en meses, si es prioritario y su clase ABC/XYZ, para productos buscados por texto, códigos o marca.",
        "parametros": {"type": "object", "properties": {
            "texto": {"type": "string", "description": "Descripción, referencia o código"},
            "codigos": {"type": "array", "items": {"type": "integer"}}, "marca": {"type": "string"},
            "top": {"type": "integer"}}},
    },
    "stock_historico": {
        "fn": stock_historico,
        "descripcion": "Stock que había en una FECHA PASADA (para cruzar con la venta de ese momento, ej. si un producto estaba quebrado cuando bajó la venta). Usa la foto real del inventario si hay una en los 7 días previos (por sucursal; se guardan cada lunes y fin de mes desde el 23-10-2026); si no, estima el stock de la empresa hacia atrás (aproximado). Productos por texto, códigos o marca.",
        "parametros": {"type": "object", "properties": {
            "fecha": {"type": "string", "description": "AAAA-MM-DD"},
            "texto": {"type": "string", "description": "Descripción, referencia o código"},
            "codigos": {"type": "array", "items": {"type": "integer"}}, "marca": {"type": "string"},
            "top": {"type": "integer"}}, "required": ["fecha"]},
    },
    "quiebres": {
        "fn": quiebres,
        "descripcion": "Productos con venta y poca cobertura (stock + tránsito + OC pendientes menor a 'meses_umbral'). Por defecto solo prioritarios. Ordenados por venta mensual valorizada.",
        "parametros": {"type": "object", "properties": {
            "marca": {"type": "string"}, "familia": {"type": "string"}, "solo_prioritarios": {"type": "boolean"},
            "meses_umbral": {"type": "number"}, "top": {"type": "integer"}}},
    },
    "sobrestock": {
        "fn": sobrestock,
        "descripcion": "Productos con exceso de stock: más de 'meses_minimo' meses de cobertura o stock sin venta. Entrega el valor del exceso a costo (CUP).",
        "parametros": {"type": "object", "properties": {
            "marca": {"type": "string"}, "familia": {"type": "string"}, "meses_minimo": {"type": "number"},
            "top": {"type": "integer"}}},
    },
    "plan_compra": {
        "fn": plan_compra,
        "descripcion": "Compra sugerida para que, al final de 'meses_horizonte', cada producto quede con 'meses_stock' de cobertura. 'factor_venta' sube o baja la venta esperada (ej. 1.4 por temporada). Por defecto solo prioritarios.",
        "parametros": {"type": "object", "properties": {
            "marca": {"type": "string"}, "familia": {"type": "string"}, "solo_prioritarios": {"type": "boolean"},
            "meses_stock": {"type": "number"}, "meses_horizonte": {"type": "number"}, "factor_venta": {"type": "number"},
            "top": {"type": "integer"}}},
    },
    "consulta_sql": {
        "fn": consulta_sql,
        "descripcion": ("Consulta libre: ejecuta UNA consulta SQL SELECT (PostgreSQL) sobre las vistas del esquema ia "
                        "descritas en tus instrucciones. Úsala para cruzar información (cotizaciones con ventas o stock, "
                        "clientes con vendedores, metas, solicitudes, recepciones, clasificación, etc.) o cuando ninguna "
                        "otra herramienta responda. Agrupa y resume en SQL: devuelve máximo 50 filas. 15 s máximo."),
        "parametros": {"type": "object", "properties": {
            "sql": {"type": "string", "description": "Una sola consulta SELECT o WITH ... SELECT, sin punto y coma."}},
            "required": ["sql"]},
    },
    "logistica": {
        "fn": logistica,
        "descripcion": ("Despachos y retiros desde la planilla de logística (en vivo): cumplimiento, movimientos por día, "
                        "capacidad por chofer, choferes, sucursal de origen, motivos de incumplimiento, plazos de entrega, "
                        "comunas y vencidos sin cerrar. Filtros opcionales por sucursal (SI, MT, LC, CH, MR, MP), chofer y tipo "
                        "(Despacho o Retiro)."),
        "parametros": {"type": "object", "properties": {
            "desde": _FECHA, "hasta": _FECHA, "sucursal": {"type": "string"}, "chofer": {"type": "string"},
            "tipo": {"type": "string", "enum": ["Despacho", "Retiro"]}}},
    },
    "compras_ordenes": {
        "fn": compras_ordenes,
        "descripcion": "Órdenes de compra del período: monto comprado y pendiente de recibir, agrupado por proveedor, marca, mes o tipo de compra.",
        "parametros": {"type": "object", "properties": {
            "desde": _FECHA, "hasta": _FECHA, "proveedor": {"type": "string"}, "marca": {"type": "string"},
            "agrupar": {"type": "string", "enum": ["proveedor", "marca", "mes", "tipo"]},
            "solo_pendientes": {"type": "boolean"}, "top": {"type": "integer"}}},
    },
}
