"""
Backfill (reejecutable) de productos_obligatorios desde
Productos_Obligatorios.xlsx -- tabla chica (~530 filas), upsert simple
sin necesidad del patron de carga por lotes de ventas/inventario_stock.

Uso: python scripts/backfill_obligatorios.py
"""
import os
import sys
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import data_loader_obligatorios as do
import db


def cargar_productos_obligatorios(df=None):
    if df is None:
        df = do._leer_productos_obligatorios()

    filas = []
    for _, r in df.iterrows():
        filas.append((
            int(r["CODIGO_OBLIGATORIO"]),
            r["FAMILIA"],
            r["SUBFAMILIA"],
            r["GRUPO"] if pd.notna(r.get("GRUPO")) else None,
            r["DESCRIPCION"],
            r["PROCEDENCIA_OBLIGATORIA"],
            int(r["CODIGO_EQUIVALENTE"]) if pd.notna(r.get("CODIGO_EQUIVALENTE")) else None,
            float(r["MESES_OBJETIVO"]) if pd.notna(r.get("MESES_OBJETIVO")) else None,
        ))

    sql = """
        insert into productos_obligatorios
            (codigo_obligatorio, familia, subfamilia, grupo, descripcion,
             procedencia_obligatoria, codigo_equivalente, meses_objetivo)
        values (%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (codigo_obligatorio) do update set
            familia=excluded.familia, subfamilia=excluded.subfamilia, grupo=excluded.grupo,
            descripcion=excluded.descripcion, procedencia_obligatoria=excluded.procedencia_obligatoria,
            codigo_equivalente=excluded.codigo_equivalente, meses_objetivo=excluded.meses_objetivo,
            updated_at=now()
    """
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select codigo_obligatorio from productos_obligatorios")
            existentes = {row["codigo_obligatorio"] for row in cur.fetchall()}
            nuevos = {f[0] for f in filas}
            a_borrar = existentes - nuevos
            if a_borrar:
                cur.execute(
                    "delete from productos_obligatorios where codigo_obligatorio = any(%s)",
                    (list(a_borrar),),
                )
            cur.executemany(sql, filas)
        conn.commit()
    print(f"  {len(filas)} productos obligatorios cargados/actualizados"
          f"{f', {len(a_borrar)} eliminados (ya no estan en el Excel)' if a_borrar else ''}.")


def main():
    cargar_productos_obligatorios()
    print("Backfill de Productos Obligatorios completo.")


if __name__ == "__main__":
    main()
