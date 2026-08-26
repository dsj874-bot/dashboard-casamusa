"""
Carga inicial Compras/Recepciones (Excel) -> Postgres (compras,
recepciones). Reusa las funciones de data_loader_adquisiciones.py
(deteccion de hoja, exclusion de PROVEEDORES_EXCLUIDOS) para no
duplicar esa logica -- este script solo aplana y sube.

Mismo patron de batch-commit-con-reintento que
scripts/backfill_inventario.py: el pooler de Supabase en modo
transaccion puede cortar una conexion larga a mitad de camino, asi
que TRUNCATE va en su propia transaccion corta y el INSERT se hace en
lotes, cada uno con su propio commit.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import db
import data_loader_pg as dlpg
import data_loader_adquisiciones as da

COLUMNAS_COMPRAS = [
    "n_orden_compra", "codigo", "fecha_creacion", "rut", "nombre_proveedor",
    "id_procedencia", "marca", "referencia", "descripcion", "cantidad_comprada",
    "cup", "desviacion", "precio_unitario", "precio_total", "cantidad_pendiente",
    "total_pendiente", "tipo_compra", "sucursal", "cod_pago",
]
_MAPA_COMPRAS = {
    "n_orden_compra": "N_ORDEN_COMPRA", "codigo": "CODIGO", "fecha_creacion": "FECHA_CREACION",
    "rut": "RUT", "nombre_proveedor": "NOMBRE_PROVEEDOR", "id_procedencia": "ID_PROCEDENCIA",
    "marca": "MARCA", "referencia": "REFERENCIA", "descripcion": "DESCRIPCION",
    "cantidad_comprada": "CANTIDAD_COMPRADA", "cup": "CUP", "desviacion": "DESVIACION",
    "precio_unitario": "PRECIO_UNITARIO", "precio_total": "PRECIO_TOTAL",
    "cantidad_pendiente": "CANTIDAD_PENDIENTE", "total_pendiente": "TOTAL_PENDIENTE",
    "tipo_compra": "TIPO_COMPRA", "sucursal": "SUCURSAL", "cod_pago": "COD_PAGO",
}

COLUMNAS_RECEPCIONES = [
    "n_recepcion", "n_oc", "codigo", "fecha_recepcion", "rut_proveedor", "nombre_proveedor",
    "id_procedencia", "referencia", "descripcion", "tipo_oc", "comentario_oc", "u_m",
    "cantidad", "precio", "total", "precio_clp", "total_clp", "cond_pago", "sucursal", "num_semana",
]
_MAPA_RECEPCIONES = {
    "n_recepcion": "N_RECEPCION", "n_oc": "N_OC", "codigo": "CODIGO",
    "fecha_recepcion": "FECHA_RECEPCION", "rut_proveedor": "RUT_PROVEEDOR",
    "nombre_proveedor": "NOMBRE_PROVEEDOR", "id_procedencia": "ID_PROCEDENCIA",
    "referencia": "REFERENCIA", "descripcion": "DESCRIPCION", "tipo_oc": "TIPO_OC",
    "comentario_oc": "COMENTARIO_OC", "u_m": "U_M", "cantidad": "CANTIDAD", "precio": "PRECIO",
    "total": "TOTAL", "precio_clp": "PRECIO_CLP", "total_clp": "TOTAL_CLP",
    "cond_pago": "COND_PAGO", "sucursal": "SUCURSAL", "num_semana": "NUM_SEMANA",
}


def _lotes(seq, tamano):
    for i in range(0, len(seq), tamano):
        yield seq[i:i + tamano]


def _insertar_filas(tabla, columnas_pg, mapa, ano, df, tamano_lote=5000, reintentos=3):
    """Solo el INSERT por lotes con commit-y-reintento (ver Gotcha
    Postgres #2 en CLAUDE.md) -- el caller decide que se borra antes."""
    cols_sql = ", ".join(["ano"] + columnas_pg)
    placeholders = ", ".join(["%s"] * (len(columnas_pg) + 1))
    sql = f"insert into {tabla} ({cols_sql}) values ({placeholders})"

    filas = [
        tuple([ano] + [dlpg.valor_sql(row.get(mapa[c])) for c in columnas_pg])
        for row in df.to_dict("records")
    ]
    print(f"  {tabla} {ano}: {len(filas)} filas")

    total = 0
    conn = db.get_connection()
    try:
        for lote in _lotes(filas, tamano_lote):
            for intento in range(1, reintentos + 1):
                try:
                    with conn.cursor() as cur:
                        cur.executemany(sql, lote)
                    conn.commit()
                    total += len(lote)
                    break
                except Exception as e:
                    print(f"    lote fallo (intento {intento}/{reintentos}): {e}")
                    try:
                        conn.close()
                    except Exception:
                        pass
                    if intento == reintentos:
                        raise
                    conn = db.get_connection()
    finally:
        conn.close()

    print(f"  {tabla} {ano}: {total} filas cargadas.")
    return total


def cargar_ano_pg(tabla, columnas_pg, mapa, ano, df, tamano_lote=5000, reintentos=3):
    """Reemplaza TODAS las filas de un año puntual (DELETE WHERE ano=X +
    INSERT). Uso: carga inicial completa / re-extraccion completa del
    año desde SAP. NO usar para una subida web incremental -- si el
    archivo no trae el año completo, esto borra todo lo que no venia
    en el archivo (bug real 2026-08-26: una subida de solo Agosto borro
    Enero-Julio enteros). Para eso, usar cargar_incremental_pg."""
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"delete from {tabla} where ano = %s", (ano,))
        conn.commit()
    conn.close()

    return _insertar_filas(tabla, columnas_pg, mapa, ano, df, tamano_lote, reintentos)


def cargar_incremental_pg(tabla, columnas_pg, mapa, clave_pg, clave_excel, ano, df, tamano_lote=5000, reintentos=3):
    """Sube/actualiza SOLO las filas cuya clave natural (N_ORDEN_COMPRA
    para compras, N_RECEPCION para recepciones) aparece en el archivo
    subido -- borra esas claves puntuales y las vuelve a insertar,
    dejando todo lo demas del año intacto. Mismo patron de "sync solo
    lo que trae el archivo" que ya usa Ventas (sincronizar_ventas_pg,
    que sincroniza por dia en vez de por año completo). Pensado para
    subidas web recurrentes (agregar las OC/recepciones nuevas del
    mes), no para la carga inicial (ver cargar_ano_pg)."""
    claves = df[clave_excel].dropna().unique().tolist()
    if not claves:
        print(f"  {tabla} {ano}: archivo sin filas, nada que hacer.")
        return 0

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"delete from {tabla} where ano = %s and {clave_pg} = ANY(%s)", (ano, claves))
            print(f"  {tabla} {ano}: {cur.rowcount} filas viejas reemplazadas ({len(claves)} claves en el archivo).")
        conn.commit()
    conn.close()

    return _insertar_filas(tabla, columnas_pg, mapa, ano, df, tamano_lote, reintentos)


def cargar_compras(ano, df):
    """Usada por /api/subir_compras (subida web) -- incremental: solo
    toca las OC que vienen en el archivo."""
    return cargar_incremental_pg("compras", COLUMNAS_COMPRAS, _MAPA_COMPRAS, "n_orden_compra", "N_ORDEN_COMPRA", ano, df)


def cargar_recepciones(ano, df):
    """Usada por /api/subir_recepciones (subida web) -- incremental:
    solo toca las recepciones que vienen en el archivo."""
    return cargar_incremental_pg("recepciones", COLUMNAS_RECEPCIONES, _MAPA_RECEPCIONES, "n_recepcion", "N_RECEPCION", ano, df)


def cargar_compras_completo(ano, df):
    """Carga inicial / re-extraccion completa del año -- reemplaza TODO
    el año. Usada solo por el __main__ de este script, no por la web."""
    return cargar_ano_pg("compras", COLUMNAS_COMPRAS, _MAPA_COMPRAS, ano, df)


def cargar_recepciones_completo(ano, df):
    """Carga inicial / re-extraccion completa del año -- reemplaza TODO
    el año. Usada solo por el __main__ de este script, no por la web."""
    return cargar_ano_pg("recepciones", COLUMNAS_RECEPCIONES, _MAPA_RECEPCIONES, ano, df)


if __name__ == "__main__":
    print("Cargando Compras...")
    cargar_compras_completo(2025, da.get_df_2025())
    cargar_compras_completo(2026, da.get_df_2026())
    print("Cargando Recepciones...")
    cargar_recepciones_completo(2025, da.get_df_recepciones_2025())
    cargar_recepciones_completo(2026, da.get_df_recepciones_2026())
    print("Listo.")
