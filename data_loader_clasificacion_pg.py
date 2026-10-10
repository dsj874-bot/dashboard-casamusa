"""
Clasificacion de productos (Forecast) -- ABC x XYZ + ciclo de vida, para
la empresa y para cada sucursal, con los ultimos 12 meses cerrados.
Ver migrations/016_clasificacion_productos.sql para las definiciones.

calcular_clasificacion_pg() hace el calculo completo y lo guarda como un
clasificacion_calculo nuevo; get_clasificacion_pg() lo lee para la
pantalla. recalcular_si_corresponde_pg() lo llama el cron diario: solo
recalcula cuando cerro un mes nuevo.
"""
import math
from collections import defaultdict
from datetime import date, timedelta

import db
import data_loader_pg

# Alcance -> (codigos de sucursal_logica en ventas, bodega de inventario_stock)
SUCURSALES = {
    "CH": (["CH"], "Chicureo"),
    "MP": (["MP"], "Maipú"),
    "MT": (["MT"], "Matta"),
    "MR": (["MR"], "Manuel Rodríguez"),
    "LC": (["LC"], "Las Condes"),
    "SI": (["SE", "CMD"], "San Isidro"),
}
NOMBRE_ALCANCE = {"EMPRESA": "Empresa", "CH": "Chicureo", "MP": "Maipú", "MT": "Matta",
                  "MR": "Manuel Rodríguez", "LC": "Las Condes", "SI": "San Isidro"}
CORTE_A, CORTE_B = 0.80, 0.95
CV_X, CV_Y = 0.5, 1.0
MESES_NUEVO = 6
TAMANO_LOTE = 5000


def periodo_cerrado(fecha_datos):
    """Ultimos 12 meses cerrados respecto de la fecha de datos: si la
    fecha es el ultimo dia del mes, ese mes cuenta como cerrado."""
    manana = fecha_datos + timedelta(days=1)
    if manana.month != fecha_datos.month:
        hasta = fecha_datos
    else:
        hasta = date(fecha_datos.year, fecha_datos.month, 1) - timedelta(days=1)
    y, m = hasta.year, hasta.month - 11
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1), hasta


def _meses(desde, hasta):
    """Lista de claves ano*12+mes entre desde y hasta (inclusive)."""
    a, b = desde.year * 12 + desde.month - 1, hasta.year * 12 + hasta.month - 1
    return list(range(a, b + 1))


def _clasificar_alcance(ventas, primera, stock, desde, hasta):
    """ventas: {codigo: {am: (venta, margen, unidades)}}; primera: {codigo: date};
    stock: {codigo: (stock, valor, venta_mensual)}. Devuelve {codigo: dict}."""
    meses = _meses(desde, hasta)
    limite_nuevo = hasta - timedelta(days=int(MESES_NUEVO * 30.5))
    res = {}
    totales = {}
    for cod, por_mes in ventas.items():
        v = sum(x[0] for x in por_mes.values())
        g = sum(x[1] for x in por_mes.values())
        u = sum(x[2] for x in por_mes.values())
        totales[cod] = (v, g, u)

    # ABC por margen (solo margen positivo suma al acumulado)
    total_pos = sum(max(t[1], 0) for t in totales.values()) or 1.0
    acum = 0.0
    abc = {}
    for cod in sorted(totales, key=lambda c: -totales[c][1]):
        g = totales[cod][1]
        if g <= 0:
            abc[cod] = "C"
            continue
        antes = acum / total_pos
        acum += g
        abc[cod] = "A" if antes < CORTE_A else ("B" if antes < CORTE_B else "C")

    for cod in set(ventas) | {c for c, s in stock.items() if s[0] > 0}:
        por_mes = ventas.get(cod, {})
        v, g, u = totales.get(cod, (0.0, 0.0, 0.0))
        pv = primera.get(cod)
        st, valor, vm = stock.get(cod, (0.0, 0.0, 0.0))
        cobertura = round(st / vm, 2) if vm > 0 else None
        fila = {"venta": v, "margen": g, "unidades": u, "primera_venta": pv,
                "stock": st, "valor_inventario": valor, "venta_mensual": vm, "cobertura": cobertura,
                "meses_con_venta": sum(1 for am in meses if por_mes.get(am, (0, 0, 0))[2] > 0 or por_mes.get(am, (0, 0, 0))[0] > 0),
                "abc": None, "xyz": None, "cv": None}
        if not por_mes or v <= 0 and u <= 0:
            fila["ciclo"] = "Sin venta"
            res[cod] = fila
            continue
        fila["abc"] = abc.get(cod, "C")
        # XYZ: desde la primera venta si el producto es mas nuevo que el periodo
        desde_am = meses[0]
        if pv and pv > desde:
            desde_am = max(desde_am, pv.year * 12 + pv.month - 1)
        serie = [max(por_mes.get(am, (0, 0, 0))[2], 0.0) for am in meses if am >= desde_am]
        media = sum(serie) / len(serie) if serie else 0.0
        if media > 0 and len(serie) >= 2:
            cv = math.sqrt(sum((x - media) ** 2 for x in serie) / len(serie)) / media
        else:
            cv = None
        fila["cv"] = round(cv, 3) if cv is not None else None
        fila["xyz"] = "Z" if cv is None or cv > CV_Y else ("Y" if cv > CV_X else "X")
        # Ciclo de vida
        if pv and pv > limite_nuevo:
            fila["ciclo"] = "Nuevo"
        else:
            # En declive: se vendia con regularidad (6+ de los 9 meses
            # anteriores) y los ultimos 3 meses cayeron bajo la mitad. Sin
            # la exigencia de regularidad, cualquier producto esporadico
            # con 3 meses sin venta (lo normal en un Z) salia "en declive".
            ult3 = [max(por_mes.get(am, (0, 0, 0))[2], 0.0) for am in meses[-3:]]
            prev9 = [max(por_mes.get(am, (0, 0, 0))[2], 0.0) for am in meses[:-3]]
            p_prev = sum(prev9) / len(prev9) if prev9 else 0.0
            p_ult = sum(ult3) / 3.0
            regular = sum(1 for x in prev9 if x > 0) >= 6
            fila["ciclo"] = "En declive" if regular and p_prev > 0 and p_ult < 0.5 * p_prev else "Activo"
        res[cod] = fila
    return res


def calcular_clasificacion_pg(calculado_por="sistema"):
    """Calcula la clasificacion completa (empresa + 6 sucursales) y la
    guarda como un calculo nuevo. Devuelve un resumen."""
    fecha_datos = data_loader_pg.fecha_datos_real_pg()
    desde, hasta = periodo_cerrado(fecha_datos)
    suc_a_alcance = {s: alc for alc, (sucs, _) in SUCURSALES.items() for s in sucs}
    bodega_a_alcance = {b: alc for alc, (_, b) in SUCURSALES.items()}

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT sucursal_logica AS suc, codigo_cm AS cod, ano * 12 + mes - 1 AS am,
                          sum(total) AS v, sum(utilidad_bruta) AS g, sum(cantidad) AS u
                     FROM v_ventas
                    WHERE fecha_conta BETWEEN %(desde)s AND %(hasta)s AND codigo_cm IS NOT NULL
                    GROUP BY 1, 2, 3""",
                {"desde": desde, "hasta": hasta},
            )
            ventas = {"EMPRESA": defaultdict(dict)}
            for alc in SUCURSALES:
                ventas[alc] = defaultdict(dict)
            for r in cur.fetchall():
                t = (float(r["v"] or 0), float(r["g"] or 0), float(r["u"] or 0))
                emp = ventas["EMPRESA"][r["cod"]]
                prev = emp.get(r["am"], (0.0, 0.0, 0.0))
                emp[r["am"]] = (prev[0] + t[0], prev[1] + t[1], prev[2] + t[2])
                alc = suc_a_alcance.get(r["suc"])
                if alc:
                    d = ventas[alc][r["cod"]]
                    prev = d.get(r["am"], (0.0, 0.0, 0.0))
                    d[r["am"]] = (prev[0] + t[0], prev[1] + t[1], prev[2] + t[2])

            cur.execute(
                """SELECT sucursal_logica AS suc, codigo_cm AS cod, min(fecha_conta) AS f
                     FROM ventas WHERE codigo_cm IS NOT NULL AND total > 0 GROUP BY 1, 2"""
            )
            primera = {"EMPRESA": {}}
            for alc in SUCURSALES:
                primera[alc] = {}
            for r in cur.fetchall():
                emp = primera["EMPRESA"]
                if r["cod"] not in emp or r["f"] < emp[r["cod"]]:
                    emp[r["cod"]] = r["f"]
                alc = suc_a_alcance.get(r["suc"])
                if alc:
                    d = primera[alc]
                    if r["cod"] not in d or r["f"] < d[r["cod"]]:
                        d[r["cod"]] = r["f"]

            cur.execute(
                """SELECT s.codigo, s.bodega, coalesce(s.stock, 0) AS st, coalesce(s.venta_mensual, 0) AS vm,
                          coalesce(p.cup, 0) AS cup
                     FROM inventario_stock s JOIN productos p ON p.codigo = s.codigo
                    WHERE coalesce(s.stock, 0) > 0 OR s.bodega = 'Todas' OR coalesce(s.venta_mensual, 0) > 0"""
            )
            stock = {"EMPRESA": {}}
            for alc in SUCURSALES:
                stock[alc] = {}
            vm_todas = {}
            for r in cur.fetchall():
                st, vm, cup = float(r["st"]), float(r["vm"]), float(r["cup"])
                if r["bodega"] == "Todas":
                    vm_todas[r["codigo"]] = vm
                    continue
                if r["bodega"] != "Merma" and st > 0:
                    e = stock["EMPRESA"].get(r["codigo"], (0.0, 0.0, 0.0))
                    stock["EMPRESA"][r["codigo"]] = (e[0] + st, e[1] + st * cup, 0.0)
                alc = bodega_a_alcance.get(r["bodega"])
                if alc:
                    stock[alc][r["codigo"]] = (st, st * cup, vm)
            for cod, (st, valor, _) in list(stock["EMPRESA"].items()):
                stock["EMPRESA"][cod] = (st, valor, vm_todas.get(cod, 0.0))
            for cod, vm in vm_todas.items():
                if cod not in stock["EMPRESA"]:
                    stock["EMPRESA"][cod] = (0.0, 0.0, vm)

            cur.execute(
                """SELECT codigo,
                          bool_or(tipo_compra = 'STOCK')  AS stock,
                          bool_or(tipo_compra = 'PEDIDO') AS pedido
                     FROM compras WHERE fecha_creacion BETWEEN %(desde)s AND %(hasta)s GROUP BY codigo""",
                {"desde": desde, "hasta": hasta},
            )
            abast = {}
            for r in cur.fetchall():
                abast[r["codigo"]] = ("Stock y pedido" if r["stock"] and r["pedido"]
                                      else "Stock" if r["stock"] else "Pedido")
            cur.execute(
                """SELECT DISTINCT ON (codigo) codigo, nombre_proveedor FROM (
                       SELECT codigo, nombre_proveedor, count(*) AS n FROM compras
                        WHERE nombre_proveedor IS NOT NULL AND ano >= (SELECT max(ano) FROM compras) - 1
                        GROUP BY 1, 2) t
                    ORDER BY codigo, n DESC, nombre_proveedor"""
            )
            proveedor = {r["codigo"]: r["nombre_proveedor"] for r in cur.fetchall()}

            cur.execute(
                "INSERT INTO clasificacion_calculo (desde, hasta, calculado_por) VALUES (%s, %s, %s) RETURNING id",
                (desde, hasta, calculado_por),
            )
            calculo_id = cur.fetchone()["id"]
        conn.commit()

        filas = []
        resumen = {}
        for alc in ["EMPRESA"] + list(SUCURSALES):
            res = _clasificar_alcance(ventas[alc], primera[alc], stock[alc], desde, hasta)
            resumen[alc] = len(res)
            for cod, f in res.items():
                filas.append((
                    calculo_id, alc, cod, f["venta"], f["margen"], f["unidades"], f["meses_con_venta"],
                    f["cv"], f["abc"], f["xyz"], f["ciclo"], f["primera_venta"], f["stock"],
                    f["valor_inventario"], f["venta_mensual"], f["cobertura"],
                    abast.get(cod, "Sin compras"), proveedor.get(cod),
                ))

        # COPY por lotes, un commit por lote (nunca una transaccion gigante).
        # executemany fila a fila tardaba ~10 min por la latencia a la base;
        # COPY manda cada lote de una vez. El calculo recien se marca
        # completo al final.
        with conn.cursor() as cur:
            for i in range(0, len(filas), TAMANO_LOTE):
                with cur.copy(
                    """COPY clasificacion_producto
                         (calculo_id, alcance, codigo, venta, margen, unidades, meses_con_venta, cv, abc, xyz,
                          ciclo, primera_venta, stock, valor_inventario, venta_mensual, cobertura,
                          abastecimiento, proveedor) FROM STDIN"""
                ) as cp:
                    for fila in filas[i:i + TAMANO_LOTE]:
                        cp.write_row(fila)
                conn.commit()
            cur.execute("UPDATE clasificacion_calculo SET completo = true WHERE id = %s", (calculo_id,))
            # Se conserva solo el calculo nuevo
            cur.execute("DELETE FROM clasificacion_calculo WHERE id <> %s", (calculo_id,))
        conn.commit()

    return {"calculo_id": calculo_id, "desde": desde.isoformat(), "hasta": hasta.isoformat(),
            "filas": len(filas), "por_alcance": resumen}


def recalcular_si_corresponde_pg():
    """Para el cron diario: recalcula solo si cerro un mes nuevo desde el
    ultimo calculo completo (o si nunca se ha calculado)."""
    _, hasta = periodo_cerrado(data_loader_pg.fecha_datos_real_pg())
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT max(hasta) AS h FROM clasificacion_calculo WHERE completo")
            ultimo = cur.fetchone()["h"]
    if ultimo == hasta:
        return {"recalculado": False, "hasta": hasta.isoformat()}
    return {"recalculado": True, **calcular_clasificacion_pg(calculado_por="cron")}


def _ultimo_calculo(cur):
    cur.execute("""SELECT id, desde, hasta, calculado_en, calculado_por FROM clasificacion_calculo
                    WHERE completo ORDER BY id DESC LIMIT 1""")
    return cur.fetchone()


def get_clasificacion_pg(alcance="EMPRESA", marca=None, familia=None, proveedor=None,
                         celda=None, limite=500):
    """Matriz ABC x XYZ (+ Sin venta) con codigos, margen, venta e inventario
    por celda, y el listado de productos de la celda elegida (o de todas)."""
    if alcance not in NOMBRE_ALCANCE:
        alcance = "EMPRESA"
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            calc = _ultimo_calculo(cur)
            if not calc:
                return {"calculo": None}
            frag, params = "", {"cid": calc["id"], "alc": alcance}
            if marca:
                frag += " AND p.marca = ANY(%(marca)s)"
                params["marca"] = marca
            if familia:
                frag += " AND p.familia = ANY(%(familia)s)"
                params["familia"] = familia
            if proveedor:
                frag += " AND c.proveedor = ANY(%(proveedor)s)"
                params["proveedor"] = proveedor
            base = f"""FROM clasificacion_producto c LEFT JOIN productos p ON p.codigo = c.codigo
                       WHERE c.calculo_id = %(cid)s AND c.alcance = %(alc)s {frag}"""
            cur.execute(
                f"""SELECT coalesce(c.abc, '-') AS abc, coalesce(c.xyz, '-') AS xyz,
                           count(*) AS codigos, sum(c.venta) AS venta, sum(c.margen) AS margen,
                           sum(c.valor_inventario) AS inventario,
                           count(*) FILTER (WHERE c.ciclo = 'Nuevo') AS nuevos,
                           count(*) FILTER (WHERE c.ciclo = 'En declive') AS declive
                    {base} GROUP BY 1, 2""",
                params,
            )
            celdas = [{k: (float(v) if isinstance(v, (int, float)) or hasattr(v, "as_integer_ratio") else v)
                       for k, v in r.items()} for r in cur.fetchall()]
            cur.execute(f"SELECT DISTINCT p.marca FROM clasificacion_producto c LEFT JOIN productos p ON p.codigo = c.codigo "
                        f"WHERE c.calculo_id = %(cid)s AND c.alcance = %(alc)s AND p.marca IS NOT NULL ORDER BY 1",
                        {"cid": calc["id"], "alc": alcance})
            marcas = [r["marca"] for r in cur.fetchall()]
            cur.execute(f"SELECT DISTINCT p.familia FROM clasificacion_producto c LEFT JOIN productos p ON p.codigo = c.codigo "
                        f"WHERE c.calculo_id = %(cid)s AND c.alcance = %(alc)s AND p.familia IS NOT NULL ORDER BY 1",
                        {"cid": calc["id"], "alc": alcance})
            familias = [r["familia"] for r in cur.fetchall()]
            cur.execute(f"SELECT DISTINCT proveedor FROM clasificacion_producto "
                        f"WHERE calculo_id = %(cid)s AND alcance = %(alc)s AND proveedor IS NOT NULL ORDER BY 1",
                        {"cid": calc["id"], "alc": alcance})
            proveedores = [r["proveedor"] for r in cur.fetchall()]

            frag_celda = ""
            if celda == "SV":
                frag_celda = " AND c.abc IS NULL"
            elif celda and len(celda) == 2 and celda[0] in "ABC" and celda[1] in "XYZ":
                frag_celda = " AND c.abc = %(c_abc)s AND c.xyz = %(c_xyz)s"
                params.update({"c_abc": celda[0], "c_xyz": celda[1]})
            elif celda and len(celda) == 1 and celda in "ABC":
                frag_celda = " AND c.abc = %(c_abc)s"
                params["c_abc"] = celda
            cur.execute(f"SELECT count(*) AS n {base} {frag_celda}", params)
            n_lista = cur.fetchone()["n"]
            params["lim"] = limite
            cur.execute(
                f"""SELECT c.codigo, p.descripcion, p.marca, p.familia, c.proveedor, c.abc, c.xyz, c.ciclo,
                           c.venta, c.margen, c.unidades, c.meses_con_venta, c.cv, c.stock, c.valor_inventario,
                           c.venta_mensual, c.cobertura, c.abastecimiento, c.primera_venta
                    {base} {frag_celda}
                    ORDER BY c.valor_inventario DESC, c.margen DESC LIMIT %(lim)s""",
                params,
            )
            productos = []
            for r in cur.fetchall():
                d = dict(r)
                for k in ("venta", "margen", "unidades", "cv", "stock", "valor_inventario", "venta_mensual", "cobertura"):
                    d[k] = float(d[k]) if d[k] is not None else None
                d["primera_venta"] = d["primera_venta"].isoformat() if d["primera_venta"] else None
                productos.append(d)

    for c in celdas:
        for k in ("codigos", "nuevos", "declive"):
            c[k] = int(c[k])
    return {
        "calculo": {"desde": calc["desde"].isoformat(), "hasta": calc["hasta"].isoformat(),
                    "calculado_en": calc["calculado_en"].isoformat(), "calculado_por": calc["calculado_por"]},
        "alcance": alcance,
        "alcances": [{"codigo": k, "nombre": v} for k, v in NOMBRE_ALCANCE.items()],
        "celdas": celdas,
        "filtros": {"marca": marcas, "familia": familias, "proveedor": proveedores},
        "celda": celda,
        "productos": productos,
        "n_productos": n_lista,
        "limite": limite,
    }
