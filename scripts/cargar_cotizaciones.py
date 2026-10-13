"""Carga a Postgres los reportes de cotizaciones del ERP que esten en
data/comercial/Cotizaciones/MMYY_Cotizaciones.xlsx (o los que se pasen
como argumento). Usa las mismas funciones que la subida web
(/subir_cotizaciones), asi que volver a correrlo no duplica: reemplaza
cada cotizacion por N de cotizacion.

Uso:  python scripts/cargar_cotizaciones.py [archivo.xlsx ...]
"""
import glob
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data_loader_cotizaciones_pg as dlc  # noqa: E402

CARPETA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "comercial", "Cotizaciones")
FILAS_POR_PARTE = 5000


def cargar_archivo(ruta):
    t0 = time.time()
    df = pd.read_excel(ruta, engine="calamine")
    faltan = [c for c in dlc.COLUMNAS_ERP if c not in df.columns]
    if faltan:
        raise SystemExit(f"{ruta}: faltan columnas {faltan}")
    df = df[dlc.COLUMNAS_ERP].astype(object).where(df[dlc.COLUMNAS_ERP].notna(), None)
    filas = dlc.normalizar_filas(df.values.tolist())
    # Partes de ~5000 filas sin cortar una cotizacion por la mitad
    filas.sort(key=lambda f: f[0])
    total_cot, i = 0, 0
    while i < len(filas):
        j = min(i + FILAS_POR_PARTE, len(filas))
        while j < len(filas) and filas[j][0] == filas[j - 1][0]:
            j += 1
        total_cot += dlc.cargar_parte(filas[i:j])
        i = j
    fechas = [f[1] for f in filas]
    dlc.registrar_carga(os.path.basename(ruta), len(filas), total_cot, min(fechas), max(fechas), "script")
    print(f"{os.path.basename(ruta)}: {len(filas)} lineas, {total_cot} cotizaciones, "
          f"{min(fechas)} a {max(fechas)} ({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    archivos = sys.argv[1:] or sorted(glob.glob(os.path.join(CARPETA, "*_Cotizaciones.xlsx")))
    for a in archivos:
        cargar_archivo(a)
