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
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import data_loader

LOG_PATH = os.path.join(BASE_DIR, "actualizar_diario.log")


def main():
    resultado = data_loader.actualizar_desde_archivo_mensual()
    lineas = [f"{datetime.now():%Y-%m-%d %H:%M:%S} - {resultado.get('msg')}"]

    if not resultado.get("ok"):
        confirmacion = data_loader.confirmar_dia_sin_ventas()
        lineas.append(f"{datetime.now():%Y-%m-%d %H:%M:%S} - {confirmacion.get('msg')}")

    texto = "\n".join(lineas) + "\n"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(texto)
    print(texto.strip())


if __name__ == "__main__":
    main()
