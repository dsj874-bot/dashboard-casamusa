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


def cargar_ano_pg(tabla, columnas_pg, mapa, ano, df, tamano_lote=5000, reintentos=3):
    """Reemplaza SOLO las filas de un año puntual (DELETE WHERE ano=X +
    INSERT) -- reusable tanto por la carga inicial completa (llamada una
    vez por año) como por una subida web futura de un año especifico,
    sin arriesgar borrar el otro año por error. Mismo patron de
    commit-por-lote-con-reintento que el resto del proyecto (ver Gotcha
    Postgres #2 en CLAUDE.md)."""
    cols_sql = ", ".join(["ano"] + columnas_pg)
    placeholders = ", ".join(["%s"] * (len(columnas_pg) + 1))
    sql = f"insert into {tabla} ({cols_sql}) values ({placeholders})"

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"delete from {tabla} where ano = %s", (ano,))
        conn.commit()
    conn.close()

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


def cargar_compras(ano, df):
    return cargar_ano_pg("compras", COLUMNAS_COMPRAS, _MAPA_COMPRAS, ano, df)


def cargar_recepciones(ano, df):
    return cargar_ano_pg("recepciones", COLUMNAS_RECEPCIONES, _MAPA_RECEPCIONES, ano, df)


if __name__ == "__main__":
    print("Cargando Compras...")
    cargar_compras(2025, da.get_df_2025())
    cargar_compras(2026, da.get_df_2026())
    print("Cargando Recepciones...")
    cargar_recepciones(2025, da.get_df_recepciones_2025())
    cargar_recepciones(2026, da.get_df_recepciones_2026())
    print("Listo.")
