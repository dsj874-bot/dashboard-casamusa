"""
Sugerido de Compra (Forecast) -- productos NACIONALES, segun su clase
ABC x XYZ en cada sucursal (ver data_loader_clasificacion_pg.py).

Pedido del usuario 2026-10-10: "quiero la propuesta, pero solo para
productos nacionales", con las metas estandar por clase (META_MESES).

Por sucursal:  meta = meses de la clase x venta mensual de la sucursal
               falta = meta - (stock + transito), redondeado a embalaje
               el exceso NO se mueve (se muestra como traspasable).
Empresa:       compra = suma de lo que falta en las sucursales - lo que
               San Isidro (bodega central) tiene sobre su propia meta -
               OC de STOCK ya por recibir, redondeado a embalaje, a CUP.
               El sobrante de las otras sucursales no se descuenta.

Fuera del sugerido: importados, codigos que empiezan con 6 (se compran a
pedido, indicado por el usuario), los marcados "no comprar"
(productos_no_comprar), CZ y sin venta en 12 meses. La clase es la del
ultimo calculo mensual; stock, transito y venta mensual se leen al dia.
"""
import math

import db
import data_loader_clasificacion_pg as dcl
import data_loader_exclusion_compra as dec

# Meses de venta de la sucursal por clase; "emb" = 1 embalaje; 0 = no comprar
META_MESES = {"AX": 1.5, "AY": 2.0, "AZ": 1.5, "BX": 1.5, "BY": 2.0, "BZ": 1.0,
              "CX": 1.0, "CY": "emb", "CZ": 0, "SV": 0}
META_NUEVO = 1.5
SUCURSALES = list(dcl.SUCURSALES)   # CH, MP, MT, MR, LC, SI


def _meta_unidades(clase, ciclo, vm, emb):
    """Meta de stock en unidades para una clase en una sucursal."""
    if clase in ("CZ", "SV"):
        return 0
    if ciclo == "Nuevo" and clase not in ("CZ", "SV"):
        return math.ceil(round(META_NUEVO * vm, 6)) if vm > 0 else emb
    regla = META_MESES.get(clase, 0)
    if regla == "emb":
        return emb
    if not regla:
        return 0
    return math.ceil(round(regla * vm, 6)) if vm > 0 else 0


def get_sugerido_compra_pg(proveedor=None, marca=None, familia=None, clase=None):
    clases_filtro = set(clase) if clase else None   # 'clase' se reusa abajo por sucursal
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, desde, hasta, calculado_en FROM clasificacion_calculo
                            WHERE completo ORDER BY id DESC LIMIT 1""")
            calc = cur.fetchone()
            if not calc:
                return {"calculo": None}
            cur.execute(
                """SELECT c.alcance, c.codigo, c.abc, c.xyz, c.ciclo, c.unidades, c.proveedor
                     FROM clasificacion_producto c
                     JOIN productos p ON p.codigo = c.codigo
                    WHERE c.calculo_id = %s AND p.procedencia = 'Nacional'
                      AND left(c.codigo::text, 1) <> '6'""",
                (calc["id"],),
            )
            clases = {}
            prov_emp = {}
            for r in cur.fetchall():
                if r["alcance"] == "EMPRESA":
                    prov_emp[r["codigo"]] = r["proveedor"]
                    clases.setdefault(r["codigo"], {})["EMPRESA"] = r
                else:
                    clases.setdefault(r["codigo"], {})[r["alcance"]] = r
            codigos = list(clases)
            cur.execute(
                """SELECT codigo, descripcion, marca, familia, coalesce(nullif(embalaje, 0), 1) AS emb, coalesce(cup, 0) AS cup
                     FROM productos WHERE codigo = ANY(%s)""",
                (codigos,),
            )
            prod = {r["codigo"]: r for r in cur.fetchall()}
            bodegas = {b: alc for alc, (_, b) in dcl.SUCURSALES.items()}
            cur.execute(
                """SELECT codigo, bodega, coalesce(stock, 0) AS st, coalesce(transito, 0) AS tr,
                          coalesce(venta_mensual, 0) AS vm
                     FROM inventario_stock WHERE codigo = ANY(%s) AND bodega = ANY(%s)""",
                (codigos, list(bodegas)),
            )
            inv = {}
            for r in cur.fetchall():
                inv[(bodegas[r["bodega"]], r["codigo"])] = (float(r["st"]), float(r["tr"]), float(r["vm"]))
            cur.execute(
                """SELECT codigo, sum(cantidad_pendiente) AS u,
                          sum(cantidad_pendiente * precio_total / nullif(cantidad_comprada, 0)) AS v
                     FROM compras WHERE cantidad_pendiente > 0 AND tipo_compra = 'STOCK' AND codigo = ANY(%s)
                    GROUP BY codigo""",
                (codigos,),
            )
            oc = {r["codigo"]: (float(r["u"] or 0), float(r["v"] or 0)) for r in cur.fetchall()}
    excluidos = dec.codigos_excluidos_compra()

    productos = []
    exceso_suc = {s: 0.0 for s in SUCURSALES}
    for cod, por_alc in clases.items():
        p = prod.get(cod)
        if not p:
            continue
        nombre_prov = prov_emp.get(cod)
        if proveedor and nombre_prov not in proveedor:
            continue
        if marca and p["marca"] not in marca:
            continue
        if familia and p["familia"] not in familia:
            continue
        emb, cup = int(p["emb"]), float(p["cup"])
        detalle, necesidad, exceso_si, necesidad_otros = {}, 0, 0.0, 0
        for s in SUCURSALES:
            r = por_alc.get(s)
            st, tr, vm = inv.get((s, cod), (0.0, 0.0, 0.0))
            if r is None:
                if st > 0:
                    detalle[s] = {"clase": None, "envio": 0, "stock": st}
                continue
            clase = (r["abc"] + r["xyz"]) if r["abc"] else "SV"
            if vm <= 0 and r["unidades"]:
                vm = max(float(r["unidades"]), 0.0) / 12.0
            meta = _meta_unidades(clase, r["ciclo"], vm, emb)
            disp = st + tr
            falta = max(0.0, meta - disp)
            cajas = math.ceil(round(falta / emb, 6)) if falta > 0 else 0
            envio = cajas * emb
            exceso = max(0.0, disp - meta) if meta > 0 or clase in ("CZ", "SV") else 0.0
            exceso_suc[s] += exceso * cup
            necesidad += envio
            if s == "SI":
                exceso_si = exceso
            else:
                necesidad_otros += envio
            detalle[s] = {"clase": clase, "envio": envio, "stock": st, "meta": meta}
        emp = por_alc.get("EMPRESA")
        clase_emp = ((emp["abc"] + emp["xyz"]) if emp["abc"] else "SV") if emp else None
        # Filtro por clasificacion: la clase de la empresa (la columna Clase)
        if clases_filtro and clase_emp not in clases_filtro:
            continue
        oc_u, oc_v = oc.get(cod, (0.0, 0.0))
        # Antes de comprar se despacha desde San Isidro lo que le sobra
        desde_si = min(exceso_si, necesidad_otros)
        bruto = max(0.0, necesidad - desde_si - oc_u)
        compra = math.ceil(round(bruto / emb, 6)) * emb if bruto > 0 else 0
        if cod in excluidos:
            compra = 0
        if necesidad <= 0 and compra <= 0:
            continue
        productos.append({
            "codigo": cod, "descripcion": p["descripcion"], "marca": p["marca"], "familia": p["familia"],
            "proveedor": nombre_prov, "clase": clase_emp, "ciclo": emp["ciclo"] if emp else None,
            "embalaje": emb, "cup": cup, "sucursales": detalle, "necesidad": necesidad,
            "desde_si": desde_si, "oc_en_camino": oc_u, "compra": compra, "cajas": compra // emb if emb else compra,
            "valor": round(compra * cup, 0), "excluido": cod in excluidos,
        })

    productos.sort(key=lambda x: -x["valor"])
    por_prov = {}
    for x in productos:
        k = x["proveedor"] or "Sin proveedor asignado"
        d = por_prov.setdefault(k, {"proveedor": k, "productos": 0, "valor": 0.0, "unidades": 0})
        if x["compra"] > 0:
            d["productos"] += 1
            d["valor"] += x["valor"]
            d["unidades"] += x["compra"]
    # Resumen por marca (pedido del usuario 2026-10-10: "solo por marca,
    # no por proveedor")
    por_marca = {}
    for x in productos:
        if x["compra"] <= 0:
            continue
        k = x["marca"] or "Sin marca"
        d = por_marca.setdefault(k, {"marca": k, "productos": 0, "valor": 0.0, "unidades": 0})
        d["productos"] += 1
        d["valor"] += x["valor"]
        d["unidades"] += x["compra"]
    por_clase = {}
    for x in productos:
        if x["compra"] > 0:
            k = x["clase"] or "-"
            por_clase[k] = por_clase.get(k, 0.0) + x["valor"]
    oc_desc = sum(min(x["oc_en_camino"], max(0.0, x["necesidad"] - x["desde_si"])) * x["cup"] for x in productos)
    desde_si_v = sum(x["desde_si"] * x["cup"] for x in productos)
    return {
        "calculo": {"desde": calc["desde"].isoformat(), "hasta": calc["hasta"].isoformat(),
                    "calculado_en": calc["calculado_en"].isoformat()},
        "metas": META_MESES, "meta_nuevo": META_NUEVO,
        "sucursales": [{"codigo": s, "nombre": dcl.NOMBRE_ALCANCE[s]} for s in SUCURSALES],
        "total": round(sum(x["valor"] for x in productos), 0),
        "n_productos": sum(1 for x in productos if x["compra"] > 0),
        "n_proveedores": sum(1 for d in por_prov.values() if d["productos"] > 0),
        "oc_descontada": round(oc_desc, 0),
        "desde_san_isidro": round(desde_si_v, 0),
        "exceso_traspasable": {s: round(v, 0) for s, v in exceso_suc.items()},
        "por_proveedor": sorted([d for d in por_prov.values() if d["productos"] > 0], key=lambda d: -d["valor"]),
        "por_marca": sorted(por_marca.values(), key=lambda d: -d["valor"]),
        "n_marcas": len(por_marca),
        "por_clase": por_clase,
        "productos": productos,
    }
