"""
Segunda Linea: productos AAA o M05 (los 2 niveles mas criticos de
CLAS_CSD) que NO estan en la lista curada de Obligatorios
(productos_obligatorios) -- items que no son de primera linea, pero
que si el presupuesto alcanza conviene abastecer. A diferencia de
Obligatorios, aca no hay un par Importado/Nacional curado a mano: cada
codigo se trata solo, sin combinar con un equivalente.

No requiere ningun archivo Excel nuevo -- el universo se calcula 100%
desde datos que ya estan en Postgres (clas_csd de productos, exclusion
de productos_obligatorios, exclusion de codigos que empiezan con "6").
Por eso no hay version "Excel" de este modulo ni script de validacion:
es una funcionalidad nueva, no un port de algo que ya existia.

Reusa la mecanica ya probada de data_loader_obligatorios_pg.py
(_stock_combinado_pg, _venta_combinada_pg, _cargar_stock_pg) pasando
siempre cod_equiv=None -- esas funciones ya manejan ese caso.
"""
import math

import db
import data_loader_inventario as dli
import data_loader_obligatorios as do
import data_loader_obligatorios_pg as dopg
import data_loader_exclusion_compra as dec

# Umbrales propios, mas laxos que los de Obligatorios (15/25 dias) --
# estos productos no son "no puede faltar", son "conviene abastecer si
# alcanza el presupuesto", asi que un quiebre aca es menos urgente.
UMBRAL_QUIEBRE_DIAS = 7     # rojo
UMBRAL_ALERTA_DIAS = 15     # amarillo (entre este y el rojo)

# Mismo criterio que Obligatorios: reparte desde San Isidro hasta que
# la sucursal deje de estar en rojo/amarillo.
OBJETIVO_DIAS_DISTRIBUCION = UMBRAL_ALERTA_DIAS

# Tope de 1 mes de objetivo (vs 2 meses en Obligatorios) -- pedido
# explicito del usuario: no interesa cargar mas de 1 mes de stock en
# productos de segunda linea.
MESES_OBJETIVO_COMPRA = 1.0
NIVELES_COMPARACION_MESES = [0.5, 1.0]


def _clasificar_nivel(alcance, stock_combinado):
    """Igual que data_loader_obligatorios._clasificar_nivel pero con
    los umbrales propios de Segunda Linea (7/15 dias en vez de 15/25).
    No se puede reusar esa funcion directamente porque lee los
    umbrales de Obligatorios desde constantes de modulo, no desde
    parametros."""
    if alcance is not None:
        if alcance < UMBRAL_QUIEBRE_DIAS:
            return "rojo"
        if alcance < UMBRAL_ALERTA_DIAS:
            return "amarillo"
        return "verde"
    return "rojo" if stock_combinado <= 0 else "verde"


def _cargar_candidatos(cur, familia=None):
    """Universo de Segunda Linea: AAA/M05, no obligatorio ni
    equivalente de uno, codigo que no empieza con "6". Todo lo que
    hace falta (embalaje, pedido_total, cup, id_procedencia) sale de
    esta unica consulta a productos -- a diferencia de Obligatorios,
    aca no hace falta una segunda tabla/consulta."""
    sql = f"""
        select codigo, familia, subfamilia, grupo, descripcion,
               embalaje, pedido_total, cup, id_procedencia
        from productos
        where clas_csd in ('AAA', 'M05')
          and codigo not in (
              select codigo_obligatorio from productos_obligatorios
              union
              select codigo_equivalente from productos_obligatorios where codigo_equivalente is not null
          )
          and {dec._condicion_prefijos_excluidos(dec.PREFIJOS_FUERA_SEGUNDA_LINEA)}
    """
    params = {}
    if familia:
        sql += " and familia = %(familia)s"
        params["familia"] = familia
    sql += " order by familia, subfamilia, descripcion"
    cur.execute(sql, params)
    return cur.fetchall()


def _procedencia(id_procedencia):
    return "Importado" if id_procedencia in dli.IDS_IMPORTADO else "Nacional"


def get_familias_segunda_linea():
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                select distinct familia from productos
                where clas_csd in ('AAA', 'M05')
                  and codigo not in (
                      select codigo_obligatorio from productos_obligatorios
                      union
                      select codigo_equivalente from productos_obligatorios where codigo_equivalente is not null
                  )
                  and {dec._condicion_prefijos_excluidos(dec.PREFIJOS_FUERA_SEGUNDA_LINEA)}
                order by familia
            """)
            return [r["familia"] for r in cur.fetchall()]


def get_alertas_quiebre_segunda_linea(familia=None):
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            candidatos = _cargar_candidatos(cur, familia)
            codigos = {c["codigo"] for c in candidatos}
            bodegas = [n for n, _, _, _ in do.SUCURSALES_CRITICAS] + ["Todas"]
            datos = dopg._cargar_stock_pg(cur, codigos, bodegas)

    productos = []
    for c in candidatos:
        cod = c["codigo"]

        sucursales = {}
        tiene_quiebre = False
        tiene_alerta_temprana = False
        tiene_quiebre_cd = False
        for nombre, _, _, es_cd in do.SUCURSALES_CRITICAS:
            stock_combinado = dopg._stock_combinado_pg(datos, cod, None, nombre)

            if es_cd:
                venta_combinada = dopg._venta_combinada_pg(datos, cod, None, "Todas")
                venta_local_si = dopg._venta_combinada_pg(datos, cod, None, "San Isidro")
                venta_combinada = max(0.0, venta_combinada - venta_local_si)
                tiene_venta = True
            else:
                tiene_venta = nombre in dli.VENTA_MENSUAL_COL
                venta_combinada = dopg._venta_combinada_pg(datos, cod, None, nombre) if tiene_venta else 0.0

            alcance = round(stock_combinado / venta_combinada * 30, 1) if (tiene_venta and venta_combinada > 0) else None
            nivel = _clasificar_nivel(alcance, stock_combinado)

            sucursales[nombre] = {
                "stock":   round(stock_combinado, 0),
                "alcance": alcance,
                "nivel":   nivel,
                "quiebre": nivel == "rojo",
                "es_cd":   es_cd,
            }
            if nivel == "rojo":
                tiene_quiebre = True
                if es_cd:
                    tiene_quiebre_cd = True
            elif nivel == "amarillo":
                tiene_alerta_temprana = True

        stock_total = sum(s["stock"] for s in sucursales.values())
        venta_total_combinada = dopg._venta_combinada_pg(datos, cod, None, "Todas")
        alcance_total = round(stock_total / venta_total_combinada * 30, 1) if venta_total_combinada > 0 else None
        nivel_total = _clasificar_nivel(alcance_total, stock_total)
        total = {"stock": round(stock_total, 0), "alcance": alcance_total, "nivel": nivel_total, "quiebre": nivel_total == "rojo"}

        productos.append({
            "familia":              c["familia"],
            "subfamilia":           c["subfamilia"],
            "grupo":                c["grupo"],
            "descripcion":          c["descripcion"],
            "codigo":               int(cod),
            "procedencia":          _procedencia(c["id_procedencia"]),
            "sucursales":           sucursales,
            "total":                total,
            "tiene_quiebre":        tiene_quiebre,
            "tiene_alerta_temprana": tiene_alerta_temprana,
            "tiene_quiebre_cd":     tiene_quiebre_cd,
        })

    return {
        "sucursales": [{"nombre": n, "es_cd": cd} for n, _, _, cd in do.SUCURSALES_CRITICAS],
        "productos":  productos,
        "resumen": {
            "total_candidatos":    len(productos),
            "con_quiebre":         sum(1 for p in productos if p["tiene_quiebre"]),
            "con_alerta_temprana": sum(1 for p in productos if p["tiene_alerta_temprana"] and not p["tiene_quiebre"]),
            "con_quiebre_cd":      sum(1 for p in productos if p["tiene_quiebre_cd"]),
        },
    }


def exportar_alertas_excel(familia=None):
    # Misma forma que el resultado de Obligatorios (sucursales/productos
    # con familia/subfamilia/grupo/descripcion/codigo/procedencia/
    # sucursales/total) -- se puede reusar el mismo formateador de Excel.
    return do._construir_excel_alertas(get_alertas_quiebre_segunda_linea(familia))


def get_distribucion_segunda_linea(familia=None):
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            candidatos = _cargar_candidatos(cur, familia)
            codigos = {c["codigo"] for c in candidatos}
            bodegas = [n for n, _, _, _ in do.SUCURSALES_CRITICAS] + ["Todas"]
            datos = dopg._cargar_stock_pg(cur, codigos, bodegas)

    sucursales_destino = [n for n, _, _, es_cd in do.SUCURSALES_CRITICAS if not es_cd]

    productos = []
    for c in candidatos:
        cod = c["codigo"]

        stock_si = dopg._stock_combinado_pg(datos, cod, None, "San Isidro")
        venta_local_si = dopg._venta_combinada_pg(datos, cod, None, "San Isidro")
        reserva_si = (venta_local_si / 30) * OBJETIVO_DIAS_DISTRIBUCION
        disponible = max(0.0, stock_si - reserva_si)

        embalaje = int(c["embalaje"]) if c["embalaje"] else 1

        candidatas = []
        for nombre in sucursales_destino:
            stock_suc = dopg._stock_combinado_pg(datos, cod, None, nombre)

            if nombre == "Maipú":
                objetivo_unidades = 500.0 if c["familia"] == "CONDUCTORES" else float(embalaje)
                necesidad = max(0.0, objetivo_unidades - stock_suc)
                alcance_actual = None
                orden_urgencia = (
                    (stock_suc / objetivo_unidades) * OBJETIVO_DIAS_DISTRIBUCION
                    if objetivo_unidades > 0 else OBJETIVO_DIAS_DISTRIBUCION
                )
            else:
                tiene_venta = nombre in dli.VENTA_MENSUAL_COL
                venta_suc = dopg._venta_combinada_pg(datos, cod, None, nombre) if tiene_venta else 0.0
                if venta_suc > 0:
                    alcance_actual = round(stock_suc / venta_suc * 30, 1)
                    objetivo_unidades = OBJETIVO_DIAS_DISTRIBUCION * (venta_suc / 30)
                    necesidad = max(0.0, objetivo_unidades - stock_suc)
                    orden_urgencia = alcance_actual
                else:
                    alcance_actual = None
                    necesidad = 0.0
                    orden_urgencia = None

            candidatas.append({
                "nombre":         nombre,
                "stock_actual":   round(stock_suc, 0),
                "alcance_actual": alcance_actual,
                "necesidad":      necesidad,
                "orden_urgencia": orden_urgencia,
            })

        candidatas.sort(key=lambda x: (
            x["orden_urgencia"] is None,
            x["orden_urgencia"] if x["orden_urgencia"] is not None else 0,
        ))

        disponible_restante = disponible
        envios = {}
        cubiertos = {}
        for cd in candidatas:
            envio_crudo = min(cd["necesidad"], disponible_restante)
            envio = math.floor(envio_crudo / embalaje) * embalaje
            envios[cd["nombre"]] = float(envio)
            cubiertos[cd["nombre"]] = (cd["necesidad"] - envio_crudo) < 1
            disponible_restante -= envio

        tiene_envio = any(v > 0 for v in envios.values())
        tiene_necesidad_sin_cubrir = any(not cubiertos[cd["nombre"]] for cd in candidatas)
        if not tiene_envio and not tiene_necesidad_sin_cubrir:
            continue

        productos.append({
            "familia":               c["familia"],
            "subfamilia":            c["subfamilia"],
            "grupo":                 c["grupo"],
            "descripcion":           c["descripcion"],
            "codigo":                int(cod),
            "embalaje":              embalaje,
            "stock_san_isidro":      round(stock_si, 0),
            "reserva_san_isidro":    round(reserva_si, 0),
            "disponible_san_isidro": round(disponible, 0),
            "sobrante_san_isidro":   round(disponible_restante, 0),
            "envios": {
                cd["nombre"]: {
                    "cantidad_a_enviar": envios[cd["nombre"]],
                    "stock_actual":      cd["stock_actual"],
                    "alcance_actual":    cd["alcance_actual"],
                    "necesidad":         round(cd["necesidad"], 0),
                    "cubierto":          cubiertos[cd["nombre"]],
                }
                for cd in candidatas
            },
        })

    productos.sort(key=lambda p: -sum(e["cantidad_a_enviar"] for e in p["envios"].values()))

    total_a_enviar = sum(sum(e["cantidad_a_enviar"] for e in p["envios"].values()) for p in productos)
    n_con_deficit = sum(1 for p in productos if any(not e["cubierto"] for e in p["envios"].values()))

    return {
        "productos": productos,
        "sucursales": sucursales_destino,
        "resumen": {
            "total_productos":         len(productos),
            "total_unidades_a_enviar": round(total_a_enviar, 0),
            "productos_con_deficit":   n_con_deficit,
        },
        "objetivo_dias": OBJETIVO_DIAS_DISTRIBUCION,
    }


def _construir_excel_distribucion(resultado):
    """Version simplificada de do._construir_excel_distribucion --
    aca no hay codigo_nacional/codigo_otro (un solo codigo por
    producto, sin equivalente), asi que no se puede reusar tal cual."""
    import pandas as pd
    import io

    nombres_sucursales = resultado["sucursales"]
    filas = []
    for p in resultado["productos"]:
        fila = {
            "Familia":          p["familia"],
            "Subfamilia":       p["subfamilia"],
            "Grupo":            p["grupo"] or "",
            "Descripcion":      p["descripcion"],
            "Codigo":           p["codigo"],
            "Embalaje":         p["embalaje"],
            "Stock San Isidro": p["stock_san_isidro"],
            "Disponible SI":    p["disponible_san_isidro"],
        }
        for nombre in nombres_sucursales:
            info = p["envios"][nombre]
            fila[f"{nombre} - Enviar"] = info["cantidad_a_enviar"]
            fila[f"{nombre} - Necesidad"] = info["necesidad"]
            fila[f"{nombre} - Cubierto"] = "Si" if info["cubierto"] else "No"
        filas.append(fila)

    columnas = ["Familia", "Subfamilia", "Grupo", "Descripcion", "Codigo", "Embalaje", "Stock San Isidro", "Disponible SI"]
    for nombre in nombres_sucursales:
        columnas += [f"{nombre} - Enviar", f"{nombre} - Necesidad", f"{nombre} - Cubierto"]

    df = pd.DataFrame(filas, columns=columnas)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Distribucion 2L")
    buffer.seek(0)
    return buffer


def exportar_distribucion_excel(familia=None):
    return _construir_excel_distribucion(get_distribucion_segunda_linea(familia))


def get_plan_compra_segunda_linea(familia=None, meses_objetivo_default=None):
    meses_objetivo = meses_objetivo_default if meses_objetivo_default is not None else MESES_OBJETIVO_COMPRA

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            candidatos = _cargar_candidatos(cur, familia)
            codigos = {c["codigo"] for c in candidatos}
            bodegas = [n for n, _, _, _ in do.SUCURSALES_CRITICAS] + ["Todas"]
            datos = dopg._cargar_stock_pg(cur, codigos, bodegas)
            lead_time_real = dopg._lead_time_real_por_producto_pg(cur, codigos)

    codigos_excluidos = dec.codigos_excluidos_compra()

    productos = []
    for c in candidatos:
        cod = c["codigo"]

        venta_consolidada = dopg._venta_combinada_pg(datos, cod, None, "Todas")
        stock_total = sum(
            dopg._stock_combinado_pg(datos, cod, None, nombre)
            for nombre, _, _, _ in do.SUCURSALES_CRITICAS
        )

        lt = lead_time_real.get(cod) or {"dias_habiles": do.LEAD_TIME_DIAS_NACIONAL, "proveedor": None, "es_real": False}
        colchon_lead_time = (venta_consolidada / do.DIAS_HABILES_MES) * lt["dias_habiles"]
        pedido_total = float(c["pedido_total"]) if c["pedido_total"] is not None else 0.0

        objetivo_empresa = (meses_objetivo * venta_consolidada) + colchon_lead_time
        necesario = max(0.0, objetivo_empresa - stock_total - pedido_total)

        embalaje = int(c["embalaje"]) if c["embalaje"] else 1
        cantidad_a_comprar = math.ceil(necesario / embalaje) * embalaje if necesario > 0 else 0

        # Excluido de compra: (a) marcado a mano en
        # /gestionar_productos_compra, o (b) codigo Importado que
        # empieza con 3 o 7 -- regla de negocio permanente (a
        # diferencia de (a), no es una lista curada): esos productos
        # no los compra el usuario, no tiene injerencia sobre ellos.
        # Solo afecta Plan de Compra, no Alertas/Distribucion (a
        # diferencia del digito 6, que se excluye de Segunda Linea
        # completa -- ver _cargar_candidatos).
        cod_str = str(cod)
        excluido_compra = cod in codigos_excluidos or cod_str.startswith(dec.PREFIJOS_IMPORTADO_SIN_INJERENCIA)
        if excluido_compra:
            cantidad_a_comprar = 0

        productos.append({
            "familia":            c["familia"],
            "subfamilia":         c["subfamilia"],
            "grupo":              c["grupo"],
            "descripcion":        c["descripcion"],
            "codigo_a_comprar":   int(cod),
            "embalaje":           embalaje,
            "cantidad_a_comprar": cantidad_a_comprar,
            "sin_opcion_nacional": False,  # siempre False: no hay eleccion Nacional/Importado en Segunda Linea
            "excluido_compra":    excluido_compra,
            "lead_time_dias_habiles": round(lt["dias_habiles"], 1),
            "lead_time_proveedor":    lt["proveedor"],
            "lead_time_es_real":      lt["es_real"],
        })

    productos.sort(key=lambda p: -(p["cantidad_a_comprar"] or 0))

    return {
        "productos": productos,
        "resumen": {
            "total_candidatos":     len(productos),
            "excluidos_compra":     sum(1 for p in productos if p["excluido_compra"]),
            "con_necesidad_compra": sum(1 for p in productos if (p["cantidad_a_comprar"] or 0) > 0),
        },
    }


def get_resumen_valor_compra_segunda_linea(familia=None):
    """Valor total ($) de la compra sugerida a cada nivel de
    NIVELES_COMPARACION_MESES -- mismo criterio que
    data_loader_obligatorios.get_resumen_valor_compra."""
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            candidatos = _cargar_candidatos(cur, familia)
    precio_por_codigo = {c["codigo"]: float(c["cup"] or 0) for c in candidatos}

    niveles = []
    for meses in NIVELES_COMPARACION_MESES:
        resultado = get_plan_compra_segunda_linea(familia, meses)
        valor_total = sum(
            p["cantidad_a_comprar"] * precio_por_codigo.get(p["codigo_a_comprar"], 0)
            for p in resultado["productos"]
            if (p["cantidad_a_comprar"] or 0) > 0
        )
        niveles.append({"meses": meses, "valor": round(valor_total, 0)})
    return {"niveles": niveles}


def exportar_plan_compras_excel(familia=None, meses_objetivo_default=None):
    return do._construir_excel_plan_compras(get_plan_compra_segunda_linea(familia, meses_objetivo_default))
