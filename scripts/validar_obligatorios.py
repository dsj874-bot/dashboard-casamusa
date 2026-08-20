"""
Diff campo-por-campo entre las funciones originales (Excel/pandas, en
data_loader_obligatorios.py) y las nuevas (Postgres, en
data_loader_obligatorios_pg.py) -- mismo patron que
validar_fase1_comercial.py / validar_inventario.py. Por ahora solo
Alertas de Quiebre Critico (lo unico ya portado).

Uso: python scripts/validar_obligatorios.py
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import data_loader_obligatorios as do
import data_loader_obligatorios_pg as dopg

EPSILON = 0.5
fallas = []


def _cerca(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= EPSILON
    return a == b


def _diff_dict(nombre, viejo, nuevo, ruta=""):
    claves = set(viejo.keys()) | set(nuevo.keys())
    for k in sorted(claves, key=str):
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
    if "codigo" in r:
        return r["codigo"]
    if "nombre" in r:
        return r["nombre"]
    return str(r)


def _diff_lista(nombre, viejo, nuevo, ruta):
    if viejo and not isinstance(viejo[0], dict):
        if set(viejo) != set(nuevo):
            fallas.append(f"[{nombre}] {ruta}: solo_Excel={set(viejo)-set(nuevo)!r} solo_Postgres={set(nuevo)-set(viejo)!r}")
        return
    idx_viejo = {_clave_fila(r): r for r in viejo}
    idx_nuevo = {_clave_fila(r): r for r in nuevo}
    claves = set(idx_viejo.keys()) | set(idx_nuevo.keys())
    for k in sorted(claves, key=str):
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
    print("Comparando get_familias_obligatorios()...")
    check("get_familias_obligatorios", do.get_familias_obligatorios(), dopg.get_familias_obligatorios_pg())

    print("Comparando get_alertas_quiebre_critico()...")
    check(
        "get_alertas_quiebre_critico (todas las familias)",
        do.get_alertas_quiebre_critico(),
        dopg.get_alertas_quiebre_critico_pg(),
    )
    for familia in do.get_familias_obligatorios():
        check(
            f"get_alertas_quiebre_critico ({familia})",
            do.get_alertas_quiebre_critico(familia),
            dopg.get_alertas_quiebre_critico_pg(familia),
        )

    print()
    if fallas:
        print(f"{len(fallas)} diferencias encontradas:")
        for f in fallas[:80]:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("Todo coincide. Sin diferencias.")


if __name__ == "__main__":
    main()
