"""
Nivel de Servicio: mide si el stock actual alcanza para cubrir la
demanda -- general y por sucursal. Usa el mismo "fill rate" que se usa
en la industria (sum(min(stock,demanda)) / sum(demanda)), medido solo
sobre Productos Prioritarios (productos_obligatorios) -- no tiene
sentido medir esto sobre todo el catalogo, la mayoria no tiene
demanda diaria real.

San Isidro se trata como una sucursal independiente mas (solo su
propia venta local, sin el ajuste de "CD reabastece a las demas" que
usan Alertas/Distribucion) -- pedido explicito del usuario para este
reporte especificamente.

Inventario Total ($) por sucursal es un dato distinto (todo el
catalogo, no solo Prioritarios) -- se reusa _stock_por_bodega_pg, ya
calculado y probado en data_loader_inventario_pg.py.
"""
import db
import data_loader_inventario as dli
import data_loader_obligatorios as do
import data_loader_obligatorios_pg as dopg
import data_loader_inventario_pg as dipg


def get_nivel_servicio_pg():
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            obligatorios = dopg._cargar_obligatorios_pg(cur)

            codigos_necesarios = set()
            for fila in obligatorios:
                codigos_necesarios.add(fila["codigo_obligatorio"])
                if fila["codigo_equivalente"] is not None:
                    codigos_necesarios.add(fila["codigo_equivalente"])

            sucursales = [n for n, _, _, _ in do.SUCURSALES_CRITICAS]
            datos = dopg._cargar_stock_pg(cur, codigos_necesarios, sucursales)

            por_bodega_raw = dipg._stock_por_bodega_pg(cur)

    cubierto_por_suc = {suc: 0.0 for suc in sucursales}
    demanda_por_suc = {suc: 0.0 for suc in sucursales}

    for fila in obligatorios:
        cod_obl = fila["codigo_obligatorio"]
        cod_equiv = fila["codigo_equivalente"]

        for suc in sucursales:
            stock_combinado = dopg._stock_combinado_pg(datos, cod_obl, cod_equiv, suc)

            # Sin el ajuste de CD para San Isidro -- solo su propia
            # venta local, igual que cualquier otra sucursal. Si alguna
            # sucursal no tiene columna de venta mensual propia
            # (dli.VENTA_MENSUAL_COL), demanda queda en 0 -- no aporta
            # ni resta nada (no hace falta excluirla a mano).
            tiene_venta = suc in dli.VENTA_MENSUAL_COL
            venta_combinada = dopg._venta_combinada_pg(datos, cod_obl, cod_equiv, suc) if tiene_venta else 0.0
            demanda_diaria = venta_combinada / 30

            cubierto_por_suc[suc] += min(stock_combinado, demanda_diaria)
            demanda_por_suc[suc] += demanda_diaria

    def _nivel(cubierto, demanda):
        return round(cubierto / demanda * 100, 1) if demanda > 0 else None

    por_sucursal = []
    for suc in sucursales:
        valor_stock = float(por_bodega_raw[suc]["valor_stock"]) if suc in por_bodega_raw else 0.0
        por_sucursal.append({
            "sucursal":        suc,
            "valor_inventario": round(valor_stock, 0),
            "nivel_servicio":  _nivel(cubierto_por_suc[suc], demanda_por_suc[suc]),
        })
    por_sucursal.sort(key=lambda r: -r["valor_inventario"])

    cubierto_total = sum(cubierto_por_suc.values())
    demanda_total = sum(demanda_por_suc.values())
    valor_inventario_total = sum(r["valor_inventario"] for r in por_sucursal)

    return {
        "general": {
            "valor_inventario": round(valor_inventario_total, 0),
            "nivel_servicio":   _nivel(cubierto_total, demanda_total),
        },
        "por_sucursal": por_sucursal,
    }


def guardar_snapshot_diario_pg():
    """Guarda en nivel_servicio_historico el estado actual (general +
    por sucursal) para la fecha de HOY -- pensado para llamarse una vez
    al dia desde un cron (ver /api/cron/nivel_servicio_snapshot en
    app.py). UPSERT por (fecha, sucursal): si el cron corriera dos
    veces el mismo dia (o el snapshot se dispara a mano de nuevo), no
    duplica fila -- pisa con el valor mas reciente de ese dia."""
    d = get_nivel_servicio_pg()

    filas = [("TOTAL", d["general"]["valor_inventario"], d["general"]["nivel_servicio"])]
    filas += [(r["sucursal"], r["valor_inventario"], r["nivel_servicio"]) for r in d["por_sucursal"]]

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO nivel_servicio_historico (fecha, sucursal, valor_inventario, nivel_servicio)
                   VALUES (CURRENT_DATE, %s, %s, %s)
                   ON CONFLICT (fecha, sucursal) DO UPDATE SET
                       valor_inventario = excluded.valor_inventario,
                       nivel_servicio   = excluded.nivel_servicio,
                       capturado_en     = now()""",
                filas,
            )
        conn.commit()

    return {"filas_guardadas": len(filas)}
