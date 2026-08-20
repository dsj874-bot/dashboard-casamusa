"""
Diff campo-por-campo entre las funciones originales (Excel/pandas, en
data_loader_inventario.py) y las nuevas (Postgres, en
data_loader_inventario_pg.py) -- mismo patron que
validar_fase1_comercial.py.

Uso: python scripts/validar_inventario.py
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import data_loader_inventario as dli
import data_loader_inventario_pg as dlipg

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
            if v and not isinstance(v[0], dict):
                if set(v) != set(n):
                    fallas.append(f"[{nombre}] {r}: solo_Excel={set(v)-set(n)!r} solo_Postgres={set(n)-set(v)!r}")
                continue
            _diff_lista(nombre, v, n, r)
        elif not _cerca(v, n):
            fallas.append(f"[{nombre}] {r}: Excel={v!r} vs Postgres={n!r}")


def _clave_fila(r):
    if "clase" in r:
        return r["clase"]
    if "marca" in r:
        return r["marca"]
    if "subfamilia" in r:
        return r["subfamilia"]
    if "bodega" in r:
        return r["bodega"]
    return str(r)


def _diff_lista(nombre, viejo, nuevo, ruta):
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
    print("Comparando get_resumen_inventario()...")
    check("get_resumen_inventario", dli.get_resumen_inventario(), dlipg.get_resumen_inventario_pg())

    print("Comparando get_inventario_por_bodega()...")
    check("get_inventario_por_bodega", dli.get_inventario_por_bodega(), dlipg.get_inventario_por_bodega_pg())

    print("Comparando get_inventario_por_clasificacion()...")
    check("get_inventario_por_clasificacion", dli.get_inventario_por_clasificacion(), dlipg.get_inventario_por_clasificacion_pg())

    print("Comparando get_inventario_por_procedencia()...")
    check("get_inventario_por_procedencia", dli.get_inventario_por_procedencia(), dlipg.get_inventario_por_procedencia_pg())

    print("Comparando get_inventario_por_familia()...")
    check("get_inventario_por_familia", dli.get_inventario_por_familia(), dlipg.get_inventario_por_familia_pg())

    print("Comparando get_bodegas_disponibles()...")
    check("get_bodegas_disponibles", dli.get_bodegas_disponibles(), dlipg.get_bodegas_disponibles_pg())

    print("Comparando get_inventario_por_marca_subfamilia()...")
    check(
        "get_inventario_por_marca_subfamilia (Todas)",
        dli.get_inventario_por_marca_subfamilia("Todas"),
        dlipg.get_inventario_por_marca_subfamilia_pg("Todas"),
    )
    check(
        "get_inventario_por_marca_subfamilia (San Isidro)",
        dli.get_inventario_por_marca_subfamilia("San Isidro"),
        dlipg.get_inventario_por_marca_subfamilia_pg("San Isidro"),
    )
    check(
        "get_inventario_por_marca_subfamilia (Maipú, Importado)",
        dli.get_inventario_por_marca_subfamilia("Maipú", "Importado"),
        dlipg.get_inventario_por_marca_subfamilia_pg("Maipú", "Importado"),
    )

    print()
    if fallas:
        print(f"{len(fallas)} diferencias encontradas:")
        for f in fallas[:60]:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("Todo coincide. Sin diferencias.")


if __name__ == "__main__":
    main()
