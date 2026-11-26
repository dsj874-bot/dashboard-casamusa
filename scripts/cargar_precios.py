"""
Carga la lista de precios de SAP (export "Precio_Venta") a la tabla
precio_lista (migracion 028). Toma el archivo mas reciente de
data/comercial/Precios/*/*_Precio_Venta.xlsx, o el que se pase como argumento.

Cada carga REEMPLAZA la anterior (es una foto de las listas vigentes).

    python scripts/cargar_precios.py
    python scripts/cargar_precios.py "data/comercial/Precios/11 Nov-26/2611_Precio_Venta.xlsx"
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

import db  # noqa: E402

COLUMNAS = ["LP_BASE_CM", "LP_BASE_CMD", "LP_D1_CM", "LP_D2_CM", "LP_D3_CM", "LP_D4_CM"]


def _archivo():
    if len(sys.argv) > 1:
        return sys.argv[1]
    archivos = glob.glob("data/comercial/Precios/*/*_Precio_Venta.xlsx")
    if not archivos:
        raise SystemExit("No hay archivos *_Precio_Venta.xlsx en data/comercial/Precios/")
    return max(archivos, key=lambda p: os.path.basename(p)[:4])   # AAMM al inicio del nombre


def main():
    ruta = _archivo()
    df = pd.read_excel(ruta)
    faltan = [c for c in ["CODIGO_CM"] + COLUMNAS if c not in df.columns]
    if faltan:
        raise SystemExit(f"Al archivo le faltan columnas: {faltan}")
    df = df[pd.to_numeric(df["CODIGO_CM"], errors="coerce").notna()].copy()
    df["CODIGO_CM"] = df["CODIGO_CM"].astype("int64")
    for c in COLUMNAS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    filas = [tuple([int(r.CODIGO_CM)] + [None if pd.isna(getattr(r, c)) else float(getattr(r, c)) for c in COLUMNAS]
                   + [os.path.basename(ruta)]) for r in df.itertuples(index=False)]
    # COPY: un solo envio para las ~19.000 filas (con INSERT por lotes contra Oregon tomaba minutos).
    # Va a una tabla temporal y se reemplaza en una transaccion corta: nunca queda la lista a medias.
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE precio_carga (LIKE precio_lista INCLUDING DEFAULTS) ON COMMIT DROP")
            with cur.copy("COPY precio_carga (codigo, lp_base_cm, lp_base_cmd, lp_d1_cm, lp_d2_cm, lp_d3_cm, lp_d4_cm, archivo) FROM STDIN") as cp:
                for f in filas:
                    cp.write_row(f)
            cur.execute("DELETE FROM precio_lista")
            cur.execute("INSERT INTO precio_lista SELECT * FROM precio_carga")
        conn.commit()
    print(f"{len(filas)} códigos cargados desde {ruta}")


if __name__ == "__main__":
    main()
