"""
Consolida el archivo mensual de ventas (data/AAMM_Vtas.xlsx) sin
necesidad de que la app Flask este corriendo.

Pensado para ejecutarse como Tarea Programada de Windows (no requiere
que iniciar.bat este abierto), todos los dias a las 19:00.

Si hay archivo nuevo, lo consolida (igual que el boton "Actualizar
datos"). Si NO hay archivo (habil o no), confirma el dia como "sin
ventas, dato final" para que el corte de comparaciones avance igual
-- si ese dia si tuvo venta real, subir el archivo despues corrige el
numero real (la fecha confirmada solo fija el tope de corte).
"""
import os
import sys
import shutil
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import data_loader

LOG_PATH    = os.path.join(BASE_DIR, "actualizar_diario.log")
BACKUP_DIR  = r"C:\Users\Marcelo\OneDrive\CasaMusa_Dashboard_Backup"
EXCLUIR_EXT = (".log", ".procesando", ".done")


def _respaldar_datos():
    """Copia los archivos de data/ (Excel/parquet/metas/presupuesto) a
    OneDrive -- para no perderlo todo si el PC falla, se pierde o lo
    roban. Corre todos los dias junto con la consolidacion, sin
    importar si esta encontro archivo o no."""
    origen = os.path.join(BASE_DIR, "data")
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        copiados = 0
        for nombre in os.listdir(origen):
            ruta = os.path.join(origen, nombre)
            if os.path.isfile(ruta) and not nombre.endswith(EXCLUIR_EXT):
                shutil.copy2(ruta, os.path.join(BACKUP_DIR, nombre))
                copiados += 1
        return {"ok": True, "msg": f"Respaldo OK: {copiados} archivos copiados a OneDrive."}
    except Exception as e:
        return {"ok": False, "msg": f"Respaldo fallo: {e}"}


def main():
    resultado = data_loader.actualizar_desde_archivo_mensual()
    lineas = [f"{datetime.now():%Y-%m-%d %H:%M:%S} - {resultado.get('msg')}"]

    if not resultado.get("ok"):
        confirmacion = data_loader.confirmar_dia_sin_ventas()
        lineas.append(f"{datetime.now():%Y-%m-%d %H:%M:%S} - {confirmacion.get('msg')}")

    respaldo = _respaldar_datos()
    lineas.append(f"{datetime.now():%Y-%m-%d %H:%M:%S} - {respaldo.get('msg')}")

    texto = "\n".join(lineas) + "\n"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(texto)
    print(texto.strip())


if __name__ == "__main__":
    main()
