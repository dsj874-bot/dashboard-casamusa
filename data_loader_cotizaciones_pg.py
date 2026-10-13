"""Cotizaciones (Comercial): carga del reporte de cotizaciones del ERP y
los cuatro paneles de la pantalla /cotizaciones -- Embudo, Seguimiento,
Perdidas y No creados. Tablas en migrations/018_cotizaciones.sql.

Definiciones (las mismas del analisis del 2026-10-13):
- Cotizacion cerrada: al menos una linea con factura/boleta vinculada.
- Facturado: total cotizado de las lineas con factura (el precio
  facturado es igual al cotizado en el 99,8% de las lineas).
- No creado: lineas con codigo 100000/100005 (producto sin codigo).
- Proyecto: cotizacion de mas de $20 MM.
- Catalogo: cotizaciones de hasta $20 MM sin productos no creados; es la
  conversion comparable entre sucursales y vendedores.
"""
import re
import unicodedata
from datetime import date, datetime, timedelta

import db

CODIGOS_NO_CREADOS = (100000, 100005)
TOPE_PROYECTO = 20_000_000
TAMANO_LOTE = 5000

SUCURSALES = {
    "SI-STK": "San Isidro", "LC-STK": "Las Condes", "MR-STK": "Manuel Rodríguez",
    "MT-STK": "Matta", "CH-STK": "Chicureo", "MP-STK": "Maipú",
}
# CASE de almacen -> bodega de inventario_stock (para cruzar con el stock)
_SQL_BODEGA = "CASE l.almacen " + " ".join(
    f"WHEN '{a}' THEN '{b}'" for a, b in SUCURSALES.items()) + " END"
# Clientes de mostrador sin nombre: no tiene sentido hacerles seguimiento
_SQL_NOMINADO = "NOT (coalesce(l.codigo_cliente,'') LIKE 'CB-%%' OR l.codigo_cliente = 'C9999999-3')"

# Orden de columnas en que el navegador manda cada fila (ver
# templates/subir_cotizaciones.html) = encabezados del reporte del ERP.
COLUMNAS_ERP = [
    "Nro_Cotizacion", "Fecha_Cotizacion", "Almacen", "Codigo_Cliente", "Nombre_Cliente",
    "Vendedor", "Codigo_Articulo", "Descripcion", "Marca", "Grupo", "Familia",
    "Cantidad", "Precio_Unitario", "Total_Linea", "Nro_Pedido", "Fecha_Pedido",
    "Nro_Entrega", "Fecha_Entrega", "Nro_Factura_Boleta", "Fecha_Factura_Boleta",
]
_COLS_DB = (
    "nro_cotizacion, fecha, almacen, codigo_cliente, nombre_cliente, vendedor, codigo, "
    "descripcion, marca, grupo, familia, cantidad, precio_unitario, total, nro_pedido, "
    "fecha_pedido, nro_entrega, fecha_entrega, nro_factura, fecha_factura"
)
_FECHAS = {1, 15, 17, 19}
_ENTEROS = {0, 6, 14, 16, 18}
_NUMEROS = {11, 12, 13}


def a_json(obj):
    """Fechas a ISO (jsonify las manda en formato RFC 822) y Decimal a
    float, recorriendo dicts y listas."""
    if isinstance(obj, dict):
        return {k: a_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [a_json(v) for v in obj]
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if obj.__class__.__name__ == "Decimal":
        return float(obj)
    return obj


# ══════════════════════════════════════════════════════
#  CARGA
# ══════════════════════════════════════════════════════
def _fecha(v):
    if v in (None, ""):
        return None
    if isinstance(v, (date, datetime)):
        return v if isinstance(v, date) and not isinstance(v, datetime) else v.date()
    return date.fromisoformat(str(v)[:10])


def _entero(v):
    if v in (None, ""):
        return None
    return int(float(v))


def _numero(v):
    if v in (None, ""):
        return 0.0
    return float(v)


def normalizar_filas(filas):
    """Convierte las filas (listas en el orden de COLUMNAS_ERP) a tuplas
    listas para COPY. Lanza ValueError con un mensaje legible si una fila
    no calza con el formato del reporte."""
    salida, vistas = [], set()
    for i, f in enumerate(filas, start=1):
        if len(f) != len(COLUMNAS_ERP):
            raise ValueError(f"La fila {i} no tiene las {len(COLUMNAS_ERP)} columnas del reporte.")
        try:
            t = []
            for j, v in enumerate(f):
                if j in _FECHAS:
                    t.append(_fecha(v))
                elif j in _ENTEROS:
                    t.append(_entero(v))
                elif j in _NUMEROS:
                    t.append(_numero(v))
                else:
                    t.append(None if v in (None, "") else str(v).strip())
        except (TypeError, ValueError) as e:
            raise ValueError(f"La fila {i} tiene un dato que no se pudo leer ({e}).")
        if t[0] is None or t[1] is None or t[6] is None:
            raise ValueError(f"La fila {i} no trae N de cotizacion, fecha o codigo.")
        t = tuple(t)
        if t in vistas:          # lineas repetidas exactas en el reporte
            continue
        vistas.add(t)
        salida.append(t)
    return salida


def cargar_parte(filas):
    """Reemplaza en la base las cotizaciones que vienen en `filas` (ya
    normalizadas). Cada parte trae cotizaciones completas, asi que borrar
    y reinsertar por N de cotizacion deja la base al dia sin duplicar."""
    if not filas:
        return 0
    nros = sorted({f[0] for f in filas})
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM cotizacion_linea WHERE nro_cotizacion = ANY(%s)", (nros,))
            for i in range(0, len(filas), TAMANO_LOTE):
                with cur.copy(f"COPY cotizacion_linea ({_COLS_DB}) FROM STDIN") as cp:
                    for fila in filas[i:i + TAMANO_LOTE]:
                        cp.write_row(fila)
        conn.commit()
    return len(nros)


def registrar_carga(archivo, filas, cotizaciones, fecha_min, fecha_max, usuario):
    db.execute(
        """INSERT INTO cotizacion_carga (archivo, filas, cotizaciones, fecha_min, fecha_max, cargado_por)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (archivo, filas, cotizaciones, fecha_min, fecha_max, usuario))


def estado_cargas():
    meses = db.query_all("""
        SELECT date_trunc('month', fecha)::date AS mes, count(DISTINCT nro_cotizacion) AS cotizaciones,
               count(*) AS lineas, sum(total) AS cotizado, max(fecha) AS hasta
        FROM cotizacion_linea GROUP BY 1 ORDER BY 1 DESC""")
    ultimas = db.query_all("""
        SELECT archivo, filas, cotizaciones, fecha_min, fecha_max, cargado_por, cargado_en
        FROM cotizacion_carga ORDER BY cargado_en DESC LIMIT 10""")
    return {"meses": meses, "cargas": ultimas}


# ══════════════════════════════════════════════════════
#  FILTROS
# ══════════════════════════════════════════════════════
def opciones():
    r = db.query_one("SELECT min(fecha) AS desde, max(fecha) AS hasta FROM cotizacion_linea")
    vend = db.query_all("""
        SELECT vendedor, mode() WITHIN GROUP (ORDER BY almacen) AS almacen, count(DISTINCT nro_cotizacion) AS n
        FROM cotizacion_linea WHERE fecha >= %s GROUP BY vendedor HAVING count(DISTINCT nro_cotizacion) >= 20
        ORDER BY vendedor""", ((r["hasta"] or date.today()) - timedelta(days=365),))
    anos = []
    if r["desde"]:
        anos = list(range(r["hasta"].year, r["desde"].year - 1, -1))
    return {
        "desde": r["desde"], "hasta": r["hasta"], "anos": anos,
        "sucursales": [{"codigo": a, "nombre": n} for a, n in SUCURSALES.items()],
        "vendedores": [{"nombre": v["vendedor"], "sucursal": SUCURSALES.get(v["almacen"], v["almacen"])}
                       for v in vend if v["vendedor"] and not v["vendedor"].startswith("-")],
    }


def _where(desde, hasta, almacenes, vendedores, alias="l"):
    w = [f"{alias}.fecha BETWEEN %(desde)s AND %(hasta)s", f"{alias}.total > 0"]
    p = {"desde": desde, "hasta": hasta}
    if almacenes:
        w.append(f"{alias}.almacen = ANY(%(almacenes)s)")
        p["almacenes"] = list(almacenes)
    if vendedores:
        w.append(f"{alias}.vendedor = ANY(%(vendedores)s)")
        p["vendedores"] = list(vendedores)
    return " AND ".join(w), p


# ══════════════════════════════════════════════════════
#  EMBUDO
# ══════════════════════════════════════════════════════
_TRAMOS = [("1", "Menos de $200 mil"), ("2", "$200 mil a $1 MM"), ("3", "$1 a $5 MM"),
           ("4", "$5 a $20 MM"), ("5", "Más de $20 MM")]


def get_embudo(desde, hasta, almacenes=None, vendedores=None):
    where, p = _where(desde, hasta, almacenes, vendedores)
    p.update({"nc": list(CODIGOS_NO_CREADOS), "tope": TOPE_PROYECTO})
    filas = db.query_all(f"""
        WITH c AS (
            SELECT l.nro_cotizacion, min(l.fecha) AS fecha, min(l.almacen) AS almacen,
                   min(l.vendedor) AS vendedor, sum(l.total) AS total,
                   coalesce(sum(l.total) FILTER (WHERE l.nro_factura IS NOT NULL), 0) AS fact,
                   bool_or(l.nro_factura IS NOT NULL) AS cerrada,
                   bool_or(l.nro_pedido IS NOT NULL) AS con_pedido,
                   bool_or(l.codigo = ANY(%(nc)s)) AS nocre,
                   min(l.fecha_factura) AS primera_factura
            FROM cotizacion_linea l WHERE {where}
            GROUP BY l.nro_cotizacion
        ), x AS (
            SELECT *, date_trunc('month', fecha)::date AS mes,
                   CASE WHEN total < 200000 THEN '1' WHEN total < 1000000 THEN '2'
                        WHEN total < 5000000 THEN '3' WHEN total <= %(tope)s THEN '4' ELSE '5' END AS tramo,
                   (total <= %(tope)s AND NOT nocre) AS catalogo
            FROM c
        )
        SELECT grouping(almacen) AS g_suc, grouping(vendedor) AS g_vend, grouping(tramo) AS g_tramo,
               grouping(mes) AS g_mes, almacen, vendedor, tramo, mes,
               mode() WITHIN GROUP (ORDER BY almacen) AS suc_principal,
               count(*) AS cot, count(*) FILTER (WHERE cerrada) AS cerradas,
               sum(total) AS cotizado, sum(fact) AS facturado,
               coalesce(sum(total) FILTER (WHERE catalogo), 0) AS cot_cat,
               coalesce(sum(fact) FILTER (WHERE catalogo), 0) AS fact_cat,
               coalesce(sum(total) FILTER (WHERE nocre), 0) AS cot_nocre,
               coalesce(sum(total) FILTER (WHERE tramo = '5'), 0) AS cot_proy,
               count(*) FILTER (WHERE cerrada AND primera_factura <= fecha) AS mismo_dia,
               count(*) FILTER (WHERE con_pedido AND NOT cerrada) AS pedido_sin_factura
        FROM x
        GROUP BY GROUPING SETS ((), (almacen), (vendedor), (tramo), (mes))
    """, p)

    def fila(r):
        cot, cot_m = int(r["cot"]), float(r["cotizado"] or 0)
        return {
            "cot": cot, "cerradas": int(r["cerradas"]),
            "pct_cierre": 100 * r["cerradas"] / cot if cot else None,
            "cotizado": cot_m, "facturado": float(r["facturado"] or 0),
            "conv_monto": 100 * float(r["facturado"]) / cot_m if cot_m else None,
            "conv_catalogo": 100 * float(r["fact_cat"]) / float(r["cot_cat"]) if r["cot_cat"] else None,
            "pct_nocre": 100 * float(r["cot_nocre"]) / cot_m if cot_m else None,
            "pct_proyectos": 100 * float(r["cot_proy"]) / cot_m if cot_m else None,
            "pct_mismo_dia": 100 * r["mismo_dia"] / r["cerradas"] if r["cerradas"] else None,
            "pedido_sin_factura": int(r["pedido_sin_factura"]),
        }

    out = {"total": None, "sucursales": [], "vendedores": [], "tramos": [], "meses": []}
    tramos = dict(_TRAMOS)
    otros = []
    for r in filas:
        if r["g_suc"] and r["g_vend"] and r["g_tramo"] and r["g_mes"]:
            out["total"] = fila(r)
        elif not r["g_suc"]:
            # Almacenes chicos (DM, SE, SI-ECO...) van en "Otros" -- ver mas abajo
            if r["almacen"] in SUCURSALES:
                out["sucursales"].append({"codigo": r["almacen"], "nombre": SUCURSALES[r["almacen"]], **fila(r)})
            else:
                otros.append(r)
        elif not r["g_vend"]:
            if r["vendedor"] and not r["vendedor"].startswith("-"):
                out["vendedores"].append({"nombre": r["vendedor"],
                                          "sucursal": SUCURSALES.get(r["suc_principal"], r["suc_principal"]), **fila(r)})
        elif not r["g_tramo"]:
            out["tramos"].append({"tramo": r["tramo"], "nombre": tramos[r["tramo"]], **fila(r)})
        else:
            out["meses"].append({"mes": r["mes"], **fila(r)})
    if otros:
        suma = {k: sum(o[k] or 0 for o in otros) for k in (
            "cot", "cerradas", "cotizado", "facturado", "cot_cat", "fact_cat", "cot_nocre", "cot_proy",
            "mismo_dia", "pedido_sin_factura")}
        out["sucursales_otros"] = {"codigo": None, "nombre": "Otros almacenes", **fila(suma)}
    out["sucursales"].sort(key=lambda x: -x["facturado"])
    if otros:
        out["sucursales"].append(out.pop("sucursales_otros"))
    out["vendedores"].sort(key=lambda x: -x["facturado"])
    out["tramos"].sort(key=lambda x: x["tramo"])
    out["meses"].sort(key=lambda x: x["mes"])
    return out


# ══════════════════════════════════════════════════════
#  SEGUIMIENTO: cotizaciones recientes sin pedido ni factura
# ══════════════════════════════════════════════════════
DIAS_SEGUIMIENTO = 7


def get_seguimiento(almacenes=None, vendedores=None):
    hasta = db.query_one("SELECT max(fecha) AS f FROM cotizacion_linea")["f"]
    if not hasta:
        return {"hasta": None, "cotizaciones": []}
    desde = hasta - timedelta(days=DIAS_SEGUIMIENTO)
    where, p = _where(desde, hasta, almacenes, vendedores)
    p["nc"] = list(CODIGOS_NO_CREADOS)
    filas = db.query_all(f"""
        WITH l AS (
            SELECT l.*, {_SQL_BODEGA} AS bodega FROM cotizacion_linea l
            WHERE {where} AND {_SQL_NOMINADO}
        ), abiertas AS (
            SELECT nro_cotizacion FROM l GROUP BY nro_cotizacion
            HAVING bool_and(nro_pedido IS NULL AND nro_factura IS NULL)
        ), x AS (
            SELECT l.*, coalesce(s.stock, 0) AS st_suc, coalesce(si.stock, 0) AS st_si
            FROM l JOIN abiertas USING (nro_cotizacion)
            LEFT JOIN inventario_stock s  ON s.codigo = l.codigo AND s.bodega = l.bodega
            LEFT JOIN inventario_stock si ON si.codigo = l.codigo AND si.bodega = 'San Isidro'
        )
        SELECT nro_cotizacion, min(fecha) AS fecha, min(almacen) AS almacen, min(vendedor) AS vendedor,
               min(nombre_cliente) AS cliente, count(*) AS lineas, sum(total) AS total,
               count(*) FILTER (WHERE codigo = ANY(%(nc)s)) AS no_creados,
               count(*) FILTER (WHERE NOT codigo = ANY(%(nc)s) AND st_suc < cantidad) AS sin_stock,
               count(*) FILTER (WHERE NOT codigo = ANY(%(nc)s) AND st_suc < cantidad
                                  AND st_si >= cantidad AND bodega <> 'San Isidro') AS en_si
        FROM x GROUP BY nro_cotizacion
        ORDER BY sum(total) DESC LIMIT 400
    """, p)
    for f in filas:
        f["sucursal"] = SUCURSALES.get(f["almacen"], f["almacen"])
        f["dias"] = (hasta - f["fecha"]).days
        f["total"] = float(f["total"])
    return {"desde": desde, "hasta": hasta, "cotizaciones": filas}


def get_detalle(nro):
    filas = db.query_all(f"""
        SELECT l.codigo, l.descripcion, l.marca, l.cantidad, l.precio_unitario, l.total,
               l.nro_pedido, l.nro_factura, {_SQL_BODEGA} AS bodega,
               coalesce(s.stock, 0) AS st_suc, coalesce(si.stock, 0) AS st_si
        FROM cotizacion_linea l
        LEFT JOIN inventario_stock s  ON s.codigo = l.codigo AND s.bodega = {_SQL_BODEGA}
        LEFT JOIN inventario_stock si ON si.codigo = l.codigo AND si.bodega = 'San Isidro'
        WHERE l.nro_cotizacion = %s ORDER BY l.total DESC""", (nro,))
    for f in filas:
        for k in ("cantidad", "precio_unitario", "total", "st_suc", "st_si"):
            f[k] = float(f[k] or 0)
        f["no_creado"] = f["codigo"] in CODIGOS_NO_CREADOS
    return filas


# ══════════════════════════════════════════════════════
#  PERDIDAS: productos y clientes con mas monto sin cerrar
# ══════════════════════════════════════════════════════
def get_perdidas(desde, hasta, almacenes=None, vendedores=None):
    where, p = _where(desde, hasta, almacenes, vendedores)
    p.update({"nc": list(CODIGOS_NO_CREADOS), "tope": TOPE_PROYECTO})
    base = f"""
        WITH c AS (
            SELECT nro_cotizacion FROM cotizacion_linea l WHERE {where}
            GROUP BY nro_cotizacion HAVING sum(total) <= %(tope)s
        ), l AS (
            SELECT l.*, {_SQL_BODEGA} AS bodega,
                   (l.nro_factura IS NULL AND l.nro_pedido IS NULL) AS perdida
            FROM cotizacion_linea l JOIN c USING (nro_cotizacion)
            WHERE {where} AND NOT l.codigo = ANY(%(nc)s)
        )"""
    productos = db.query_all(base + """
        , st AS (
            SELECT codigo, sum(stock) AS stock, sum(venta_mensual) AS venta_mensual
            FROM inventario_stock
            WHERE bodega IN ('San Isidro','Las Condes','Manuel Rodríguez','Matta','Chicureo','Maipú')
            GROUP BY codigo
        ), p AS (
            SELECT codigo, max(descripcion) AS descripcion, max(marca) AS marca,
                   count(DISTINCT codigo_cliente) FILTER (WHERE perdida) AS clientes,
                   count(*) FILTER (WHERE perdida) AS lineas,
                   sum(total) AS cotizado, coalesce(sum(total) FILTER (WHERE perdida), 0) AS perdido,
                   count(*) FILTER (WHERE perdida AND bodega IS NOT NULL) AS lin_suc,
                   count(*) FILTER (WHERE perdida AND bodega IS NOT NULL AND bodega <> 'San Isidro') AS lin_fuera_si
            FROM l GROUP BY codigo
        )
        SELECT p.*, coalesce(st.stock, 0) AS stock, coalesce(st.venta_mensual, 0) AS venta_mensual,
               coalesce(si.stock, 0) AS stock_si
        FROM p LEFT JOIN st USING (codigo)
        LEFT JOIN inventario_stock si ON si.codigo = p.codigo AND si.bodega = 'San Isidro'
        WHERE p.perdido > 0
        ORDER BY p.perdido DESC LIMIT 60""", p)
    for f in productos:
        for k in ("cotizado", "perdido", "stock", "venta_mensual", "stock_si"):
            f[k] = float(f[k] or 0)
        f["pct_perdido"] = 100 * f["perdido"] / f["cotizado"] if f["cotizado"] else None

    # Lineas perdidas: ¿habia stock hoy en la sucursal, en San Isidro o en ninguna?
    stock = db.query_one(base + f"""
        , emp AS (
            SELECT codigo, sum(stock) AS stock FROM inventario_stock
            WHERE bodega IN ('San Isidro','Las Condes','Manuel Rodríguez','Matta','Chicureo','Maipú')
            GROUP BY codigo
        ), x AS (
            SELECT l.total, l.cantidad, l.bodega, coalesce(s.stock, 0) AS st_suc, coalesce(si.stock, 0) AS st_si,
                   coalesce(emp.stock, 0) AS st_emp
            FROM l
            LEFT JOIN inventario_stock s  ON s.codigo = l.codigo AND s.bodega = l.bodega
            LEFT JOIN inventario_stock si ON si.codigo = l.codigo AND si.bodega = 'San Isidro'
            LEFT JOIN emp ON emp.codigo = l.codigo
            WHERE l.perdida AND l.bodega IS NOT NULL
        )
        SELECT count(*) AS lineas, coalesce(sum(total), 0) AS monto,
               count(*) FILTER (WHERE st_suc >= cantidad) AS con_stock,
               coalesce(sum(total) FILTER (WHERE st_suc >= cantidad), 0) AS monto_con_stock,
               count(*) FILTER (WHERE st_suc < cantidad AND bodega <> 'San Isidro' AND st_si >= cantidad) AS en_si,
               coalesce(sum(total) FILTER (WHERE st_suc < cantidad AND bodega <> 'San Isidro' AND st_si >= cantidad), 0) AS monto_en_si,
               count(*) FILTER (WHERE st_suc < cantidad AND st_emp < cantidad) AS sin_stock,
               coalesce(sum(total) FILTER (WHERE st_suc < cantidad AND st_emp < cantidad), 0) AS monto_sin_stock
        FROM x""", p)
    stock = {k: float(v or 0) for k, v in stock.items()}

    clientes = db.query_all(f"""
        WITH c AS (
            SELECT l.nro_cotizacion, min(l.codigo_cliente) AS cli, min(l.nombre_cliente) AS nombre,
                   sum(l.total) AS total,
                   coalesce(sum(l.total) FILTER (WHERE l.nro_factura IS NOT NULL), 0) AS fact,
                   bool_or(l.nro_factura IS NOT NULL) AS cerrada,
                   bool_or(l.codigo = ANY(%(nc)s)) AS nocre
            FROM cotizacion_linea l WHERE {where} AND {_SQL_NOMINADO}
            GROUP BY l.nro_cotizacion
        )
        SELECT cli, max(nombre) AS nombre, count(*) AS cot, count(*) FILTER (WHERE cerrada) AS cerradas,
               sum(total) AS cotizado, sum(fact) AS facturado,
               count(*) FILTER (WHERE nocre) AS con_nocre
        FROM c GROUP BY cli HAVING count(*) >= 5
        ORDER BY sum(total) - sum(fact) DESC LIMIT 40""", p)
    for f in clientes:
        f["cotizado"], f["facturado"] = float(f["cotizado"]), float(f["facturado"])
        f["pct_cierre"] = 100 * f["cerradas"] / f["cot"]
        f["perdido"] = f["cotizado"] - f["facturado"]
    return {"productos": productos, "stock": stock, "clientes": clientes}


# ══════════════════════════════════════════════════════
#  NO CREADOS: lo que piden y no tenemos codigo
# ══════════════════════════════════════════════════════
_STOP = {"DE", "PARA", "CON", "SIN", "X", "P", "Y", "EL", "LA", "EN", "TIPO", "MTS", "MT", "MM", "CM", "UN",
         "UNID", "GALV", "ZN", "BLANCO", "NEGRO", "GRIS", "ROJO", "VERDE", "AZUL", "DOBLE", "SIMPLE", "LED",
         "LIBRE", "HALOGENO", "HALOGENOS", "REF", "MODELO", "MOD"}
_SIN = {"CABLES": "CABLE", "ALAMBRE": "CABLE", "TUBERIA": "TUBO", "CONDUIT": "TUBO", "LUMINARIAS": "LUMINARIA",
        "FOCOS": "FOCO", "PROYECTORES": "PROYECTOR", "REFLECTOR": "PROYECTOR", "TOMACORRIENTE": "ENCHUFE",
        "ESCALERILLA": "BANDEJA", "PORTACONDUCTORA": "BANDEJA"}


def _concepto(desc):
    t = unicodedata.normalize("NFKD", str(desc or "").upper()).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    toks = []
    for w in t.split():
        w = _SIN.get(w, w)
        if not re.search(r"\d", w) and w not in _STOP and len(w) > 2 and w not in toks:
            toks.append(w)
    return " ".join(toks[:2]).capitalize() if toks else "(sin descripción)"


def get_no_creados(desde, hasta, almacenes=None, vendedores=None):
    where, p = _where(desde, hasta, almacenes, vendedores)
    p["nc"] = list(CODIGOS_NO_CREADOS)
    lineas = db.query_all(f"""
        SELECT l.nro_cotizacion, l.fecha, l.almacen, l.vendedor, l.codigo_cliente, l.nombre_cliente,
               l.descripcion, l.cantidad, l.precio_unitario, l.total
        FROM cotizacion_linea l WHERE {where} AND l.codigo = ANY(%(nc)s)
        ORDER BY l.fecha DESC, l.total DESC""", p)
    tot_cot = db.query_one(f"SELECT coalesce(sum(l.total), 0) AS t FROM cotizacion_linea l WHERE {where}", p)["t"]

    conceptos = {}
    for f in lineas:
        f["total"] = float(f["total"])
        f["cantidad"] = float(f["cantidad"])
        f["precio_unitario"] = float(f["precio_unitario"])
        f["sucursal"] = SUCURSALES.get(f["almacen"], f["almacen"])
        k = _concepto(f["descripcion"])
        c = conceptos.setdefault(k, {"concepto": k, "clientes": set(), "cot": set(), "vendedores": set(),
                                     "lineas": 0, "monto": 0.0, "ejemplos": {}})
        c["clientes"].add(f["codigo_cliente"])
        c["cot"].add(f["nro_cotizacion"])
        c["vendedores"].add(f["vendedor"])
        c["lineas"] += 1
        if f["total"] <= TOPE_PROYECTO:
            c["monto"] += f["total"]
        d = (f["descripcion"] or "").strip()
        c["ejemplos"][d] = c["ejemplos"].get(d, 0) + 1
    lista = []
    for c in conceptos.values():
        lista.append({"concepto": c["concepto"], "clientes": len(c["clientes"]), "cotizaciones": len(c["cot"]),
                      "vendedores": len(c["vendedores"]), "lineas": c["lineas"], "monto": c["monto"],
                      "ejemplo": max(c["ejemplos"], key=c["ejemplos"].get) if c["ejemplos"] else ""})
    lista.sort(key=lambda x: (-x["clientes"], -x["cotizaciones"]))

    # Especiales (codigos 6xxxxx) vendidos a varios clientes: candidatos a catalogo
    pv = {"desde": desde, "hasta": hasta}
    w_suc = ""
    if almacenes:
        w_suc = " AND sucursal = ANY(%(almacenes)s)"
        pv["almacenes"] = list(almacenes)
    esp_tot = db.query_one(f"""
        SELECT coalesce(sum(total) FILTER (WHERE codigo_cm BETWEEN 600000 AND 699999), 0) AS venta_esp,
               coalesce(sum(utilidad_bruta) FILTER (WHERE codigo_cm BETWEEN 600000 AND 699999), 0) AS util_esp,
               coalesce(sum(total), 0) AS venta, coalesce(sum(utilidad_bruta), 0) AS util
        FROM v_ventas WHERE fecha_doc BETWEEN %(desde)s AND %(hasta)s
          AND tipo_doc NOT IN ('NOTA DE CREDITO', 'NOTA DE DEBITO'){w_suc}""", pv)
    especiales = db.query_all(f"""
        SELECT codigo_cm AS codigo, max(descripcion) AS descripcion, max(marca) AS marca,
               count(DISTINCT codigo_cliente) AS clientes, count(DISTINCT date_trunc('month', fecha_doc)) AS meses,
               sum(cantidad) AS unidades, sum(total) AS venta, sum(utilidad_bruta) AS utilidad
        FROM v_ventas
        WHERE fecha_doc BETWEEN %(desde)s AND %(hasta)s AND codigo_cm BETWEEN 600000 AND 699999
          AND tipo_doc NOT IN ('NOTA DE CREDITO', 'NOTA DE DEBITO'){w_suc}
        GROUP BY codigo_cm HAVING count(DISTINCT codigo_cliente) >= 3
        ORDER BY count(DISTINCT codigo_cliente) DESC, sum(total) DESC LIMIT 60""", pv)
    for e in especiales:
        e["venta"], e["unidades"] = float(e["venta"] or 0), float(e["unidades"] or 0)
        e["pct_mg"] = 100 * float(e.pop("utilidad") or 0) / e["venta"] if e["venta"] else None

    monto = sum(f["total"] for f in lineas)
    venta_esp = float(esp_tot["venta_esp"])
    venta_cat = float(esp_tot["venta"]) - venta_esp
    return {
        "kpis": {
            "lineas": len(lineas), "cotizaciones": len({f["nro_cotizacion"] for f in lineas}),
            "clientes": len({f["codigo_cliente"] for f in lineas}), "monto": monto,
            "pct_cotizado": 100 * monto / float(tot_cot) if tot_cot else None,
            "venta_especiales": venta_esp,
            "pct_venta_especiales": 100 * venta_esp / float(esp_tot["venta"]) if esp_tot["venta"] else None,
            "mg_especiales": 100 * float(esp_tot["util_esp"]) / venta_esp if venta_esp else None,
            "mg_catalogo": 100 * (float(esp_tot["util"]) - float(esp_tot["util_esp"])) / venta_cat if venta_cat else None,
        },
        "conceptos": lista[:40],
        "especiales": especiales,
        "lineas": lineas[:300],
    }
