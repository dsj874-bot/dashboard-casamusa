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
            if v and not isinstance(v[0], dict):
                # Lista de escalares: si son todos strings (ej. lista de
                # sucursales/vendedores) el orden no importa -- comparar
                # como conjunto. Si son numeros (ej. venta por mes), el
                # orden SI importa -- comparar posicion a posicion.
                if all(isinstance(x, str) for x in v + n):
                    if set(v) != set(n):
                        fallas.append(
                            f"[{nombre}] {r}: solo_Excel={set(v) - set(n)!r} solo_Postgres={set(n) - set(v)!r}"
                        )
                elif len(v) != len(n):
                    fallas.append(f"[{nombre}] {r}: largo distinto Excel={len(v)} vs Postgres={len(n)}")
                else:
                    for i, (vi, ni) in enumerate(zip(v, n)):
                        if not _cerca(vi, ni):
                            fallas.append(f"[{nombre}] {r}[{i}]: Excel={vi!r} vs Postgres={ni!r}")
                continue
            _diff_lista(nombre, v, n, r)
        elif not _cerca(v, n):
            fallas.append(f"[{nombre}] {r}: Excel={v!r} vs Postgres={n!r}")


def _clave_fila(r):
    if "nombre" in r:
        return r["nombre"]
    if "mes" in r:
        return r["mes"]
    if "vendedor" in r:
        return (r["sucursal"], r["vendedor"])
    return r["sucursal"]


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

    print("Comparando get_filtros_proyeccion()...")
    check("get_filtros_proyeccion (sin filtro)", dl.get_filtros_proyeccion(), dlpg.get_filtros_proyeccion_pg())

    print("Comparando get_proyeccion()...")
    check("get_proyeccion (sin filtros)", dl.get_proyeccion(), dlpg.get_proyeccion_pg())
    check(
        "get_proyeccion (sucursal MT)",
        dl.get_proyeccion({"sucursal": "MT"}),
        dlpg.get_proyeccion_pg({"sucursal": "MT"}),
    )
    check(
        "get_proyeccion (sucursal Express=[CH,MP])",
        dl.get_proyeccion({"sucursal": ["CH", "MP"]}),
        dlpg.get_proyeccion_pg({"sucursal": ["CH", "MP"]}),
    )
    check(
        "get_proyeccion (procedencia Importado)",
        dl.get_proyeccion({"procedencia": "Importado"}),
        dlpg.get_proyeccion_pg({"procedencia": "Importado"}),
    )

    print("Comparando get_seguimiento_metas()...")
    check("get_seguimiento_metas (sin filtros)", dl.get_seguimiento_metas(), dlpg.get_seguimiento_metas_pg())
    check(
        "get_seguimiento_metas (sucursal MT)",
        dl.get_seguimiento_metas({"sucursal": "MT"}),
        dlpg.get_seguimiento_metas_pg({"sucursal": "MT"}),
    )
    check(
        "get_seguimiento_metas (vendedor puntual)",
        dl.get_seguimiento_metas({"vendedor": "FRANCISCA CORREA"}),
        dlpg.get_seguimiento_metas_pg({"vendedor": "FRANCISCA CORREA"}),
    )

    print("Comparando get_seguimiento_ppto()...")
    check("get_seguimiento_ppto (sin filtro)", dl.get_seguimiento_ppto(), dlpg.get_seguimiento_ppto_pg())
    check(
        "get_seguimiento_ppto (MT)",
        dl.get_seguimiento_ppto(filtro_sucursal="MT"),
        dlpg.get_seguimiento_ppto_pg(filtro_sucursal="MT"),
    )
    check(
        "get_seguimiento_ppto (Express=[CH,MP])",
        dl.get_seguimiento_ppto(filtro_sucursal=["CH", "MP"]),
        dlpg.get_seguimiento_ppto_pg(filtro_sucursal=["CH", "MP"]),
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
