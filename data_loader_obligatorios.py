"""
Productos Obligatorios: la lista de productos criticos que "no
pueden faltar" en ninguna sucursal (data/inventario/Productos_
Obligatorios.xlsx, mantenida a mano, se completa Familia por
Familia), y las dos pantallas que se calculan sobre ella --
Alertas de Quiebre Critico y Plan de Compra/Reposicion.

Depende de data_loader_inventario para leer el stock/venta real
(via _leer_inventario()) -- este modulo solo agrega la logica de
"que productos son obligatorios y como estan".
"""
import os
import io
import math
import pandas as pd

import data_loader_inventario as dli

PRODUCTOS_OBLIGATORIOS_XLSX = os.path.join(dli.DATA_DIR_INVENTARIO, "Productos_Obligatorios.xlsx")

# Sucursales "de mostrador" para la alerta de quiebre critico -- a
# diferencia de BODEGAS (que incluye canales especiales como Oficina,
# Mercado Libre o E-commerce), aca solo van las sucursales fisicas
# donde un producto obligatorio "no puede faltar". San Isidro se marca
# aparte porque ademas de sucursal es el CD que reabastece a las
# demas -- si San Isidro queda en $0 no hay de donde reponer, por eso
# es la alerta mas critica.
#
# Incluye tambien la columna de TRANSITO de cada sucursal -- el
# stock que se usa en toda esta pantalla es "stock + transito"
# sumado de una vez (no se muestra por separado): un producto con
# $0 en bodega pero con unidades ya compradas en camino no deberia
# aparecer como quiebre total, porque esa mercaderia ya esta
# resuelta, solo falta que llegue.
SUCURSALES_CRITICAS = [
    ("Chicureo",           "STOCK CHICUREO",           "TRANSITO CHICUREO",           False),
    ("Las Condes",         "STOCK LAS CONDES",         "TRANSITO LAS CONDES",         False),
    ("Maipú",              "STOCK MAIPU",              "TRANSITO MAIPU",              False),
    ("Manuel Rodríguez",   "STOCK MANUEL RODRIGUEZ",   "TRANSITO MANUEL RODRIGUEZ",   False),
    ("Matta",              "STOCK MATTA",              "TRANSITO MATTA",              False),
    ("San Isidro",         "STOCK SAN ISIDRO",         "TRANSITO SAN ISIDRO",         True),   # True = es CD
]

_cache_obligatorios = {"df": None, "mod_time": None}


def _leer_productos_obligatorios():
    """Lista de productos criticos que "no pueden faltar" en ninguna
    sucursal, mantenida a mano (se va completando Familia por Familia,
    no es necesario tener todo el catalogo cubierto para que esto
    funcione). CODIGO_EQUIVALENTE es opcional -- hay productos
    obligatorios que no tienen sustituto (ej. algunos que solo existen
    en version Nacional)."""
    if not os.path.exists(PRODUCTOS_OBLIGATORIOS_XLSX):
        return pd.DataFrame(columns=[
            "FAMILIA", "SUBFAMILIA", "GRUPO", "DESCRIPCION", "CODIGO_OBLIGATORIO",
            "PROCEDENCIA_OBLIGATORIA", "CODIGO_EQUIVALENTE", "MESES_OBJETIVO",
        ])
    mod_time = os.path.getmtime(PRODUCTOS_OBLIGATORIOS_XLSX)
    if _cache_obligatorios["df"] is None or _cache_obligatorios["mod_time"] != mod_time:
        try:
            df = pd.read_excel(PRODUCTOS_OBLIGATORIOS_XLSX, engine="calamine")
        except Exception:
            df = pd.read_excel(PRODUCTOS_OBLIGATORIOS_XLSX)
        _cache_obligatorios["df"] = df
        _cache_obligatorios["mod_time"] = mod_time
    return _cache_obligatorios["df"]


def get_familias_obligatorios():
    """Familias disponibles en Productos_Obligatorios.xlsx, para el
    selector de la pantalla de alertas."""
    obl = _leer_productos_obligatorios()
    return sorted(obl["FAMILIA"].dropna().unique().tolist())


def _valor_col(fila_prod, col):
    if fila_prod is None or col not in fila_prod or pd.isna(fila_prod[col]):
        return 0.0
    return float(fila_prod[col])


def _stock_combinado(fila_obl, fila_equiv, stock_col, transito_col=None):
    """Stock + transito de la sucursal para el par Importado+Nacional
    -- se SUMAN los 4 valores (stock y transito, de ambas
    procedencias) porque son unidades fisicas reales o ya compradas y
    en camino, que cuentan igual para saber cuanto hay disponible
    pronto. El transito no se muestra por separado a proposito (para
    no complejizar la pantalla) -- el numero que se ve ya es el
    total."""
    total = _valor_col(fila_obl, stock_col) + _valor_col(fila_equiv, stock_col)
    if transito_col:
        total += _valor_col(fila_obl, transito_col) + _valor_col(fila_equiv, transito_col)
    return total


def _pedido_nacional(fila_comprar):
    """Cantidad ya pedida al proveedor del CODIGO QUE SE VA A COMPRAR
    (siempre Nacional en Plan de Compra -- ver cod_a_comprar), leida
    de PEDIDO_TOTAL en Inventario.xlsx (exportada directo desde SAP
    junto con stock/transito -- no es un archivo aparte que haya que
    mantener sincronizado). A proposito NO se suma el pedido del
    codigo Importado aunque exista: ese pedido demora ~60 dias en
    llegar y no resuelve la necesidad inmediata que calcula esta
    pantalla -- mezclarlo aca subestimaria cuanto Nacional hace falta
    pedir ahora. Solo se resta en Plan de Compra -- NO se usa en
    Alertas de Quiebre, que se mantiene como una senal conservadora de
    lo que hay fisicamente disponible o a punto de llegar."""
    return _valor_col(fila_comprar, "PEDIDO_TOTAL")


def _venta_combinada(fila_obl, fila_equiv, col):
    """Venta mensual del par Importado+Nacional -- a diferencia del
    stock, aca se usa el PROMEDIO (no la suma) cuando existen ambos
    codigos. Sumar sobreestimaria la demanda real: cuando el Importado
    quiebra, parte de esa misma venta se termina registrando bajo el
    Nacional (sustitucion), asi que ambos numeros no son demanda
    100% adicional entre si. Si el producto no tiene equivalente
    (fila_equiv es None), no hay nada que promediar y se usa tal cual
    la venta del unico codigo que existe."""
    v_obl = _valor_col(fila_obl, col)
    if fila_equiv is None:
        return v_obl
    v_equiv = _valor_col(fila_equiv, col)
    return (v_obl + v_equiv) / 2


# Umbrales de alcance EN DIAS de cobertura para el semaforo de
# Alertas de Quiebre Critico -- se paso de meses a dias porque el
# lead time real del proveedor Nacional (medido en la pantalla de
# Lead Time por Proveedor: ~5.7 dias promedio empresa) esta en dias,
# y comparar "1 mes de stock" contra "5.7 dias de lead time" obliga a
# convertir mentalmente cada vez. En dias la comparacion es directa.
# Rojo (15 dias) es ~2.6x el lead time promedio -- deja margen para
# variabilidad de venta y proveedores algo mas lentos que el
# promedio, sin ser un umbral arbitrario de "1 mes" sin relacion con
# cuanto realmente demora reponer. Amarillo (25 dias) es una zona de
# planificacion, no de urgencia real. Umbral UNICO para todos los
# proveedores por ahora (no varia segun el lead time especifico de
# cada uno, aunque algunos -- ej. Industria Manufactura Electrica,
# ~15.9 dias -- son mas lentos que el propio corte rojo).
UMBRAL_QUIEBRE_CRITICO_DIAS = 15    # rojo
UMBRAL_ALERTA_TEMPRANA_DIAS = 25    # amarillo (entre este y el rojo)


def _clasificar_nivel(alcance, stock_combinado):
    """Clasifica una celda (sucursal o total) en 3 niveles segun
    DIAS de cobertura -- rojo es la alerta critica real (menos de
    UMBRAL_QUIEBRE_CRITICO_DIAS, ya no queda margen para reaccionar
    dado el lead time del proveedor), amarillo es alerta temprana
    (entre el umbral critico y UMBRAL_ALERTA_TEMPRANA_DIAS, para
    planificar sin apuro), verde esta bien. Si no hay dato de venta
    (alcance None), se usa como respaldo el quiebre simple (stock en
    $0 = rojo), igual que antes."""
    if alcance is not None:
        if alcance < UMBRAL_QUIEBRE_CRITICO_DIAS:
            return "rojo"
        if alcance < UMBRAL_ALERTA_TEMPRANA_DIAS:
            return "amarillo"
        return "verde"
    return "rojo" if stock_combinado <= 0 else "verde"


def get_alertas_quiebre_critico(familia=None):
    """
    Para cada producto obligatorio, en cada sucursal critica
    (SUCURSALES_CRITICAS) se combina el Importado obligatorio con su
    CODIGO_EQUIVALENTE Nacional -- son el mismo producto para el
    cliente, asi que se suman stock y venta mensual de ambos antes de
    calcular Alcance (DIAS de cobertura = stock combinado / venta
    diaria combinada, mes de 30 dias). Semaforo de 2 niveles, ver
    _clasificar_nivel.

    Venta Mensual no existe para todas las sucursales (falta en
    Maipu, ver dli.VENTA_MENSUAL_COL) -- donde no hay ese dato, se usa
    como respaldo el quiebre simple (stock combinado en $0), en vez de
    dejar la sucursal sin ninguna senal.

    San Isidro se marca como "es_cd": un quiebre ahi es la alerta mas
    critica porque es la sucursal que reabastece a las demas.
    """
    df_inv = dli._leer_inventario()
    obl = _leer_productos_obligatorios()
    if familia:
        obl = obl[obl["FAMILIA"] == familia]

    codigos_necesarios = set(obl["CODIGO_OBLIGATORIO"].dropna()) | set(obl["CODIGO_EQUIVALENTE"].dropna())
    stock_por_codigo = {
        row["CODIGO"]: row
        for _, row in df_inv[df_inv["CODIGO"].isin(codigos_necesarios)].iterrows()
    }

    productos = []
    total_quiebres = 0
    total_quiebres_cd = 0

    for _, fila in obl.iterrows():
        cod_obl = fila["CODIGO_OBLIGATORIO"]
        cod_equiv = fila["CODIGO_EQUIVALENTE"] if pd.notna(fila.get("CODIGO_EQUIVALENTE")) else None
        fila_stock_obl = stock_por_codigo.get(cod_obl)
        fila_stock_equiv = stock_por_codigo.get(cod_equiv) if cod_equiv else None

        sucursales = {}
        tiene_quiebre = False
        tiene_alerta_temprana = False
        tiene_quiebre_cd = False
        for nombre, stock_col, transito_col, es_cd in SUCURSALES_CRITICAS:
            stock_combinado = _stock_combinado(fila_stock_obl, fila_stock_equiv, stock_col, transito_col)

            # San Isidro es el CD que reabastece a todas las demas
            # sucursales -- no tenemos el dato real de cuanto transfiere
            # a cada una, asi que se aproxima con "venta consolidada
            # menos su propia venta local": lo que las OTRAS sucursales
            # necesitan reponer (San Isidro tambien vende directo, no
            # es un CD puro -- su venta local es ~36% del total, y no
            # tiene sentido exigirle stock para cubrir su propia venta
            # DOS veces: una como "sucursal" y otra de nuevo dentro del
            # total consolidado). Sigue siendo una aproximacion (no hay
            # forma de saber cuanto va a cada sucursal especifica), pero
            # ya no compara su stock contra el 100% de la demanda de
            # toda la empresa incluyendose a si misma.
            venta_col = dli.VENTA_MENSUAL_TODAS if es_cd else dli.VENTA_MENSUAL_COL.get(nombre)
            alcance = None
            if venta_col:
                venta_combinada = _venta_combinada(fila_stock_obl, fila_stock_equiv, venta_col)
                if es_cd:
                    venta_local_si = _venta_combinada(
                        fila_stock_obl, fila_stock_equiv, dli.VENTA_MENSUAL_COL["San Isidro"]
                    )
                    venta_combinada = max(0.0, venta_combinada - venta_local_si)
                if venta_combinada > 0:
                    # Alcance en DIAS de cobertura (mes de 30 dias
                    # corridos, mismo criterio ya usado en el resto
                    # del modulo para pasar de venta mensual a diaria).
                    alcance = round(stock_combinado / venta_combinada * 30, 1)

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
                total_quiebres += 1
                if es_cd:
                    tiene_quiebre_cd = True
                    total_quiebres_cd += 1
            elif nivel == "amarillo":
                tiene_alerta_temprana = True

        # Columna Total -- stock sumado de TODAS las sucursales criticas
        # (dato completo, no falta en ninguna) y venta mensual usando
        # VENTA MENSUAL CONSOLIDADA (el total real de la empresa, no
        # solo la suma de las 5 sucursales con dato propio -- asi
        # tampoco se pierde lo que se vende en Maipu).
        stock_total = sum(s["stock"] for s in sucursales.values())
        venta_total_combinada = _venta_combinada(fila_stock_obl, fila_stock_equiv, dli.VENTA_MENSUAL_TODAS)
        alcance_total = round(stock_total / venta_total_combinada * 30, 1) if venta_total_combinada > 0 else None
        nivel_total = _clasificar_nivel(alcance_total, stock_total)
        total = {"stock": round(stock_total, 0), "alcance": alcance_total, "nivel": nivel_total, "quiebre": nivel_total == "rojo"}

        productos.append({
            "familia":              fila["FAMILIA"],
            "subfamilia":           fila["SUBFAMILIA"],
            "grupo":                fila.get("GRUPO") if pd.notna(fila.get("GRUPO")) else None,
            "descripcion":          fila["DESCRIPCION"],
            "codigo":               int(cod_obl),
            "procedencia":          fila.get("PROCEDENCIA_OBLIGATORIA"),
            "codigo_equivalente":   int(cod_equiv) if cod_equiv else None,
            "sucursales":           sucursales,
            "total":                total,
            "tiene_quiebre":        tiene_quiebre,
            "tiene_alerta_temprana": tiene_alerta_temprana,
            "tiene_quiebre_cd":     tiene_quiebre_cd,
        })

    return {
        "sucursales": [{"nombre": n, "es_cd": cd} for n, _, _, cd in SUCURSALES_CRITICAS],
        "productos":  productos,
        "resumen": {
            "total_obligatorios":  len(productos),
            "con_quiebre":         sum(1 for p in productos if p["tiene_quiebre"]),
            "con_alerta_temprana": sum(1 for p in productos if p["tiene_alerta_temprana"] and not p["tiene_quiebre"]),
            "con_quiebre_cd":      sum(1 for p in productos if p["tiene_quiebre_cd"]),
        },
    }


ESTADO_POR_NIVEL = {"rojo": "Quiebre Critico", "amarillo": "Alerta Temprana", "verde": "OK"}


def exportar_alertas_excel(familia=None):
    """
    Genera un Excel en memoria con el detalle de Alertas de Quiebre
    Critico -- una fila por producto obligatorio, con Stock, Alcance
    (dias) y Estado por cada sucursal critica, mas la columna Total.
    Mismo criterio de familia que la pantalla. Devuelve un BytesIO
    listo para mandar como descarga.
    """
    resultado = get_alertas_quiebre_critico(familia)
    nombres_sucursales = [s["nombre"] for s in resultado["sucursales"]]

    filas = []
    for p in resultado["productos"]:
        fila = {
            "Familia":     p["familia"],
            "Subfamilia":  p["subfamilia"],
            "Grupo":       p["grupo"] or "",
            "Descripcion": p["descripcion"],
            "Codigo":      p["codigo"],
            "Procedencia": p["procedencia"],
        }
        for nombre in nombres_sucursales:
            info = p["sucursales"][nombre]
            fila[f"{nombre} - Stock"] = info["stock"]
            fila[f"{nombre} - Alcance (dias)"] = info["alcance"]
            fila[f"{nombre} - Estado"] = ESTADO_POR_NIVEL[info["nivel"]]
        fila["Total - Stock"] = p["total"]["stock"]
        fila["Total - Alcance (dias)"] = p["total"]["alcance"]
        fila["Total - Estado"] = ESTADO_POR_NIVEL[p["total"]["nivel"]]
        filas.append(fila)

    columnas = ["Familia", "Subfamilia", "Grupo", "Descripcion", "Codigo", "Procedencia"]
    for nombre in nombres_sucursales:
        columnas += [f"{nombre} - Stock", f"{nombre} - Alcance (dias)", f"{nombre} - Estado"]
    columnas += ["Total - Stock", "Total - Alcance (dias)", "Total - Estado"]

    df = pd.DataFrame(filas, columns=columnas)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Alertas Quiebre")
    buffer.seek(0)
    return buffer


# Objetivo de dias de cobertura para la distribucion desde San Isidro
# -- mismo corte que el "verde" de Alertas (25 dias), para que ambas
# pantallas hablen el mismo idioma: se reparte hasta que la sucursal
# deje de estar en rojo/amarillo.
OBJETIVO_DIAS_DISTRIBUCION = UMBRAL_ALERTA_TEMPRANA_DIAS


def get_distribucion_desde_san_isidro(familia=None):
    """
    Cuanto despachar desde San Isidro (CD) a cada una de las otras 5
    sucursales, producto por producto, para que cada una llegue a
    OBJETIVO_DIAS_DISTRIBUCION dias de cobertura -- sin dejar a San
    Isidro por debajo de lo que necesita para su propia venta local.

    Paso 1 -- reserva de San Isidro: igual que el ajuste ya aplicado
    en Alertas, se calcula cuanto necesita San Isidro para cubrir SU
    PROPIA venta local (no la consolidada) durante el mismo objetivo
    de dias. Lo que sobra de su stock actual sobre esa reserva es lo
    "disponible para despachar".

    Paso 2 -- reparto por urgencia: las 5 sucursales se ordenan de
    MENOR a MAYOR alcance actual (la mas urgente primero -- esto ya
    ordena rojo, despues amarillo, despues verde, sin necesitar una
    regla aparte). Se le asigna a la primera lo que le falta para
    llegar al objetivo, despues a la siguiente, y asi hasta que se
    acaba lo disponible. Si no alcanza para todas, las que ya estaban
    mejor (mayor alcance) quedan sin nada -- a proposito, es la
    sucursal que menos lo necesita en este momento.

    Maipu no tiene columna de venta mensual propia (ver
    dli.VENTA_MENSUAL_COL), asi que no se le puede calcular un
    objetivo en dias como al resto -- usa una regla fija en su lugar:
    para la familia Conductores (cables), objetivo = 500 mts siempre;
    para el resto, solo completar 1 embalaje. Se ordena junto a las
    demas sucursales por una escala de cobertura equivalente en dias
    (no es un alcance real medido, es una aproximacion solo para
    ordenar la urgencia).

    Los envios se redondean hacia ABAJO al multiplo de EMBALAJE del
    producto (no se puede despachar una caja partida, y redondear
    para arriba podria mandar mas de lo que hay disponible en San
    Isidro).

    Codigo a despachar: el stock combinado de San Isidro junta el
    Importado (obligatorio) con su equivalente Nacional, pero son dos
    codigos fisicos distintos guardados por separado. Se prefiere
    despachar siempre desde el Nacional primero (mismo criterio que
    Plan de Compra); si su stock en San Isidro no alcanza para cubrir
    el envio completo, se completa el resto con el otro codigo. Cada
    envio queda desglosado en "desde_nacional" / "desde_otro".
    """
    df_inv = dli._leer_inventario()
    obl = _leer_productos_obligatorios()
    if familia:
        obl = obl[obl["FAMILIA"] == familia]

    codigos_necesarios = set(obl["CODIGO_OBLIGATORIO"].dropna()) | set(obl["CODIGO_EQUIVALENTE"].dropna())
    stock_por_codigo = {
        row["CODIGO"]: row
        for _, row in df_inv[df_inv["CODIGO"].isin(codigos_necesarios)].iterrows()
    }

    sucursales_destino = [(n, sc, tc) for n, sc, tc, es_cd in SUCURSALES_CRITICAS if not es_cd]

    productos = []
    for _, fila in obl.iterrows():
        cod_obl = fila["CODIGO_OBLIGATORIO"]
        cod_equiv = fila["CODIGO_EQUIVALENTE"] if pd.notna(fila.get("CODIGO_EQUIVALENTE")) else None
        fila_stock_obl = stock_por_codigo.get(cod_obl)
        fila_stock_equiv = stock_por_codigo.get(cod_equiv) if cod_equiv else None

        stock_si = _stock_combinado(fila_stock_obl, fila_stock_equiv, "STOCK SAN ISIDRO", "TRANSITO SAN ISIDRO")
        venta_local_si = _venta_combinada(fila_stock_obl, fila_stock_equiv, dli.VENTA_MENSUAL_COL["San Isidro"])
        reserva_si = (venta_local_si / 30) * OBJETIVO_DIAS_DISTRIBUCION
        disponible = max(0.0, stock_si - reserva_si)

        # El stock combinado de San Isidro junta el Importado (obligatorio)
        # con su equivalente Nacional, pero fisicamente son DOS codigos
        # distintos guardados por separado -- hay que decir de cual
        # despachar. Igual que en Plan de Compra, se prefiere el
        # Nacional primero; si no alcanza, se completa con el otro
        # codigo (el obligatorio, sea cual sea su procedencia).
        cod_nacional = cod_equiv if cod_equiv else None
        cod_otro = cod_obl if cod_equiv else None
        if not cod_equiv:
            es_obligatorio_nacional = str(fila.get("PROCEDENCIA_OBLIGATORIA", "")).strip().lower() == "nacional"
            if es_obligatorio_nacional:
                cod_nacional = cod_obl
        stock_si_nacional = (
            _valor_col(stock_por_codigo.get(cod_nacional), "STOCK SAN ISIDRO")
            + _valor_col(stock_por_codigo.get(cod_nacional), "TRANSITO SAN ISIDRO")
        ) if cod_nacional else 0.0
        disponible_nacional_usable = min(stock_si_nacional, disponible)
        disponible_otro_usable = max(0.0, disponible - disponible_nacional_usable)

        # Embalaje del producto -- los envios se redondean HACIA ABAJO
        # al multiplo mas cercano (no se puede despachar una caja
        # partida, y redondear para arriba podria mandar mas de lo
        # que hay disponible). Se usa el embalaje del obligatorio, o
        # del equivalente si el obligatorio no tiene stock cargado.
        fila_embalaje = fila_stock_obl if fila_stock_obl is not None else fila_stock_equiv
        embalaje = int(fila_embalaje["EMBALAJE"]) if (
            fila_embalaje is not None and pd.notna(fila_embalaje.get("EMBALAJE")) and fila_embalaje["EMBALAJE"] > 0
        ) else 1

        candidatas = []
        for nombre, stock_col, transito_col in sucursales_destino:
            stock_suc = _stock_combinado(fila_stock_obl, fila_stock_equiv, stock_col, transito_col)

            if nombre == "Maipú":
                # Maipu no tiene columna de venta mensual en
                # Inventario.xlsx -- no se puede calcular un objetivo
                # en dias. Se usa una regla fija en su lugar: para
                # Conductores (cables), objetivo = 500 mts siempre;
                # para el resto, solo completar 1 embalaje.
                objetivo_unidades = 500.0 if fila["FAMILIA"] == "CONDUCTORES" else float(embalaje)
                necesidad = max(0.0, objetivo_unidades - stock_suc)
                alcance_actual = None
                # Escala de cobertura (0-1) llevada a "dias equivalentes"
                # sobre el mismo objetivo de dias, solo para poder
                # ordenar a Maipu junto a las demas sucursales por
                # urgencia real, en vez de mandarla siempre al final.
                orden_urgencia = (
                    (stock_suc / objetivo_unidades) * OBJETIVO_DIAS_DISTRIBUCION
                    if objetivo_unidades > 0 else OBJETIVO_DIAS_DISTRIBUCION
                )
            else:
                venta_col = dli.VENTA_MENSUAL_COL.get(nombre)
                venta_suc = _venta_combinada(fila_stock_obl, fila_stock_equiv, venta_col) if venta_col else 0.0
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
                "nombre":        nombre,
                "stock_actual":  round(stock_suc, 0),
                "alcance_actual": alcance_actual,
                "necesidad":     necesidad,
                "orden_urgencia": orden_urgencia,
            })

        # Mas urgente primero -- menor "orden_urgencia" (dias reales
        # para las demas, dias-equivalentes para Maipu). Sin ningun
        # dato para ordenar (ninguna venta y no es Maipu) va al final.
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
            # "Cubierto" mira si HABIA disponible suficiente antes de
            # redondear al embalaje -- una diferencia de menos de un
            # embalaje completo es solo redondeo de caja, no un
            # verdadero deficit de stock.
            cubiertos[c["nombre"]] = (c["necesidad"] - envio_crudo) < 1
            disponible_restante -= envio

            # De ese envio, cuanto sale del codigo Nacional (preferido)
            # y cuanto del otro codigo (solo si el Nacional no alcanza).
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
            "familia":               fila["FAMILIA"],
            "subfamilia":            fila["SUBFAMILIA"],
            "grupo":                 fila.get("GRUPO") if pd.notna(fila.get("GRUPO")) else None,
            "descripcion":           fila["DESCRIPCION"],
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
        "sucursales": [n for n, _, _ in sucursales_destino],
        "resumen": {
            "total_productos":        len(productos),
            "total_unidades_a_enviar": round(total_a_enviar, 0),
            "productos_con_deficit":  n_con_deficit,
        },
        "objetivo_dias": OBJETIVO_DIAS_DISTRIBUCION,
    }


def exportar_distribucion_excel(familia=None):
    """
    Genera un Excel en memoria con el detalle de Distribucion desde
    San Isidro -- una fila por producto, con el stock/disponible de
    San Isidro y, por cada sucursal destino, cuanto enviar, cuanto
    necesita y si queda cubierta. Devuelve un BytesIO listo para
    mandar como descarga.
    """
    resultado = get_distribucion_desde_san_isidro(familia)
    nombres_sucursales = resultado["sucursales"]

    filas = []
    for p in resultado["productos"]:
        fila = {
            "Familia":              p["familia"],
            "Subfamilia":           p["subfamilia"],
            "Grupo":                p["grupo"] or "",
            "Descripcion":          p["descripcion"],
            "Codigo Obligatorio":   p["codigo"],
            "Codigo Nacional (despachar primero)": p["codigo_nacional"] or "",
            "Codigo Otro (si Nacional no alcanza)": p["codigo_otro"] or "",
            "Embalaje":             p["embalaje"],
            "Stock San Isidro":     p["stock_san_isidro"],
            "Disponible SI":        p["disponible_san_isidro"],
        }
        for nombre in nombres_sucursales:
            info = p["envios"][nombre]
            fila[f"{nombre} - Enviar"] = info["cantidad_a_enviar"]
            fila[f"{nombre} - Desde Nacional"] = info["desde_nacional"]
            fila[f"{nombre} - Desde Otro"] = info["desde_otro"]
            fila[f"{nombre} - Necesidad"] = info["necesidad"]
            fila[f"{nombre} - Cubierto"] = "Si" if info["cubierto"] else "No"
        filas.append(fila)

    columnas = [
        "Familia", "Subfamilia", "Grupo", "Descripcion", "Codigo Obligatorio",
        "Codigo Nacional (despachar primero)", "Codigo Otro (si Nacional no alcanza)",
        "Embalaje", "Stock San Isidro", "Disponible SI",
    ]
    for nombre in nombres_sucursales:
        columnas += [
            f"{nombre} - Enviar", f"{nombre} - Desde Nacional", f"{nombre} - Desde Otro",
            f"{nombre} - Necesidad", f"{nombre} - Cubierto",
        ]

    df = pd.DataFrame(filas, columns=columnas)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Distribucion")
    buffer.seek(0)
    return buffer


# Meses objetivo de cobertura para el plan de compras -- se calcula
# UNA sola vez a nivel empresa (no sucursal por sucursal), porque en
# la practica si existe traspaso de stock entre sucursales: lo que
# sobra en una se puede mover a la que le falta, asi que no hace
# falta comprar de mas asumiendo que cada sucursal es una isla.
MESES_OBJETIVO_COMPRA = 2.0

# Colchon de lead time del proveedor Nacional (dias habiles promedio
# entre colocar la OC y que llegue la mercaderia). El calculo de
# "cuanto comprar" compara el objetivo contra el stock de HOY, pero
# para cuando la compra llegue ya se habran vendido varios dias mas
# -- sin este colchon, la sugerencia de compra queda corta justo en
# los productos de mayor rotacion con poco stock, que son los mas
# criticos. Se usan 20 dias habiles por mes (no 30 dias corridos)
# porque asi se vende realmente, de lunes a sabado aprox.
LEAD_TIME_DIAS_NACIONAL = 5
DIAS_HABILES_MES = 20


def get_plan_compra_reposicion(familia=None, meses_objetivo_default=None):
    """
    Para cada producto obligatorio, calcula cuanto hay que comprar en
    total (siempre del CODIGO Nacional -- el equivalente si existe, o
    el mismo CODIGO_OBLIGATORIO si ya es Nacional -- porque el
    Importado demora 60 dias y no sirve para responder a una
    necesidad inmediata):

        comprar = max(0, objetivo_empresa - stock_total_en_la_red - pedido_total)
        objetivo_empresa = (meses_objetivo x venta mensual consolidada)
                            + colchon_lead_time
        colchon_lead_time = (venta_mensual / 20 dias habiles) x 5 dias
                            de lead time del proveedor Nacional
        pedido_total = PEDIDO_TOTAL del codigo Nacional que se va a
                            comprar (NO del Importado, ver _pedido_nacional)

    Es un calculo a nivel empresa, no sucursal por sucursal -- como
    si existe traspaso de stock entre sucursales, el stock que sobra
    en una compensa el que falta en otra, y no corresponde sumar
    necesidades sucursal por sucursal (eso sobreestimaria la compra).

    meses_objetivo_default reemplaza el default global de
    MESES_OBJETIVO_COMPRA (para el selector de "meses de inventario"
    de la pantalla) -- pero SOLO para los productos que no tienen su
    propio MESES_OBJETIVO en la planilla (ej. MatixGo Gris, que
    siempre usa 1 mes sin importar lo que elija el usuario).

    La cantidad final se redondea hacia arriba al embalaje real del
    producto (dato que viene en Inventario.xlsx, varia por producto --
    ej. 100 o 200 metros), asi que nunca se sugiere comprar una
    cantidad que no se pueda pedir tal cual.
    """
    meses_default = meses_objetivo_default if meses_objetivo_default is not None else MESES_OBJETIVO_COMPRA
    df_inv = dli._leer_inventario()
    obl = _leer_productos_obligatorios()
    if familia:
        obl = obl[obl["FAMILIA"] == familia]

    codigos_necesarios = set(obl["CODIGO_OBLIGATORIO"].dropna()) | set(obl["CODIGO_EQUIVALENTE"].dropna())
    stock_por_codigo = {
        row["CODIGO"]: row
        for _, row in df_inv[df_inv["CODIGO"].isin(codigos_necesarios)].iterrows()
    }

    productos = []
    for _, fila in obl.iterrows():
        cod_obl = fila["CODIGO_OBLIGATORIO"]
        cod_equiv = fila["CODIGO_EQUIVALENTE"] if pd.notna(fila.get("CODIGO_EQUIVALENTE")) else None
        fila_stock_obl = stock_por_codigo.get(cod_obl)
        fila_stock_equiv = stock_por_codigo.get(cod_equiv) if cod_equiv else None

        # Siempre se compra Nacional -- el equivalente si existe, si no
        # el mismo obligatorio SOLO SI ese obligatorio ya es Nacional
        # (caso Cordones/Alambres). Si el obligatorio es Importado y no
        # tiene equivalente (ej. un accesorio que solo existe
        # Importado), no hay ningun codigo Nacional que comprar -- no
        # se puede asumir que el obligatorio sirve de reemplazo, porque
        # el Importado demora 60 dias y no resuelve la necesidad
        # inmediata que es el objetivo de esta pantalla.
        es_obligatorio_nacional = str(fila.get("PROCEDENCIA_OBLIGATORIA", "")).strip().lower() == "nacional"
        cod_a_comprar = cod_equiv if cod_equiv else (cod_obl if es_obligatorio_nacional else None)

        if cod_a_comprar is None:
            productos.append({
                "familia":            fila["FAMILIA"],
                "subfamilia":         fila["SUBFAMILIA"],
                "grupo":              fila.get("GRUPO") if pd.notna(fila.get("GRUPO")) else None,
                "descripcion":        fila["DESCRIPCION"],
                "codigo_a_comprar":   None,
                "embalaje":           None,
                "cantidad_a_comprar": None,
                "sin_opcion_nacional": True,
            })
            continue

        fila_comprar = stock_por_codigo.get(cod_a_comprar)

        venta_consolidada = _venta_combinada(fila_stock_obl, fila_stock_equiv, dli.VENTA_MENSUAL_TODAS)
        stock_total = sum(
            _stock_combinado(fila_stock_obl, fila_stock_equiv, stock_col, transito_col)
            for _, stock_col, transito_col, _ in SUCURSALES_CRITICAS
        )

        # MESES_OBJETIVO es opcional por producto -- si la planilla no
        # trae valor (caso normal) se usa el default de 2 meses. Sirve
        # para casos como MatixGo Gris, que se pide bajo pedido y no
        # amerita el mismo colchon que los colores de alta rotacion.
        meses_col = fila.get("MESES_OBJETIVO")
        meses_objetivo = float(meses_col) if pd.notna(meses_col) else meses_default

        # Colchon de lead time: dias habiles de venta que se van a
        # consumir mientras la OC esta en camino, sumados encima del
        # objetivo de meses (aplica igual sea el default o la
        # excepcion por producto).
        colchon_lead_time = (venta_consolidada / DIAS_HABILES_MES) * LEAD_TIME_DIAS_NACIONAL

        # Se resta lo que ya esta pedido al proveedor del codigo que se
        # va a comprar (Nacional) -- esa mercaderia ya esta comprada,
        # solo falta que llegue, asi que no corresponde sugerir
        # comprarla de nuevo. No cuenta el pedido del Importado (ver
        # _pedido_nacional).
        pedido_total = _pedido_nacional(fila_comprar)

        objetivo_empresa = (meses_objetivo * venta_consolidada) + colchon_lead_time
        necesario = max(0.0, objetivo_empresa - stock_total - pedido_total)

        embalaje = int(fila_comprar["EMBALAJE"]) if (fila_comprar is not None and pd.notna(fila_comprar.get("EMBALAJE")) and fila_comprar["EMBALAJE"] > 0) else 1
        cantidad_a_comprar = math.ceil(necesario / embalaje) * embalaje if necesario > 0 else 0

        productos.append({
            "familia":            fila["FAMILIA"],
            "subfamilia":         fila["SUBFAMILIA"],
            "grupo":              fila.get("GRUPO") if pd.notna(fila.get("GRUPO")) else None,
            "descripcion":        fila["DESCRIPCION"],
            "codigo_a_comprar":   int(cod_a_comprar),
            "embalaje":           embalaje,
            "cantidad_a_comprar": cantidad_a_comprar,
            "sin_opcion_nacional": False,
        })

    productos.sort(key=lambda p: -(p["cantidad_a_comprar"] or 0))

    return {
        "productos": productos,
        "resumen": {
            "total_obligatorios":   len(productos),
            "con_necesidad_compra": sum(1 for p in productos if (p["cantidad_a_comprar"] or 0) > 0),
            "sin_opcion_nacional":  sum(1 for p in productos if p["sin_opcion_nacional"]),
        },
    }


# Niveles fijos que se muestran como referencia rapida al lado del
# selector de meses en Plan de Compra -- para comparar el costo total
# a 1 / 1.5 / 2 meses sin tener que cambiar el selector varias veces.
NIVELES_COMPARACION_MESES = [1.0, 1.5, 2.0]


def get_resumen_valor_compra(familia=None):
    """Valor total ($) de la compra sugerida a cada nivel fijo de
    NIVELES_COMPARACION_MESES, respetando el filtro de familia y las
    excepciones de MESES_OBJETIVO por producto (get_plan_compra_reposicion
    ya las aplica). Usa CUP (costo unitario promedio) de Inventario.xlsx
    para convertir cantidad a $. Solo suma productos con necesidad real
    de compra -- deja fuera "sin necesidad" y "sin opcion Nacional"."""
    df_inv = dli._leer_inventario()
    precio_por_codigo = dict(zip(df_inv["CODIGO"], df_inv["CUP"]))

    niveles = []
    for meses in NIVELES_COMPARACION_MESES:
        resultado = get_plan_compra_reposicion(familia, meses)
        valor_total = sum(
            p["cantidad_a_comprar"] * precio_por_codigo.get(p["codigo_a_comprar"], 0)
            for p in resultado["productos"]
            if not p["sin_opcion_nacional"] and (p["cantidad_a_comprar"] or 0) > 0
        )
        niveles.append({"meses": meses, "valor": round(valor_total, 0)})

    return {"niveles": niveles}


def exportar_plan_compras_excel(familia=None, meses_objetivo_default=None):
    """
    Genera un Excel en memoria con los SKU que realmente hay que
    comprar (cantidad_a_comprar > 0) -- deja fuera los que ya estan
    bien ("Sin necesidad") y los que no tienen opcion Nacional
    (no hay codigo que pedir hoy). Devuelve un BytesIO listo para
    mandar como descarga.
    """
    resultado = get_plan_compra_reposicion(familia, meses_objetivo_default)
    filas = [
        {
            "Familia":            p["familia"],
            "Subfamilia":         p["subfamilia"],
            "Grupo":              p["grupo"] or "",
            "Descripcion":        p["descripcion"],
            "Codigo a Comprar":   p["codigo_a_comprar"],
            "Embalaje":           p["embalaje"],
            "Cantidad a Comprar": p["cantidad_a_comprar"],
        }
        for p in resultado["productos"]
        if not p["sin_opcion_nacional"] and (p["cantidad_a_comprar"] or 0) > 0
    ]
    df = pd.DataFrame(filas, columns=[
        "Familia", "Subfamilia", "Grupo", "Descripcion",
        "Codigo a Comprar", "Embalaje", "Cantidad a Comprar",
    ])

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Plan de Compra")
    buffer.seek(0)
    return buffer
