"""
Equivalente Postgres de data_loader_adquisiciones.py -- lee de las
tablas compras/recepciones en vez de los Excel locales. A diferencia
de las pantallas originales (que mostraban Comprado y Recibido en
pantallas separadas), estas funciones devuelven AMBOS juntos en cada
resultado -- pedido explicito del usuario: "cada pantalla debe tener
Compras y recepciones".

Igual que en data_loader_pg.py: una conexion por funcion, agregados
por ventana de fecha con FILTER (WHERE ...) en un solo GROUP BY/scan.
"""
import math
import numpy as np
import db
from data_loader_obligatorios import SIGLA_SUCURSAL

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


# Marcas que quedan fuera del KPI de abastecimiento: son productos
# ARMADOS por Casa Musa, no comprados. Aunque el maestro los marque como
# nacionales, sus componentes se compran bajo otras marcas, asi que la
# venta y la compra de una misma unidad nunca aparecen bajo la misma
# marca y el ratio de esa fila no significa nada.
# (TECH lo indico el usuario el 2026-09-21.)
MARCAS_ARMADAS = ("TECH",)

# Proveedores cuyos productos son IMPORTADOS aunque el maestro los tenga
# marcados como procedencia 'Nacional'. Sus compras no pasan por las
# tablas compras/recepciones (igual que el resto de las importaciones),
# asi que apareceran vendiendo sin comprar nunca y arrastran el
# indicador hacia abajo sin que sea real.
#
# ASCABLE (PESA5842402-9): 35 SKUs de cable, $209 millones vendidos en
# 2026, 44.354 unidades en stock y CERO lineas de compra en toda la
# historia. Lo confirmo el usuario el 2026-09-21. Se excluye por
# proveedor y no por marca porque sus productos son marca CACG, que si
# tiene compras nacionales legitimas por otro lado.
#
# OJO: esto es un parche sobre un dato malo del maestro de productos.
# Si algun dia se corrige la procedencia de esos SKUs, esta lista sobra.
PROVEEDORES_IMPORTADOS = ("PESA5842402-9",)


def var_pct(actual, anterior):
    if anterior == 0:
        return 0.0
    return round((float(actual) - float(anterior)) / float(anterior) * 100, 1)


def _filtro_tipo(tipo_compra):
    """Fragmento SQL + parametro para acotar por tipo (STOCK/PEDIDO) --
    compras usa la columna tipo_compra, recepciones usa tipo_oc (mismos
    valores, distinto nombre de columna en cada tabla de origen)."""
    if not tipo_compra:
        return "", "", {}
    return " AND tipo_compra = %(tipo)s", " AND tipo_oc = %(tipo)s", {"tipo": tipo_compra}


def _fecha_datos_pg(cur, frag_c, frag_r, params):
    cur.execute(f"SELECT max(fecha_creacion) AS f FROM compras WHERE ano = 2026 {frag_c}", params)
    fc = cur.fetchone()["f"]
    cur.execute(f"SELECT max(fecha_recepcion) AS f FROM recepciones WHERE ano = 2026 {frag_r}", params)
    fr = cur.fetchone()["f"]
    candidatas = [d for d in (fc, fr) if d is not None]
    return max(candidatas) if candidatas else None


def get_resumen_combinado_pg(tipo_compra=None):
    """KPIs de Adquisiciones -- dos filas (Comprado / Recibido), mismos
    3 grupos de comparacion que el resto del dashboard (Año Actual/
    Anterior, Mes Actual/Año Ant, Mes Actual/Mes Ant)."""
    frag_c, frag_r, params = _filtro_tipo(tipo_compra)

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            fecha_datos = _fecha_datos_pg(cur, frag_c, frag_r, params)
            if fecha_datos is None:
                return {"filas": [], "ano_actual": 2026, "ano_anterior": 2025, "fecha_datos": None}

            mes_actual = fecha_datos.month
            dia_actual = fecha_datos.day
            mes_anterior = mes_actual - 1 if mes_actual > 1 else 12
            params.update({"mes_actual": mes_actual, "dia_actual": dia_actual, "mes_anterior": mes_anterior})

            cur.execute(
                f"""SELECT
                      coalesce(sum(precio_total) FILTER (WHERE ano = 2026), 0) AS ano_26,
                      coalesce(sum(precio_total) FILTER (
                          WHERE ano = 2025 AND (
                              extract(month from fecha_creacion) < %(mes_actual)s OR
                              (extract(month from fecha_creacion) = %(mes_actual)s AND extract(day from fecha_creacion) <= %(dia_actual)s)
                          )
                      ), 0) AS ano_25,
                      coalesce(sum(precio_total) FILTER (WHERE ano = 2026 AND extract(month from fecha_creacion) = %(mes_actual)s), 0) AS mes_26,
                      coalesce(sum(precio_total) FILTER (
                          WHERE ano = 2025 AND extract(month from fecha_creacion) = %(mes_actual)s AND extract(day from fecha_creacion) <= %(dia_actual)s
                      ), 0) AS mes_25,
                      coalesce(sum(precio_total) FILTER (
                          WHERE ano = 2026 AND extract(month from fecha_creacion) = %(mes_anterior)s AND extract(day from fecha_creacion) <= %(dia_actual)s
                      ), 0) AS mes_ant,
                      count(DISTINCT n_orden_compra) FILTER (WHERE ano = 2026) AS oc_26,
                      count(DISTINCT nombre_proveedor) FILTER (WHERE ano = 2026) AS proveedores_26
                    FROM compras WHERE ano IN (2025, 2026) {frag_c}""",
                params,
            )
            rc = cur.fetchone()

            cur.execute(
                f"""SELECT
                      coalesce(sum(total_clp) FILTER (WHERE ano = 2026), 0) AS ano_26,
                      coalesce(sum(total_clp) FILTER (
                          WHERE ano = 2025 AND (
                              extract(month from fecha_recepcion) < %(mes_actual)s OR
                              (extract(month from fecha_recepcion) = %(mes_actual)s AND extract(day from fecha_recepcion) <= %(dia_actual)s)
                          )
                      ), 0) AS ano_25,
                      coalesce(sum(total_clp) FILTER (WHERE ano = 2026 AND extract(month from fecha_recepcion) = %(mes_actual)s), 0) AS mes_26,
                      coalesce(sum(total_clp) FILTER (
                          WHERE ano = 2025 AND extract(month from fecha_recepcion) = %(mes_actual)s AND extract(day from fecha_recepcion) <= %(dia_actual)s
                      ), 0) AS mes_25,
                      coalesce(sum(total_clp) FILTER (
                          WHERE ano = 2026 AND extract(month from fecha_recepcion) = %(mes_anterior)s AND extract(day from fecha_recepcion) <= %(dia_actual)s
                      ), 0) AS mes_ant,
                      count(DISTINCT n_recepcion) FILTER (WHERE ano = 2026) AS rec_26,
                      count(DISTINCT nombre_proveedor) FILTER (WHERE ano = 2026) AS proveedores_26
                    FROM recepciones WHERE ano IN (2025, 2026) {frag_r}""",
                params,
            )
            rr = cur.fetchone()

    def _fila(categoria, r):
        v26, v25, m26, m25, mant = float(r["ano_26"]), float(r["ano_25"]), float(r["mes_26"]), float(r["mes_25"]), float(r["mes_ant"])
        return {
            "categoria":     categoria,
            "v_ano_actual":  round(v26, 0),
            "v_ano_anterior": round(v25, 0),
            "var_ano":       var_pct(v26, v25),
            "v_mes_actual":  round(m26, 0),
            "v_mes_ant_ano": round(m25, 0),
            "var_mes_ano":   var_pct(m26, m25),
            "v_mes_ant_mes": round(mant, 0),
            "var_mes_mes":   var_pct(m26, mant),
        }

    filas = [_fila("Comprado", rc), _fila("Recibido", rr)]

    return {
        "filas":              filas,
        "n_oc_actual":        int(rc["oc_26"]),
        "n_recepciones_actual": int(rr["rec_26"]),
        "proveedores_compra": int(rc["proveedores_26"]),
        "proveedores_recepcion": int(rr["proveedores_26"]),
        "mes_nombre":         MESES.get(mes_actual, ""),
        "mes_anterior_nombre": MESES.get(mes_anterior, ""),
        "ano_actual":         2026,
        "ano_anterior":       2025,
        "fecha_datos":        fecha_datos.strftime("%d-%m-%Y"),
    }


def get_por_mes_combinado_pg(tipo_compra=None):
    """Comprado y Recibido por mes calendario, año actual vs año
    anterior completo -- para el grafico de evolucion mensual."""
    frag_c, frag_r, params = _filtro_tipo(tipo_compra)

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT extract(month from fecha_creacion)::int AS mes, ano, sum(precio_total) AS total
                    FROM compras WHERE ano IN (2025, 2026) {frag_c}
                    GROUP BY mes, ano""",
                params,
            )
            filas_c = {(r["mes"], r["ano"]): float(r["total"]) for r in cur.fetchall()}

            cur.execute(
                f"""SELECT extract(month from fecha_recepcion)::int AS mes, ano, sum(total_clp) AS total
                    FROM recepciones WHERE ano IN (2025, 2026) {frag_r}
                    GROUP BY mes, ano""",
                params,
            )
            filas_r = {(r["mes"], r["ano"]): float(r["total"]) for r in cur.fetchall()}

    meses = []
    for mes in range(1, 13):
        meses.append({
            "mes":               mes,
            "mes_nombre":        MESES.get(mes, ""),
            "comprado_actual":   round(filas_c.get((mes, 2026), 0.0), 0),
            "comprado_anterior": round(filas_c.get((mes, 2025), 0.0), 0),
            "recibido_actual":   round(filas_r.get((mes, 2026), 0.0), 0),
            "recibido_anterior": round(filas_r.get((mes, 2025), 0.0), 0),
        })

    return {"meses": meses, "ano_actual": 2026, "ano_anterior": 2025}


def get_por_proveedor_combinado_pg(tipo_compra=None):
    """Compra y Recepcion por proveedor -- dos filas por proveedor
    (Comprado / Recibido), año actual (YTD) vs mismo periodo año
    anterior, y participacion sobre el total comprado del año actual."""
    frag_c, frag_r, params = _filtro_tipo(tipo_compra)

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            fecha_datos = _fecha_datos_pg(cur, frag_c, frag_r, params)
            if fecha_datos is None:
                return {"proveedores": [], "ano_actual": 2026, "ano_anterior": 2025}
            mes_actual = fecha_datos.month
            dia_actual = fecha_datos.day
            params.update({"mes_actual": mes_actual, "dia_actual": dia_actual})

            cur.execute(
                f"""SELECT nombre_proveedor,
                      coalesce(sum(precio_total) FILTER (WHERE ano = 2026), 0) AS c_26,
                      coalesce(sum(precio_total) FILTER (
                          WHERE ano = 2025 AND (
                              extract(month from fecha_creacion) < %(mes_actual)s OR
                              (extract(month from fecha_creacion) = %(mes_actual)s AND extract(day from fecha_creacion) <= %(dia_actual)s)
                          )
                      ), 0) AS c_25,
                      count(DISTINCT n_orden_compra) FILTER (WHERE ano = 2026) AS n_oc
                    FROM compras
                    WHERE ano IN (2025, 2026) AND nombre_proveedor IS NOT NULL {frag_c}
                    GROUP BY nombre_proveedor""",
                params,
            )
            compra_por_prov = {r["nombre_proveedor"]: r for r in cur.fetchall()}

            cur.execute(
                f"""SELECT nombre_proveedor,
                      coalesce(sum(total_clp) FILTER (WHERE ano = 2026), 0) AS r_26,
                      coalesce(sum(total_clp) FILTER (
                          WHERE ano = 2025 AND (
                              extract(month from fecha_recepcion) < %(mes_actual)s OR
                              (extract(month from fecha_recepcion) = %(mes_actual)s AND extract(day from fecha_recepcion) <= %(dia_actual)s)
                          )
                      ), 0) AS r_25,
                      count(DISTINCT n_recepcion) FILTER (WHERE ano = 2026) AS n_rec
                    FROM recepciones
                    WHERE ano IN (2025, 2026) AND nombre_proveedor IS NOT NULL {frag_r}
                    GROUP BY nombre_proveedor""",
                params,
            )
            recepcion_por_prov = {r["nombre_proveedor"]: r for r in cur.fetchall()}

            # Compra/Recepcion por proveedor x mes (año actual) -- para
            # las columnas Ene..Dic de estacionalidad en la tabla.
            cur.execute(
                f"""SELECT nombre_proveedor, extract(month from fecha_creacion)::int AS mes,
                      sum(precio_total) AS total
                    FROM compras
                    WHERE ano = 2026 AND nombre_proveedor IS NOT NULL {frag_c}
                    GROUP BY nombre_proveedor, mes""",
                params,
            )
            c_por_prov_mes = {}
            for r in cur.fetchall():
                c_por_prov_mes.setdefault(r["nombre_proveedor"], {})[r["mes"]] = float(r["total"])

            cur.execute(
                f"""SELECT nombre_proveedor, extract(month from fecha_recepcion)::int AS mes,
                      sum(total_clp) AS total
                    FROM recepciones
                    WHERE ano = 2026 AND nombre_proveedor IS NOT NULL {frag_r}
                    GROUP BY nombre_proveedor, mes""",
                params,
            )
            r_por_prov_mes = {}
            for r in cur.fetchall():
                r_por_prov_mes.setdefault(r["nombre_proveedor"], {})[r["mes"]] = float(r["total"])

    total_compra_26 = sum(float(r["c_26"]) for r in compra_por_prov.values())
    nombres = set(compra_por_prov) | set(recepcion_por_prov)

    proveedores = []
    for nombre in nombres:
        rc = compra_por_prov.get(nombre)
        rr = recepcion_por_prov.get(nombre)
        c26 = float(rc["c_26"]) if rc else 0.0
        c25 = float(rc["c_25"]) if rc else 0.0
        r26 = float(rr["r_26"]) if rr else 0.0
        r25 = float(rr["r_25"]) if rr else 0.0
        meses_c = c_por_prov_mes.get(nombre, {})
        meses_r = r_por_prov_mes.get(nombre, {})
        fila_comprado = {
            "categoria":      "Comprado",
            "v_ano_actual":   round(c26, 0),
            "v_ano_anterior": round(c25, 0),
            "var_ano":        var_pct(c26, c25),
        }
        fila_recibido = {
            "categoria":      "Recibido",
            "v_ano_actual":   round(r26, 0),
            "v_ano_anterior": round(r25, 0),
            "var_ano":        var_pct(r26, r25),
        }
        for mes in range(1, 13):
            fila_comprado[f"mes_{mes}"] = round(meses_c.get(mes, 0.0), 0)
            fila_recibido[f"mes_{mes}"] = round(meses_r.get(mes, 0.0), 0)
        proveedores.append({
            "nombre":        nombre,
            "n_oc":          int(rc["n_oc"]) if rc else 0,
            "n_recepciones": int(rr["n_rec"]) if rr else 0,
            "participacion": round(c26 / total_compra_26 * 100, 2) if total_compra_26 > 0 else 0.0,
            "filas": [fila_comprado, fila_recibido],
        })

    proveedores.sort(key=lambda p: -p["filas"][0]["v_ano_actual"])

    return {
        "proveedores": proveedores,
        "ano_actual":  2026,
        "ano_anterior": 2025,
    }


# Piezas comunes del KPI de abastecimiento, para que la serie mensual y
# el detalle por proveedor filtren EXACTAMENTE igual (si se separan, el
# grafico y la tabla empiezan a contar universos distintos).
#
# `defecto` = proveedor responsable de cada producto. La atribucion va
# por ahi y no por el proveedor de cada transaccion, y la diferencia no
# es menor: el 17% del monto comprado se le compra a alguien distinto
# del proveedor por defecto del producto (medido 2026-09-21). Con
# atribucion transaccional, la venta y la compra de la misma unidad
# caerian en filas distintas -- uno apareceria sobreabastecido y el otro
# consumiendo stock, los dos falsos. Atribuyendo todo al responsable del
# producto, las tres cifras de una misma unidad siempre caen juntas.
_CTE_ABAST = """
    corte AS (
        SELECT least(
            (SELECT max(fecha_conta)     FROM ventas      WHERE ano = 2026),
            (SELECT max(fecha_creacion)  FROM compras     WHERE ano = 2026),
            (SELECT max(fecha_recepcion) FROM recepciones WHERE ano = 2026)
        ) AS f
    ),
    defecto AS (
        SELECT codigo, rut FROM (
            SELECT codigo_cm AS codigo, proveedor_por_defecto AS rut,
                   row_number() OVER (PARTITION BY codigo_cm
                                      ORDER BY count(*) DESC, proveedor_por_defecto) AS rn
              FROM ventas
             WHERE proveedor_por_defecto IS NOT NULL AND trim(proveedor_por_defecto) <> ''
             GROUP BY codigo_cm, proveedor_por_defecto
        ) t WHERE rn = 1
    ),
    nombres AS (
        SELECT rut, nombre FROM (
            SELECT rut, nombre,
                   row_number() OVER (PARTITION BY rut ORDER BY n DESC) AS rn
              FROM (
                SELECT rut, nombre_proveedor AS nombre, count(*) AS n
                  FROM compras WHERE nombre_proveedor IS NOT NULL GROUP BY 1, 2
                UNION ALL
                SELECT rut_proveedor, nombre_proveedor, count(*)
                  FROM recepciones WHERE nombre_proveedor IS NOT NULL GROUP BY 1, 2
              ) x
        ) y WHERE rn = 1
    ),
    movs AS (
        SELECT d.rut, v.fecha_conta AS fecha, p.familia, p.subfamilia,
               v.costo_total AS costo_venta, 0::numeric AS comprado, 0::numeric AS recibido
          FROM ventas v
          JOIN productos p ON p.codigo = v.codigo_cm
          LEFT JOIN defecto d ON d.codigo = v.codigo_cm
         CROSS JOIN corte
         WHERE v.ano = 2026 AND v.fecha_conta <= corte.f
           AND p.procedencia = 'Nacional'
           AND coalesce(p.marca, '') <> ALL(%(armadas)s)
           AND coalesce(d.rut, '') <> ALL(%(prov_imp)s)
        UNION ALL
        SELECT d.rut, c.fecha_creacion, p.familia, p.subfamilia, 0::numeric, c.precio_total, 0::numeric
          FROM compras c
          JOIN productos p ON p.codigo = c.codigo
          LEFT JOIN defecto d ON d.codigo = c.codigo
         CROSS JOIN corte
         WHERE c.ano = 2026 AND c.fecha_creacion <= corte.f
           AND p.procedencia = 'Nacional'
           AND coalesce(p.marca, '') <> ALL(%(armadas)s)
           AND coalesce(d.rut, '') <> ALL(%(prov_imp)s)
        UNION ALL
        SELECT d.rut, r.fecha_recepcion, p.familia, p.subfamilia, 0::numeric, 0::numeric, r.total_clp
          FROM recepciones r
          JOIN productos p ON p.codigo = r.codigo
          LEFT JOIN defecto d ON d.codigo = r.codigo
         CROSS JOIN corte
         WHERE r.ano = 2026 AND r.fecha_recepcion <= corte.f
           AND p.procedencia = 'Nacional'
           AND coalesce(p.marca, '') <> ALL(%(armadas)s)
           AND coalesce(d.rut, '') <> ALL(%(prov_imp)s)
    )
"""


def get_abastecimiento_proveedor_pg():
    """Costo de venta vs Comprado vs Recibido por proveedor, solo
    productos nacionales -- para medir sobreabastecimiento. Incluye la
    serie mensual del ratio, que es donde se ve si el problema se esta
    agravando o corrigiendo.

    La idea (pedido del usuario, 2026-09-21): lo que sale de bodega
    valorizado a costo es el consumo real; lo que entra es lo que se
    recibe. Si entra mas de lo que sale, el inventario crece.
    "Comprado" va al lado porque adelanta el problema: es lo que
    todavia no llega pero ya esta comprometido.

    Se mira por proveedor y no por marca porque la marca es como esta
    ordenado el catalogo, no como se decide: uno no deja de comprar una
    marca, deja de pedirle a un proveedor.

    Cuatro cosas que hubo que resolver para que el numero signifique
    algo, todas medidas el 2026-09-21:

    1. BASE DE COSTO. El CUP con que se valoriza la venta ya trae flete
       y nacionalizacion; contra el precio de factura de la OC la
       diferencia es de 0,4% (sum(cup*cantidad)/sum(precio_total) =
       1,0043). Son comparables y el equilibrio es 1,0, sin correcciones.

    2. SOLO NACIONAL. compras y recepciones son 100% nacionales (24.995
       y 25.031 filas, ni una importada): las importaciones entran por
       otra via. Mezclarlas hunde el indicador a 0,757 contra 0,901.

    3. FECHA DE CORTE. Las tres fuentes las cargan procesos distintos y
       no siempre llegan al mismo dia; se recorta todo al menor de los
       tres maximos.

    4. PROVEEDORES QUE VENDEN SIN COMPRAR. El caso grande era ASCABLE
       ($209 millones, 8,4% del costo de venta, cero compras en toda la
       historia): resulto ser producto importado mal marcado como
       nacional en el maestro, y se excluye por PROVEEDORES_IMPORTADOS.
       Los que quedan sin ninguna compra registrada son marginales
       (~$0,7 millones) pero igual van aparte y la pantalla los nombra,
       para que el costo de venta que se muestra siempre sea el mismo
       universo contra el que se compara.
    """
    params = {"armadas": list(MARCAS_ARMADAS),
              "prov_imp": list(PROVEEDORES_IMPORTADOS)}

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:

            # Desglose familia/subfamilia dentro de cada proveedor. Es el
            # mismo escaneo, solo agrupado con dos columnas mas: 422
            # filas y 48 KB, se manda junto con la pagina. A nivel de
            # CODIGO serian 4.147 filas y 706 KB, y ademas el ratio ahi
            # se vuelve ruido (un SKU que se compra cada tres meses da
            # 0,00 o 15 sin que signifique nada). El detalle por producto
            # va en la pantalla de devoluciones, que es otra pregunta.
            cur.execute(
                f"""WITH {_CTE_ABAST}
                SELECT coalesce(m.rut, '(sin proveedor asignado)') AS rut,
                       coalesce(n.nombre, m.rut, 'Sin proveedor asignado')        AS nombre,
                       coalesce(nullif(trim(m.familia), ''), 'Sin familia')       AS familia,
                       coalesce(nullif(trim(m.subfamilia), ''), 'Sin subfamilia') AS subfamilia,
                       coalesce(sum(m.costo_venta), 0) AS costo_venta,
                       coalesce(sum(m.comprado), 0)    AS comprado,
                       coalesce(sum(m.recibido), 0)    AS recibido,
                       (SELECT f FROM corte)           AS fecha_corte
                  FROM movs m
                  LEFT JOIN nombres n ON n.rut = m.rut
                 GROUP BY 1, 2, 3, 4
                """,
                params,
            )
            filas_det = cur.fetchall()

            cur.execute(
                f"""WITH {_CTE_ABAST}
                SELECT extract(month from fecha)::int AS mes,
                       coalesce(sum(costo_venta), 0) AS costo_venta,
                       coalesce(sum(comprado), 0)    AS comprado,
                       coalesce(sum(recibido), 0)    AS recibido
                  FROM movs
                 -- Mismo universo EXACTO que la tabla de abajo: los
                 -- grupos con alguna compra registrada, incluido el de
                 -- los productos sin proveedor asignado (rut NULL), que
                 -- la tabla sí lista. Antes este WHERE pedía
                 -- "rut IS NOT NULL" y dejaba ese grupo fuera solo del
                 -- gráfico: la suma de los meses daba -52,8 millones
                 -- contra los -38,1 del total. Se agrupa por el rut
                 -- normalizado para que NULL sea un grupo mas y no se
                 -- caiga en el IN.
                 WHERE coalesce(rut, '(sin proveedor asignado)') IN (
                           SELECT coalesce(rut, '(sin proveedor asignado)')
                             FROM movs GROUP BY 1
                            HAVING sum(comprado) + sum(recibido) > 0)
                 GROUP BY 1 ORDER BY 1
                """,
                params,
            )
            filas_mes = cur.fetchall()

    def _fila(nombre, cv, co, re, rut=None):
        return {
            "proveedor":       nombre,
            "nombre":          nombre,
            "rut":             rut,
            "costo_venta":     round(cv, 0),
            "comprado":        round(co, 0),
            "recibido":        round(re, 0),
            # None (no 0) cuando no hubo venta: el ratio no existe y el
            # frontend debe mostrar "—", no inventar un numero.
            "ratio_recibido":  round(re / cv, 3) if cv > 0 else None,
            "brecha_recibido": round(re - cv, 0),
        }

    # Se arma de abajo hacia arriba -- subfamilia, familia, proveedor,
    # total -- y cada nivel SUMA LOS VALORES YA REDONDEADOS del nivel de
    # abajo. Si cada uno se redondeara por su cuenta desde el dato
    # original, las columnas no cerrarian (paso: una familia daba $3 de
    # diferencia contra sus subfamilias), y una tabla que no cuadra hace
    # dudar de todo lo demas.
    arbol, nombres_rut, fecha_corte = {}, {}, None
    for r in filas_det:
        fecha_corte = fecha_corte or r["fecha_corte"]
        nombres_rut[r["rut"]] = r["nombre"]
        fams = arbol.setdefault(r["rut"], {})
        fams.setdefault(r["familia"], {})[r["subfamilia"]] = (
            float(r["costo_venta"]), float(r["comprado"]), float(r["recibido"])
        )

    proveedores, sin_compras = [], []
    tot_cv = tot_co = tot_re = 0.0
    fuera_cv = 0.0

    for rut, fams in arbol.items():
        lista_fams = []
        p_cv = p_co = p_re = 0.0
        for nom_f, subs in fams.items():
            lista_subs = [_fila(n, *v) for n, v in subs.items()]
            lista_subs.sort(key=lambda x: -x["brecha_recibido"])
            f_cv = sum(x["costo_venta"] for x in lista_subs)
            f_co = sum(x["comprado"] for x in lista_subs)
            f_re = sum(x["recibido"] for x in lista_subs)
            fila_f = _fila(nom_f, f_cv, f_co, f_re)
            fila_f["subfamilias"] = lista_subs
            lista_fams.append(fila_f)
            p_cv += f_cv; p_co += f_co; p_re += f_re

        lista_fams.sort(key=lambda x: -x["brecha_recibido"])
        fila_p = _fila(nombres_rut[rut], p_cv, p_co, p_re, rut=rut)
        fila_p["familias"] = lista_fams

        if p_co > 0 or p_re > 0:
            proveedores.append(fila_p)
            tot_cv += fila_p["costo_venta"]
            tot_co += fila_p["comprado"]
            tot_re += fila_p["recibido"]
        else:
            sin_compras.append(fila_p)
            fuera_cv += fila_p["costo_venta"]

    # Mayor sobreabastecimiento primero: por brecha en pesos y no por
    # ratio, porque un ratio alto sobre un proveedor chico no mueve la
    # aguja y taparia a los que si importan.
    proveedores.sort(key=lambda p: -p["brecha_recibido"])
    sin_compras.sort(key=lambda p: -p["costo_venta"])

    meses = []
    acumulado = 0.0
    for r in filas_mes:
        cv, co, re = float(r["costo_venta"]), float(r["comprado"]), float(r["recibido"])
        # Brecha acumulada: es LA cifra que explica el año. El dato suelto
        # de cada mes no dice nada, y el total del año tampoco -- recien
        # la curva acumulada muestra que el stock se bajo fuerte en
        # enero-febrero y se viene reponiendo desde marzo.
        acumulado += re - cv
        meses.append({
            "mes":            r["mes"],
            "mes_nombre":     MESES.get(r["mes"], str(r["mes"])),
            "costo_venta":    round(cv, 0),
            "comprado":       round(co, 0),
            "recibido":       round(re, 0),
            "ratio":          round(re / cv, 3) if cv > 0 else None,
            "ratio_comprado": round(co / cv, 3) if cv > 0 else None,
            "brecha":         round(re - cv, 0),
            "acumulado":      round(acumulado, 0),
        })

    total = _fila("Total", tot_cv, tot_co, tot_re)
    # Comprado que todavia no llega a bodega. Importa porque el ratio se
    # mide contra lo RECIBIDO: esta plata ya esta comprometida y va a
    # empujar el inventario hacia arriba sin que medie una compra nueva.
    total["en_camino"] = round(tot_co - tot_re, 0)
    total["ratio_comprado"] = round(tot_co / tot_cv, 3) if tot_cv > 0 else None

    return {
        "total":             total,
        "proveedores":       proveedores,
        "sin_compras":       sin_compras,
        "fuera_costo_venta": round(fuera_cv, 0),
        "fuera_pct":         round(fuera_cv / (tot_cv + fuera_cv) * 100, 1) if (tot_cv + fuera_cv) else 0.0,
        "meses":             meses,
        "ano":               2026,
        "fecha_corte":       fecha_corte.strftime("%d-%m-%Y") if fecha_corte else None,
    }


def get_inventario_por_sucursal_pg():
    """Evolucion del valor del inventario por sucursal, desde
    nivel_servicio_historico (lo llena el cron diario).

    Va al lado del KPI de abastecimiento porque es la comprobacion
    INDEPENDIENTE del mismo fenomeno: el ratio mide el flujo (compro mas
    rapido de lo que vendo?) y esto mide el stock (donde se esta
    acumulando?). Cuando las dos apuntan al mismo lado, la conclusion es
    solida.

    Tambien es lo unico que separa la expansion del sobreabastecimiento.
    Chicureo abrio en 2025 y Maipu en marzo de 2026: su inventario es
    mercaderia que antes no existia, no es haber comprado de mas. El
    ratio no puede distinguirlos -- Maipu se abastecio por traspasos
    desde San Isidro y casi no registra compras propias -- pero mirando
    el stock por sucursal se ve solo.

    OJO con dos cosas de nivel_servicio_historico:
      - guarda una fila 'TOTAL' ADEMAS de una por sucursal; sumarlas
        todas duplica el valor.
      - el cron partio el 2026-08-27, asi que no hay historia anterior.
    """
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT min(fecha) AS desde, max(fecha) AS hasta FROM nivel_servicio_historico"""
            )
            r = cur.fetchone()
            desde, hasta = r["desde"], r["hasta"]
            if desde is None:
                return {"sucursales": [], "total": None, "desde": None, "hasta": None, "dias": 0}

            cur.execute(
                """SELECT sucursal,
                          max(valor_inventario) FILTER (WHERE fecha = %(desde)s) AS ini,
                          max(valor_inventario) FILTER (WHERE fecha = %(hasta)s) AS fin
                     FROM nivel_servicio_historico
                    WHERE fecha IN (%(desde)s, %(hasta)s)
                    GROUP BY sucursal""",
                {"desde": desde, "hasta": hasta},
            )
            filas = cur.fetchall()

            # Primera venta de cada sucursal: asi la pantalla puede decir
            # "abrio en marzo de 2026" en vez de dejar al lector adivinar
            # por que una sucursal aparece con inventario nuevo.
            cur.execute(
                """SELECT sucursal_logica AS sigla, min(fecha_conta) AS primera
                     FROM v_ventas WHERE sucursal_logica IS NOT NULL
                    GROUP BY sucursal_logica"""
            )
            apertura = {f["sigla"]: f["primera"] for f in cur.fetchall()}

    sucursales, total = [], None
    for f in filas:
        if f["ini"] is None or f["fin"] is None:
            continue
        ini, fin = float(f["ini"]), float(f["fin"])
        fila = {
            "sucursal":  f["sucursal"],
            "sigla":     SIGLA_SUCURSAL.get(f["sucursal"]),
            "inicial":   round(ini, 0),
            "final":     round(fin, 0),
            "variacion": round(fin - ini, 0),
            "var_pct":   round((fin - ini) / ini * 100, 1) if ini else None,
            "primera_venta": None,
        }
        # "SI" es una pseudo-sucursal del lado comercial (San Isidro =
        # SE + CMD, dos canales sobre la misma bodega), asi que no existe
        # como tal en v_ventas y hay que expandirla o la fecha sale vacia.
        siglas = {"SI": ["SE", "CMD"]}.get(fila["sigla"], [fila["sigla"]])
        fechas = [apertura[x] for x in siglas if x in apertura]
        if fechas:
            fila["primera_venta"] = min(fechas).strftime("%d-%m-%Y")
        if f["sucursal"] == "TOTAL":
            total = fila
        else:
            sucursales.append(fila)

    sucursales.sort(key=lambda x: -x["final"])

    return {
        "sucursales": sucursales,
        "total":      total,
        "desde":      desde.strftime("%d-%m-%Y"),
        "hasta":      hasta.strftime("%d-%m-%Y"),
        "dias":       (hasta - desde).days,
    }


def get_lead_time_combinado_pg():
    """Lead time real (dias entre fecha_creacion de la OC y su primera
    recepcion) por proveedor -- año actual vs año anterior. Mismo
    calculo que data_loader_adquisiciones.get_lead_time_por_proveedor(),
    sobre las tablas compras/recepciones en vez del Excel."""
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """WITH primera_recepcion AS (
                       SELECT n_oc, min(fecha_recepcion) AS fecha_recepcion
                       FROM recepciones
                       GROUP BY n_oc
                   ),
                   oc AS (
                       SELECT DISTINCT ON (c.ano, c.n_orden_compra)
                           c.ano, c.n_orden_compra, c.nombre_proveedor, c.fecha_creacion,
                           pr.fecha_recepcion
                       FROM compras c
                       LEFT JOIN primera_recepcion pr ON pr.n_oc = c.n_orden_compra
                       WHERE c.ano IN (2025, 2026)
                       ORDER BY c.ano, c.n_orden_compra
                   ),
                   oc_lt AS (
                       SELECT *,
                           CASE WHEN fecha_recepcion IS NOT NULL AND fecha_recepcion >= fecha_creacion
                                THEN (fecha_recepcion - fecha_creacion)
                                ELSE NULL END AS lead_time_dias
                       FROM oc
                   )
                   SELECT ano, nombre_proveedor,
                       count(*) AS n_oc_total,
                       count(lead_time_dias) AS n_oc_recibidas,
                       avg(lead_time_dias) AS lead_promedio,
                       min(lead_time_dias) AS lead_min,
                       max(lead_time_dias) AS lead_max
                   FROM oc_lt
                   WHERE nombre_proveedor IS NOT NULL
                   GROUP BY ano, nombre_proveedor""",
            )
            grupos = cur.fetchall()

            cur.execute(
                """WITH primera_recepcion AS (
                       SELECT n_oc, min(fecha_recepcion) AS fecha_recepcion
                       FROM recepciones
                       GROUP BY n_oc
                   ),
                   oc AS (
                       SELECT DISTINCT ON (c.ano, c.n_orden_compra)
                           c.ano, c.fecha_creacion, pr.fecha_recepcion
                       FROM compras c
                       LEFT JOIN primera_recepcion pr ON pr.n_oc = c.n_orden_compra
                       WHERE c.ano IN (2025, 2026)
                       ORDER BY c.ano, c.n_orden_compra
                   )
                   SELECT ano, avg(fecha_recepcion - fecha_creacion) AS lead_promedio
                   FROM oc
                   WHERE fecha_recepcion IS NOT NULL AND fecha_recepcion >= fecha_creacion
                   GROUP BY ano""",
            )
            empresa = {r["ano"]: r["lead_promedio"] for r in cur.fetchall()}

    g26 = {r["nombre_proveedor"]: r for r in grupos if r["ano"] == 2026}
    g25 = {r["nombre_proveedor"]: r for r in grupos if r["ano"] == 2025}

    items = []
    for nombre, fila in g26.items():
        n_oc_total = int(fila["n_oc_total"])
        n_oc_recibidas = int(fila["n_oc_recibidas"])
        fila25 = g25.get(nombre)
        items.append({
            "nombre":            nombre,
            "n_oc_total":        n_oc_total,
            "n_oc_recibidas":    n_oc_recibidas,
            "n_oc_pendientes":   n_oc_total - n_oc_recibidas,
            "lead_time_actual":  round(float(fila["lead_promedio"]), 1) if fila["lead_promedio"] is not None else None,
            "lead_time_min":     int(fila["lead_min"]) if fila["lead_min"] is not None else None,
            "lead_time_max":     int(fila["lead_max"]) if fila["lead_max"] is not None else None,
            "lead_time_anterior": round(float(fila25["lead_promedio"]), 1) if fila25 and fila25["lead_promedio"] is not None else None,
        })

    items.sort(key=lambda x: (x["lead_time_actual"] is None, -(x["lead_time_actual"] or 0), -x["n_oc_total"]))

    return {
        "items": items,
        "lead_time_empresa_actual":   round(float(empresa[2026]), 1) if empresa.get(2026) is not None else None,
        "lead_time_empresa_anterior": round(float(empresa[2025]), 1) if empresa.get(2025) is not None else None,
        "ano_actual": 2026,
        "ano_anterior": 2025,
    }


UMBRAL_ON_TIME_DIAS_HABILES = 5  # mismo estandar Nacional que Plan de Compra


def get_cumplimiento_combinado_pg():
    """OTIF (On Time In Full) por proveedor, linea por linea (OC +
    codigo) del año actual. Mismo calculo que
    data_loader_adquisiciones.get_cumplimiento_por_proveedor(); el
    conteo de dias habiles se hace en Python con np.busday_count
    (igual que el original) sobre las fechas leidas de Postgres."""
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT n_orden_compra, codigo,
                       min(nombre_proveedor) AS proveedor,
                       min(fecha_creacion) AS fecha_creacion,
                       sum(cantidad_comprada) AS cantidad_comprada
                   FROM compras
                   WHERE ano = 2026
                   GROUP BY n_orden_compra, codigo""",
            )
            lineas_compra = cur.fetchall()

            cur.execute(
                """SELECT n_oc AS n_orden_compra, codigo,
                       sum(cantidad) AS cantidad_recibida,
                       min(fecha_recepcion) AS fecha_primera_recepcion
                   FROM recepciones
                   WHERE ano IN (2025, 2026)
                   GROUP BY n_oc, codigo""",
            )
            recepcion_por_linea = {(r["n_orden_compra"], r["codigo"]): r for r in cur.fetchall()}

    por_proveedor = {}
    tot_lineas = tot_in_full = tot_on_time = tot_otif = 0

    for c in lineas_compra:
        clave = (c["n_orden_compra"], c["codigo"])
        r = recepcion_por_linea.get(clave)
        cantidad_recibida = float(r["cantidad_recibida"]) if r else 0.0
        cantidad_comprada = float(c["cantidad_comprada"] or 0)
        in_full = math.isclose(cantidad_recibida, cantidad_comprada)

        on_time = False
        if r and r["fecha_primera_recepcion"] is not None:
            dias_habiles = int(np.busday_count(c["fecha_creacion"], r["fecha_primera_recepcion"]))
            on_time = dias_habiles <= UMBRAL_ON_TIME_DIAS_HABILES
        otif = in_full and on_time

        proveedor = c["proveedor"] or "(Sin proveedor)"
        acc = por_proveedor.setdefault(proveedor, {"n_lineas": 0, "n_in_full": 0, "n_on_time": 0, "n_otif": 0})
        acc["n_lineas"] += 1
        acc["n_in_full"] += int(in_full)
        acc["n_on_time"] += int(on_time)
        acc["n_otif"] += int(otif)

        tot_lineas += 1
        tot_in_full += int(in_full)
        tot_on_time += int(on_time)
        tot_otif += int(otif)

    items = []
    for proveedor, acc in por_proveedor.items():
        n = acc["n_lineas"]
        items.append({
            "nombre":       proveedor,
            "n_lineas":     n,
            "pct_in_full":  round(acc["n_in_full"] / n * 100, 1),
            "pct_on_time":  round(acc["n_on_time"] / n * 100, 1),
            "pct_otif":     round(acc["n_otif"] / n * 100, 1),
        })

    items.sort(key=lambda x: (x["pct_otif"], -x["n_lineas"]))

    resumen = {
        "n_lineas":    tot_lineas,
        "pct_in_full": round(tot_in_full / tot_lineas * 100, 1) if tot_lineas > 0 else 0.0,
        "pct_on_time": round(tot_on_time / tot_lineas * 100, 1) if tot_lineas > 0 else 0.0,
        "pct_otif":    round(tot_otif / tot_lineas * 100, 1) if tot_lineas > 0 else 0.0,
    }

    return {
        "items": items,
        "resumen": resumen,
        "umbral_dias_habiles": UMBRAL_ON_TIME_DIAS_HABILES,
        "ano_actual": 2026,
    }
