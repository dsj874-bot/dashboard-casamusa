"""
Consolida el archivo mensual de ventas (data/AAMM_Vtas.xlsx) sin
necesidad de que la app Flask este corriendo.

Pensado para ejecutarse como Tarea Programada de Windows (no requiere
que iniciar.bat este abierto). Si no hay archivo nuevo en data/, no
hace nada (no es un error).
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
    linea = f"{datetime.now():%Y-%m-%d %H:%M:%S} - {resultado.get('msg')}\n"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(linea)
    print(linea.strip())


if __name__ == "__main__":
    main()
