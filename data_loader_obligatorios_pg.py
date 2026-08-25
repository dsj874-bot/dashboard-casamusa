"""
Version Postgres de data_loader_obligatorios.py -- Alertas de Quiebre
Critico y Distribucion desde San Isidro (Plan de Compra/Reposicion
queda para despues, mismo orden acordado que el resto de Inventario).

Misma logica exacta que el original: en vez de leer todo
Inventario.xlsx a un DataFrame gigante (dli._leer_inventario()), aca
se consulta Postgres solo por los codigos que realmente hacen falta
(los obligatorios + sus equivalentes -- unos pocos cientos de los
~19.000 codigos que existen en total).
"""
import math

import db
import data_loader_inventario as dli
import data_loader_obligatorios as do
import data_loader_exclusion_compra as dec


def get_familias_obligatorios_pg():
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("select distinct familia from productos_obligatorios order by familia")
            return [r["familia"] for r in cur.fetchall()]


def _cargar_obligatorios_pg(cur, familia=None):
    sql = """
        select codigo_obligatorio, familia, subfamilia, grupo, descripcion,
               procedencia_obligatoria, codigo_equivalente, meses_objetivo
        from productos_obligatorios
    """
    params = {}
    if familia:
        sql += " where familia = %(familia)s"
        params["familia"] = familia
    sql += " order by familia, subfamilia, descripcion"
    cur.execute(sql, params)
    return cur.fetchall()


def _cargar_stock_pg(cur, codigos, bodegas):
    if not codigos:
        return {}
    cur.execute(
        "select codigo, bodega, stock, transito, venta_mensual "
        "from inventario_stock where codigo = any(%(codigos)s) and bodega = any(%(bodegas)s)",
        {"codigos": list(codigos), "bodegas": list(bodegas)},
    )
    datos = {}
    for r in cur.fetchall():
        datos[(r["codigo"], r["bodega"])] = {
            "stock":         float(r["stock"]) if r["stock"] is not None else 0.0,
            "transito":      float(r["transito"]) if r["transito"] is not None else 0.0,
            "venta_mensual": float(r["venta_mensual"]) if r["venta_mensual"] is not None else None,
        }
    return datos


def _valor(datos, codigo, bodega, campo, default=0.0):
    """Equivalente a data_loader_obligatorios._valor_col, pero contra
    el dict (codigo, bodega) -> fila en vez de una fila de DataFrame."""
    if codigo is None:
        return default
    fila = datos.get((codigo, bodega))
    if fila is None:
        return default
    v = fila.get(campo)
    return v if v is not None else default


def _stock_combinado_pg(datos, cod_obl, cod_equiv, bodega):
    return (
        _valor(datos, cod_obl, bodega, "stock") + _valor(datos, cod_equiv, bodega, "stock")
        + _valor(datos, cod_obl, bodega, "transito") + _valor(datos, cod_equiv, bodega, "transito")
    )


def _venta_combinada_pg(datos, cod_obl, cod_equiv, bodega):
    v_obl = _valor(datos, cod_obl, bodega, "venta_mensual")
    if cod_equiv is None:
        return v_obl
    v_equiv = _valor(datos, cod_equiv, bodega, "venta_mensual")
    return (v_obl + v_equiv) / 2


def get_alertas_quiebre_critico_pg(familia=None):
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            obligatorios = _cargar_obligatorios_pg(cur, familia)

            codigos_necesarios = set()
            for fila in obligatorios:
                codigos_necesarios.add(fila["codigo_obligatorio"])
                if fila["codigo_equivalente"] is not None:
                    codigos_necesarios.add(fila["codigo_equivalente"])

            bodegas = [n for n, _, _, _ in do.SUCURSALES_CRITICAS] + ["Todas"]
            datos = _cargar_stock_pg(cur, codigos_necesarios, bodegas)

    productos = []
    for fila in obligatorios:
        cod_obl = fila["codigo_obligatorio"]
        cod_equiv = fila["codigo_equivalente"]

        sucursales = {}
        tiene_quiebre = False
        tiene_alerta_temprana = False
        tiene_quiebre_cd = False
        for nombre, _, _, es_cd in do.SUCURSALES_CRITICAS:
            stock_combinado = _stock_combinado_pg(datos, cod_obl, cod_equiv, nombre)

            # San Isidro (es_cd): usa la venta CONSOLIDADA (bodega "Todas")
            # menos su propia venta local -- misma aproximacion que el
            # original (ver docstring de get_alertas_quiebre_critico en
            # data_loader_obligatorios.py). Las demas: su propia columna
            # de venta mensual, si existe (Maipu no tiene).
            if es_cd:
                venta_combinada = _venta_combinada_pg(datos, cod_obl, cod_equiv, "Todas")
                venta_local_si = _venta_combinada_pg(datos, cod_obl, cod_equiv, "San Isidro")
                venta_combinada = max(0.0, venta_combinada - venta_local_si)
                tiene_venta = True
            else:
                tiene_venta = nombre in dli.VENTA_MENSUAL_COL
                venta_combinada = _venta_combinada_pg(datos, cod_obl, cod_equiv, nombre) if tiene_venta else 0.0

            alcance = round(stock_combinado / venta_combinada * 30, 1) if (tiene_venta and venta_combinada > 0) else None
            nivel = do._clasificar_nivel(alcance, stock_combinado)

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
        venta_total_combinada = _venta_combinada_pg(datos, cod_obl, cod_equiv, "Todas")
        alcance_total = round(stock_total / venta_total_combinada * 30, 1) if venta_total_combinada > 0 else None
        nivel_total = do._clasificar_nivel(alcance_total, stock_total)
        total = {"stock": round(stock_total, 0), "alcance": alcance_total, "nivel": nivel_total, "quiebre": nivel_total == "rojo"}

        productos.append({
            "familia":              fila["familia"],
            "subfamilia":           fila["subfamilia"],
            "grupo":                fila["grupo"],
            "descripcion":          fila["descripcion"],
            "codigo":               int(cod_obl),
            "procedencia":          fila["procedencia_obligatoria"],
            "codigo_equivalente":   int(cod_equiv) if cod_equiv is not None else None,
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
            "total_obligatorios":  len(productos),
            "con_quiebre":         sum(1 for p in productos if p["tiene_quiebre"]),
            "con_alerta_temprana": sum(1 for p in productos if p["tiene_alerta_temprana"] and not p["tiene_quiebre"]),
            "con_quiebre_cd":      sum(1 for p in productos if p["tiene_quiebre_cd"]),
        },
    }


def exportar_alertas_excel_pg(familia=None):
    return do._construir_excel_alertas(get_alertas_quiebre_critico_pg(familia))


def _cargar_embalaje_pg(cur, codigos):
    if not codigos:
        return {}
    cur.execute("select codigo, embalaje from productos where codigo = any(%(codigos)s)", {"codigos": list(codigos)})
    return {r["codigo"]: r["embalaje"] for r in cur.fetchall()}


def get_distribucion_desde_san_isidro_pg(familia=None):
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            obligatorios = _cargar_obligatorios_pg(cur, familia)

            codigos_necesarios = set()
            for fila in obligatorios:
                codigos_necesarios.add(fila["codigo_obligatorio"])
                if fila["codigo_equivalente"] is not None:
                    codigos_necesarios.add(fila["codigo_equivalente"])

            bodegas = [n for n, _, _, _ in do.SUCURSALES_CRITICAS] + ["Todas"]
            datos = _cargar_stock_pg(cur, codigos_necesarios, bodegas)
            embalajes = _cargar_embalaje_pg(cur, codigos_necesarios)

    sucursales_destino = [n for n, _, _, es_cd in do.SUCURSALES_CRITICAS if not es_cd]

    productos = []
    for fila in obligatorios:
        cod_obl = fila["codigo_obligatorio"]
        cod_equiv = fila["codigo_equivalente"]

        stock_si = _stock_combinado_pg(datos, cod_obl, cod_equiv, "San Isidro")
        venta_local_si = _venta_combinada_pg(datos, cod_obl, cod_equiv, "San Isidro")
        reserva_si = (venta_local_si / 30) * do.OBJETIVO_DIAS_DISTRIBUCION
        disponible = max(0.0, stock_si - reserva_si)

        cod_nacional = cod_equiv if cod_equiv else None
        cod_otro = cod_obl if cod_equiv else None
        if not cod_equiv:
            es_obligatorio_nacional = str(fila["procedencia_obligatoria"] or "").strip().lower() == "nacional"
            if es_obligatorio_nacional:
                cod_nacional = cod_obl
        stock_si_nacional = (
            _valor(datos, cod_nacional, "San Isidro", "stock") + _valor(datos, cod_nacional, "San Isidro", "transito")
        ) if cod_nacional else 0.0
        disponible_nacional_usable = min(stock_si_nacional, disponible)
        disponible_otro_usable = max(0.0, disponible - disponible_nacional_usable)

        embalaje_obl = embalajes.get(cod_obl)
        embalaje_equiv = embalajes.get(cod_equiv) if cod_equiv else None
        embalaje_crudo = embalaje_obl if embalaje_obl else embalaje_equiv
        embalaje = int(embalaje_crudo) if embalaje_crudo else 1

        candidatas = []
        for nombre in sucursales_destino:
            stock_suc = _stock_combinado_pg(datos, cod_obl, cod_equiv, nombre)

            if nombre == "Maipú":
                objetivo_unidades = 500.0 if fila["familia"] == "CONDUCTORES" else float(embalaje)
                necesidad = max(0.0, objetivo_unidades - stock_suc)
                alcance_actual = None
                orden_urgencia = (
                    (stock_suc / objetivo_unidades) * do.OBJETIVO_DIAS_DISTRIBUCION
                    if objetivo_unidades > 0 else do.OBJETIVO_DIAS_DISTRIBUCION
                )
            else:
                tiene_venta = nombre in dli.VENTA_MENSUAL_COL
                venta_suc = _venta_combinada_pg(datos, cod_obl, cod_equiv, nombre) if tiene_venta else 0.0
                if venta_suc > 0:
                    alcance_actual = round(stock_suc / venta_suc * 30, 1)
                    objetivo_unidades = do.OBJETIVO_DIAS_DISTRIBUCION * (venta_suc / 30)
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
        restante_nacional = disponible_nacional_usable
        restante_otro = disponible_otro_usable
        envios = {}
        cubiertos = {}
        desde_nacional = {}
        desde_otro = {}
        for c in candidatas:
            envio_crudo = min(c["necesidad"], disponible_restante)
            envio = math.floor(envio_crudo / embalaje) * embalaje
            envios[c["nombre"]] = float(envio)
            cubiertos[c["nombre"]] = (c["necesidad"] - envio_crudo) < 1
            disponible_restante -= envio

            de_nacional = min(envio, restante_nacional)
            de_otro = envio - de_nacional
            restante_nacional -= de_nacional
            restante_otro -= de_otro
            desde_nacional[c["nombre"]] = de_nacional
            desde_otro[c["nombre"]] = de_otro

        tiene_envio = any(v > 0 for v in envios.values())
        tiene_necesidad_sin_cubrir = any(not cubiertos[c["nombre"]] for c in candidatas)
        if not tiene_envio and not tiene_necesidad_sin_cubrir:
            continue

        productos.append({
            "familia":               fila["familia"],
            "subfamilia":            fila["subfamilia"],
            "grupo":                 fila["grupo"],
            "descripcion":           fila["descripcion"],
            "codigo":                int(cod_obl),
            "embalaje":              embalaje,
            "codigo_nacional":       int(cod_nacional) if cod_nacional else None,
            "codigo_otro":           int(cod_otro) if cod_otro else None,
            "stock_san_isidro":      round(stock_si, 0),
            "reserva_san_isidro":    round(reserva_si, 0),
            "disponible_san_isidro": round(disponible, 0),
            "sobrante_san_isidro":   round(disponible_restante, 0),
            "envios": {
                c["nombre"]: {
                    "cantidad_a_enviar": envios[c["nombre"]],
                    "desde_nacional":    desde_nacional[c["nombre"]],
                    "desde_otro":        desde_otro[c["nombre"]],
                    "stock_actual":      c["stock_actual"],
                    "alcance_actual":    c["alcance_actual"],
                    "necesidad":         round(c["necesidad"], 0),
                    "cubierto":          cubiertos[c["nombre"]],
                }
                for c in candidatas
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
        "objetivo_dias": do.OBJETIVO_DIAS_DISTRIBUCION,
    }


def exportar_distribucion_excel_pg(familia=None):
    return do._construir_excel_distribucion(get_distribucion_desde_san_isidro_pg(familia))


def _cargar_productos_extra_pg(cur, codigos):
    """embalaje/pedido_total/cup por codigo -- todo lo que Plan de
    Compra necesita de productos ademas de lo que ya trae
    _cargar_stock_pg (que es por codigo+bodega, no por codigo solo)."""
    if not codigos:
        return {}
    cur.execute(
        "select codigo, embalaje, pedido_total, cup from productos where codigo = any(%(codigos)s)",
        {"codigos": list(codigos)},
    )
    return {r["codigo"]: r for r in cur.fetchall()}


def get_plan_compra_reposicion_pg(familia=None, meses_objetivo_default=None):
    meses_default = meses_objetivo_default if meses_objetivo_default is not None else do.MESES_OBJETIVO_COMPRA

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            obligatorios = _cargar_obligatorios_pg(cur, familia)

            codigos_necesarios = set()
            for fila in obligatorios:
                codigos_necesarios.add(fila["codigo_obligatorio"])
                if fila["codigo_equivalente"] is not None:
                    codigos_necesarios.add(fila["codigo_equivalente"])

            bodegas = [n for n, _, _, _ in do.SUCURSALES_CRITICAS] + ["Todas"]
            datos = _cargar_stock_pg(cur, codigos_necesarios, bodegas)
            productos_extra = _cargar_productos_extra_pg(cur, codigos_necesarios)

    codigos_excluidos = dec.codigos_excluidos_compra()

    productos = []
    for fila in obligatorios:
        cod_obl = fila["codigo_obligatorio"]
        cod_equiv = fila["codigo_equivalente"]

        # Siempre se compra Nacional -- ver docstring de
        # get_plan_compra_reposicion en data_loader_obligatorios.py.
        es_obligatorio_nacional = str(fila["procedencia_obligatoria"] or "").strip().lower() == "nacional"
        cod_a_comprar = cod_equiv if cod_equiv else (cod_obl if es_obligatorio_nacional else None)

        if cod_a_comprar is None:
            productos.append({
                "familia":            fila["familia"],
                "subfamilia":         fila["subfamilia"],
                "grupo":              fila["grupo"],
                "descripcion":        fila["descripcion"],
                "codigo_a_comprar":   None,
                "embalaje":           None,
                "cantidad_a_comprar": None,
                "sin_opcion_nacional": True,
                "excluido_compra":    False,
            })
            continue

        extra_comprar = productos_extra.get(cod_a_comprar)

        venta_consolidada = _venta_combinada_pg(datos, cod_obl, cod_equiv, "Todas")
        stock_total = sum(
            _stock_combinado_pg(datos, cod_obl, cod_equiv, nombre)
            for nombre, _, _, _ in do.SUCURSALES_CRITICAS
        )

        meses_col = fila["meses_objetivo"]
        meses_objetivo = float(meses_col) if meses_col is not None else meses_default

        colchon_lead_time = (venta_consolidada / do.DIAS_HABILES_MES) * do.LEAD_TIME_DIAS_NACIONAL

        pedido_total = (
            float(extra_comprar["pedido_total"])
            if (extra_comprar and extra_comprar["pedido_total"] is not None) else 0.0
        )

        objetivo_empresa = (meses_objetivo * venta_consolidada) + colchon_lead_time
        necesario = max(0.0, objetivo_empresa - stock_total - pedido_total)

        embalaje = int(extra_comprar["embalaje"]) if (extra_comprar and extra_comprar["embalaje"]) else 1
        cantidad_a_comprar = math.ceil(necesario / embalaje) * embalaje if necesario > 0 else 0

        # Excluido de compra (ver /gestionar_productos_compra): el
        # producto sigue con su calculo normal para todo lo demas,
        # pero nunca se sugiere comprarlo -- se fuerza a 0 aca, al
        # final, sin tocar el resto del calculo.
        excluido_compra = cod_a_comprar in codigos_excluidos
        if excluido_compra:
            cantidad_a_comprar = 0

        productos.append({
            "familia":            fila["familia"],
            "subfamilia":         fila["subfamilia"],
            "grupo":              fila["grupo"],
            "descripcion":        fila["descripcion"],
            "codigo_a_comprar":   int(cod_a_comprar),
            "embalaje":           embalaje,
            "cantidad_a_comprar": cantidad_a_comprar,
            "sin_opcion_nacional": False,
            "excluido_compra":    excluido_compra,
        })

    productos.sort(key=lambda p: -(p["cantidad_a_comprar"] or 0))

    return {
        "productos": productos,
        "resumen": {
            "total_obligatorios":   len(productos),
            "con_necesidad_compra": sum(1 for p in productos if (p["cantidad_a_comprar"] or 0) > 0),
            "sin_opcion_nacional":  sum(1 for p in productos if p["sin_opcion_nacional"]),
            "excluidos_compra":     sum(1 for p in productos if p["excluido_compra"]),
        },
    }


def get_resumen_valor_compra_pg(familia=None):
    niveles = []
    for meses in do.NIVELES_COMPARACION_MESES:
        resultado = get_plan_compra_reposicion_pg(familia, meses)
        candidatos = [
            p for p in resultado["productos"]
            if not p["sin_opcion_nacional"] and (p["cantidad_a_comprar"] or 0) > 0
        ]
        codigos = {p["codigo_a_comprar"] for p in candidatos}
        with db.conexion_pool() as conn:
            with conn.cursor() as cur:
                extra = _cargar_productos_extra_pg(cur, codigos)
        valor_total = sum(
            p["cantidad_a_comprar"] * float(extra[p["codigo_a_comprar"]]["cup"] or 0)
            for p in candidatos
        )
        niveles.append({"meses": meses, "valor": round(valor_total, 0)})

    return {"niveles": niveles}


def exportar_plan_compras_excel_pg(familia=None, meses_objetivo_default=None):
    return do._construir_excel_plan_compras(get_plan_compra_reposicion_pg(familia, meses_objetivo_default))


# ══════════════════════════════════════════════════════
#  PRIORIDAD (self-service sobre productos_obligatorios) --
#  /gestionar_prioridad: promover un producto (tipicamente de Segunda
#  Linea) a la lista curada de Obligatorios, o quitarlo. Reemplaza
#  tener que editar Productos_Obligatorios.xlsx a mano y re-correr el
#  backfill para cualquier cambio.
# ══════════════════════════════════════════════════════
def get_lista_prioridad():
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select codigo_obligatorio, familia, subfamilia, grupo, descripcion,
                       procedencia_obligatoria, codigo_equivalente, meses_objetivo,
                       updated_at, updated_by
                from productos_obligatorios
                order by familia, subfamilia, descripcion
            """)
            filas = cur.fetchall()
    return [
        {
            "codigo":               f["codigo_obligatorio"],
            "familia":              f["familia"],
            "subfamilia":           f["subfamilia"],
            "grupo":                f["grupo"],
            "descripcion":          f["descripcion"],
            "procedencia":          f["procedencia_obligatoria"],
            "codigo_equivalente":   f["codigo_equivalente"],
            "meses_objetivo":       float(f["meses_objetivo"]) if f["meses_objetivo"] is not None else None,
            "updated_at":           f["updated_at"].strftime("%d/%m/%Y") if f["updated_at"] else None,
            "updated_by":           f["updated_by"],
        }
        for f in filas
    ]


# Subfamilias donde el segundo nivel del Plan de Compra debe agruparse
# por COLOR (no por el "grupo" real de SAP, que ahi es solo
# TAPAS/MODULOS/ARMADOS y no dice nada util para reponer) -- ej. Living
# Now, Luzica. En cualquier otra subfamilia "grupo" sigue siendo el de
# SAP (ej. diametro en Conduit PVC).
SUBFAMILIAS_AGRUPAR_POR_COLOR = {"LIVING NOW", "LUZICA"}
# Prefijos (no la palabra completa) para cubrir concordancia de genero
# en español: "blanco/blanca", "negro/negra". "arena" no varia.
COLORES_CONOCIDOS = {"BLANC": "Blanco", "NEGR": "Negro", "ARENA": "Arena"}


def _grupo_para_obligatorios(subfamilia, descripcion, grupo_real):
    """Grupo a guardar en productos_obligatorios: el color detectado en
    la descripcion si la subfamilia es de las que se agrupan por color,
    'Soporte'/'Genericos' si es un producto de esa subfamilia sin color
    visible (mecanismo interno), o el grupo real de SAP en cualquier
    otro caso."""
    if (subfamilia or "").upper() not in SUBFAMILIAS_AGRUPAR_POR_COLOR:
        return grupo_real
    desc_up = (descripcion or "").upper()
    for prefijo, color in COLORES_CONOCIDOS.items():
        if prefijo in desc_up:
            return color
    if "SOPORTE" in desc_up:
        return "Soporte"
    return "Genericos"


def promover_a_prioridad(codigo, procedencia_obligatoria, codigo_equivalente=None, updated_by=None):
    """Agrega (o actualiza si ya existia) un producto a
    productos_obligatorios -- familia/subfamilia/descripcion se
    autocompletan desde productos, no hay que volver a escribirlos.
    "grupo" tambien se autocompleta, salvo en subfamilias de
    SUBFAMILIAS_AGRUPAR_POR_COLOR donde se reemplaza por el color
    detectado en la descripcion (ver _grupo_para_obligatorios)."""
    if str(codigo).startswith(dec.PREFIJOS_FUERA_SEGUNDA_LINEA):
        raise ValueError(f"El código {codigo} empieza con un prefijo excluido (no son productos de reventa) y no puede promoverse a Prioridad.")

    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select codigo, familia, subfamilia, grupo, descripcion from productos where codigo = %s",
                (codigo,),
            )
            p = cur.fetchone()
    if not p:
        raise ValueError(f"El código {codigo} no existe en productos.")

    grupo = _grupo_para_obligatorios(p["subfamilia"], p["descripcion"], p["grupo"])

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into productos_obligatorios
                    (codigo_obligatorio, familia, subfamilia, grupo, descripcion,
                     procedencia_obligatoria, codigo_equivalente, updated_by)
                values (%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (codigo_obligatorio) do update set
                    familia=excluded.familia, subfamilia=excluded.subfamilia, grupo=excluded.grupo,
                    descripcion=excluded.descripcion, procedencia_obligatoria=excluded.procedencia_obligatoria,
                    codigo_equivalente=excluded.codigo_equivalente, updated_by=excluded.updated_by,
                    updated_at=now()
                """,
                (codigo, p["familia"], p["subfamilia"], grupo, p["descripcion"],
                 procedencia_obligatoria, codigo_equivalente, updated_by),
            )
        conn.commit()


def quitar_de_prioridad(codigo):
    """Sacar un producto de Obligatorios -- si sigue siendo AAA/M05,
    vuelve a aparecer solo en Segunda Linea (esa lista ya excluye por
    consulta cualquier codigo que este en productos_obligatorios, asi
    que no hace falta ningun paso extra aca)."""
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from productos_obligatorios where codigo_obligatorio = %s", (codigo,))
        conn.commit()
