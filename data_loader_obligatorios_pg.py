"""
Version Postgres de data_loader_obligatorios.py -- por ahora solo
Alertas de Quiebre Critico (Distribucion desde San Isidro y Plan de
Compra/Reposicion quedan para despues, mismo orden acordado que el
resto de Inventario).

Misma logica exacta que el original: en vez de leer todo
Inventario.xlsx a un DataFrame gigante (dli._leer_inventario()), aca
se consulta Postgres solo por los codigos que realmente hacen falta
(los obligatorios + sus equivalentes -- unos pocos cientos de los
~19.000 codigos que existen en total).
"""
import db
import data_loader_inventario as dli
import data_loader_obligatorios as do


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
