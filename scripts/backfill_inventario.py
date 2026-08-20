"""
Backfill (reejecutable) del dominio Inventario: carga Inventario.xlsx +
Datos_Duros_Inventario.xlsx (via data_loader_inventario._leer_inventario(),
mismo merge que ya usa el dashboard local) a productos + inventario_stock.

inventario_stock se TRUNCATE + reinserta completo cada vez -- es una foto,
no un historial (igual que el Excel: se reemplaza entero, no se acumula).

Uso: python scripts/backfill_inventario.py
"""
import os
import sys
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import data_loader_inventario as dli
import data_loader_pg as dlpg
import db

BODEGAS_TODAS = dli.BODEGAS + [("Servicio Técnico", None, "TRANSITO SERVICIO TECNICO")]


def cargar_productos(df):
    print("Cargando dimension productos desde Inventario...")
    cols = ["CODIGO", "REFERENCIA", "DESCRIPCION", "U_M", "FAMILIA", "SUBFAMILIA",
            "GRUPO", "MARCA", "ID_PROCEDENCIA", "EMBALAJE", "MULTIPLO", "CUP",
            "CLAS_SI", "CLAS_LC", "CLAS_MR", "CLAS_MT", "CLAS_CSD"]
    for c in cols:
        if c not in df.columns:
            df[c] = None

    filas = [
        tuple(dlpg.valor_sql(v) for v in row)
        for row in df[cols].itertuples(index=False, name=None)
    ]
    # FAMILIA/SUBFAMILIA/GRUPO solo vienen de Datos_Duros_Inventario.xlsx
    # (no del export SAP crudo -- ver data_loader_inventario._leer_inventario).
    # La subida web (/api/subir_inventario) solo sube el export SAP, sin ese
    # archivo, asi que aqui van con COALESCE: si vienen null, se conserva el
    # valor que ya estaba en Postgres en vez de borrarlo.
    sql = """
        insert into productos (codigo, referencia, descripcion, um, familia, subfamilia,
            grupo, marca, id_procedencia, embalaje, multiplo, cup, clas_si, clas_lc, clas_mr, clas_mt, clas_csd)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (codigo) do update set
            referencia=excluded.referencia, descripcion=excluded.descripcion, um=excluded.um,
            familia=coalesce(excluded.familia, productos.familia),
            subfamilia=coalesce(excluded.subfamilia, productos.subfamilia),
            grupo=coalesce(excluded.grupo, productos.grupo),
            marca=excluded.marca, id_procedencia=excluded.id_procedencia, embalaje=excluded.embalaje,
            multiplo=excluded.multiplo, cup=excluded.cup, clas_si=excluded.clas_si, clas_lc=excluded.clas_lc,
            clas_mr=excluded.clas_mr, clas_mt=excluded.clas_mt, clas_csd=excluded.clas_csd, updated_at=now()
    """
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            for i in range(0, len(filas), 5000):
                cur.executemany(sql, filas[i:i + 5000])
        conn.commit()
    print(f"  {len(filas)} productos cargados/actualizados.")


def construir_stock_largo(df):
    """Reshape ancho (columnas STOCK_X/TRANSITO_X) -> largo (codigo, bodega)
    -- vectorizado, no itera fila por fila."""
    partes = []
    for nombre, stock_col, transito_col in BODEGAS_TODAS:
        venta_col = dli.VENTA_MENSUAL_COL.get(nombre)
        sub = pd.DataFrame({"codigo": df["CODIGO"]})
        sub["bodega"] = nombre
        sub["stock"] = df[stock_col] if stock_col and stock_col in df.columns else None
        sub["transito"] = df[transito_col] if transito_col and transito_col in df.columns else None
        sub["venta_mensual"] = df[venta_col] if venta_col and venta_col in df.columns else None
        partes.append(sub)

    # Fila sintetica "Todas": solo trae venta mensual CONSOLIDADA (un
    # numero propio, no la suma de las 5 bodegas con columna propia) --
    # stock/transito de "Todas" se calculan sumando las bodegas reales en
    # SQL al consultar, no se duplican aqui.
    if dli.VENTA_MENSUAL_TODAS in df.columns:
        todas = pd.DataFrame({
            "codigo": df["CODIGO"], "bodega": "Todas",
            "stock": None, "transito": None,
            "venta_mensual": df[dli.VENTA_MENSUAL_TODAS],
        })
        partes.append(todas)

    return pd.concat(partes, ignore_index=True)


def _lotes(seq, tamano):
    for i in range(0, len(seq), tamano):
        yield seq[i:i + tamano]


def cargar_stock(df, archivo_origen="backfill_inicial", tamano_lote=5000, reintentos=3):
    """Carga por lotes con COMMIT por lote y reintento con reconexion si
    un lote falla (mismo patron que backfill_ventas() en
    backfill_fase1_comercial.py -- el pooler de Supabase en modo
    transaccion puede cortar una conexion larga a mitad de camino).

    TRUNCATE va en su PROPIA transaccion corta (commit inmediato) --
    si el TRUNCATE y las ~250k filas de insert quedaran en una sola
    transaccion larga, el ACCESS EXCLUSIVE lock del TRUNCATE bloquea
    CUALQUIER lectura de la tabla (incluido un simple count(*) desde
    otra sesion) durante todo el rato que tarde la carga completa --
    ya paso una vez (carga colgada +30 min, bloqueando diagnostico)."""
    largo = construir_stock_largo(df)

    # venta_mensual solo viene de Datos_Duros_Inventario.xlsx (ver
    # construir_stock_largo) -- una subida web (solo el export SAP, sin
    # ese archivo) llega con venta_mensual en None para todo. Como esta
    # funcion hace TRUNCATE mas abajo, un COALESCE contra la fila vieja en
    # el UPDATE no sirve (la fila ya no existe para cuando corre el
    # UPDATE) -- por eso se rescata ANTES del truncate y se rellena aqui
    # en Python.
    with db.get_connection() as conn0:
        with conn0.cursor() as cur:
            cur.execute("select codigo, bodega, venta_mensual from inventario_stock where venta_mensual is not null")
            venta_previa = {(codigo, bodega): venta for codigo, bodega, venta in cur.fetchall()}
    if venta_previa:
        faltantes = largo["venta_mensual"].isna()
        if faltantes.any():
            claves = list(zip(largo.loc[faltantes, "codigo"], largo.loc[faltantes, "bodega"]))
            largo.loc[faltantes, "venta_mensual"] = [venta_previa.get(k) for k in claves]

    filas = [
        tuple(dlpg.valor_sql(v) for v in row)
        for row in largo[["codigo", "bodega", "stock", "transito", "venta_mensual"]].itertuples(index=False, name=None)
    ]
    print(f"Cargando inventario_stock ({len(filas)} filas: {len(df)} productos x {len(BODEGAS_TODAS) + 1} bodegas)...")

    sql = """
        insert into inventario_stock (codigo, bodega, stock, transito, venta_mensual)
        values (%s,%s,%s,%s,%s)
        on conflict (codigo, bodega) do update set
            stock=excluded.stock, transito=excluded.transito, venta_mensual=excluded.venta_mensual
    """

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("truncate inventario_stock")
        conn.commit()
    conn.close()

    conn = db.get_connection()
    total = 0
    try:
        for lote in _lotes(filas, tamano_lote):
            for intento in range(1, reintentos + 1):
                try:
                    with conn.cursor() as cur:
                        cur.executemany(sql, lote)
                    conn.commit()
                    break
                except Exception as e:
                    print(f"  lote fallo (intento {intento}/{reintentos}): {e}")
                    try:
                        conn.close()
                    except Exception:
                        pass
                    if intento == reintentos:
                        raise
                    conn = db.get_connection()
            total += len(lote)
            print(f"  {total}/{len(filas)} filas insertadas...")
    finally:
        conn.close()

    with db.get_connection() as conn2:
        with conn2.cursor() as cur:
            cur.execute(
                "insert into inventario_control (id, archivo_origen, filas) values (true, %s, %s) "
                "on conflict (id) do update set archivo_origen=excluded.archivo_origen, "
                "cargado_en=now(), filas=excluded.filas",
                (archivo_origen, len(df)),
            )
        conn2.commit()
    print(f"  {len(filas)} filas de inventario_stock cargadas.")


def main():
    df = dli._leer_inventario()
    cargar_productos(df.copy())
    cargar_stock(df)
    print("Backfill de Inventario completo.")


if __name__ == "__main__":
    main()
