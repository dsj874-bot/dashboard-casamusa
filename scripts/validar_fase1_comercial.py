"""
Diff campo-por-campo entre las funciones originales (Excel/pandas, en
data_loader.py) y las nuevas (Postgres, en data_loader_pg.py) -- no se
corta ninguna ruta de app.py a la version Postgres hasta que esto pase
limpio (ver plan de migracion, seccion "Verificacion").

Uso: python scripts/validar_fase1_comercial.py
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import data_loader as dl
import data_loader_pg as dlpg

EPSILON = 0.01
fallas = []


def _cerca(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= EPSILON
    return a == b


def _diff_dict(nombre, viejo, nuevo, ruta=""):
    claves = set(viejo.keys()) | set(nuevo.keys())
    for k in sorted(claves):
        r = f"{ruta}.{k}" if ruta else k
        if k not in viejo:
            fallas.append(f"[{nombre}] {r}: falta en version Excel (Postgres tiene {nuevo[k]!r})")
            continue
        if k not in nuevo:
            fallas.append(f"[{nombre}] {r}: falta en version Postgres (Excel tiene {viejo[k]!r})")
            continue
        v, n = viejo[k], nuevo[k]
        if isinstance(v, dict) and isinstance(n, dict):
            _diff_dict(nombre, v, n, r)
        elif isinstance(v, list) and isinstance(n, list):
            _diff_lista(nombre, v, n, r)
        elif not _cerca(v, n):
            fallas.append(f"[{nombre}] {r}: Excel={v!r} vs Postgres={n!r}")


def _clave_fila(r):
    return r["nombre"] if "nombre" in r else r["mes"]


def _diff_lista(nombre, viejo, nuevo, ruta):
    idx_viejo = {_clave_fila(r): r for r in viejo}
    idx_nuevo = {_clave_fila(r): r for r in nuevo}
    claves = set(idx_viejo.keys()) | set(idx_nuevo.keys())
    for k in sorted(claves):
        r = f"{ruta}[{k}]"
        if k not in idx_viejo:
            fallas.append(f"[{nombre}] {r}: fila extra solo en Postgres")
            continue
        if k not in idx_nuevo:
            fallas.append(f"[{nombre}] {r}: fila faltante en Postgres (existe en Excel)")
            continue
        _diff_dict(nombre, idx_viejo[k], idx_nuevo[k], r)


def check(nombre, viejo, nuevo):
    antes = len(fallas)
    if isinstance(viejo, dict) and isinstance(nuevo, dict):
        _diff_dict(nombre, viejo, nuevo)
    else:
        if not _cerca(viejo, nuevo):
            fallas.append(f"[{nombre}]: Excel={viejo!r} vs Postgres={nuevo!r}")
    ok = len(fallas) == antes
    print(f"  {'OK' if ok else 'FALLA'} - {nombre}")
    return ok


def main():
    print("Comparando get_resumen()...")
    check("get_resumen (sin filtro)", dl.get_resumen(), dlpg.get_resumen_pg())
    check("get_resumen (MT)", dl.get_resumen(filtro_sucursal="MT"), dlpg.get_resumen_pg(filtro_sucursal="MT"))
    check(
        "get_resumen (Express=[CH,MP])",
        dl.get_resumen(filtro_sucursal=["CH", "MP"]),
        dlpg.get_resumen_pg(filtro_sucursal=["CH", "MP"]),
    )

    print("Comparando get_ventas_por_mes()...")
    check("get_ventas_por_mes (sin filtro)", dl.get_ventas_por_mes(), dlpg.get_ventas_por_mes_pg())
    check(
        "get_ventas_por_mes (MT)",
        dl.get_ventas_por_mes(filtro_sucursal="MT"),
        dlpg.get_ventas_por_mes_pg(filtro_sucursal="MT"),
    )

    print("Comparando get_ventas_por_sucursal()...")
    check("get_ventas_por_sucursal (sin filtro)", dl.get_ventas_por_sucursal(), dlpg.get_ventas_por_sucursal_pg())
    check(
        "get_ventas_por_sucursal (MT)",
        dl.get_ventas_por_sucursal(filtro_sucursal="MT"),
        dlpg.get_ventas_por_sucursal_pg(filtro_sucursal="MT"),
    )

    print()
    if fallas:
        print(f"{len(fallas)} diferencias encontradas:")
        for f in fallas:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("Todo coincide. Sin diferencias.")


if __name__ == "__main__":
    main()
