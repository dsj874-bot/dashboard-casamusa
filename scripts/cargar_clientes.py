"""
Carga el maestro de clientes de SAP a cliente_maestro (migracion 029).
Toma data/comercial/Clientes/Maestro de Clientes.xlsx o el archivo que se pase.
Cada carga REEMPLAZA la anterior. Trae datos personales: el archivo vive en
data/ (fuera de git) y la tabla solo la leen las pantallas del CRM.

    python scripts/cargar_clientes.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

import db  # noqa: E402

COLS = {
    "Código Cliente": "codigo_cliente", "Razón Social": "razon_social", "RUT": "rut", "Dirección": "direccion",
    "Comuna (County)": "comuna", "Ciudad (City)": "ciudad", "Región": "region", "Contacto": "contacto",
    "Teléfono": "telefono", "Celular": "celular", "Correo": "correo", "Vendedor": "vendedor_sap",
    "Condición de Pago": "condicion_pago", "Línea de Crédito": "linea_credito", "Saldo Cuenta": "saldo_cuenta",
    "Pedidos Pendientes": "pedidos_pendientes", "Crédito Disponible": "credito_disponible", "Congelado": "congelado",
}
NUM = ("linea_credito", "saldo_cuenta", "pedidos_pendientes", "credito_disponible")


def main():
    ruta = sys.argv[1] if len(sys.argv) > 1 else "data/comercial/Clientes/Maestro de Clientes.xlsx"
    df = pd.read_excel(ruta, dtype={"Teléfono": str, "Celular": str, "RUT": str})
    faltan = [c for c in COLS if c not in df.columns]
    if faltan:
        raise SystemExit(f"Al archivo le faltan columnas: {faltan}")
    df = df[list(COLS)].rename(columns=COLS)
    df = df[df["codigo_cliente"].notna()].drop_duplicates("codigo_cliente")
    for c in NUM:
        df[c] = pd.to_numeric(df[c], errors="coerce")          # una fila viene con columnas corridas
    df["congelado"] = df["congelado"].where(df["congelado"].isin(["Y", "N"]), None)
    df["condicion_pago"] = df["condicion_pago"].where(df["condicion_pago"].map(lambda x: isinstance(x, str)), None)
    filas = [tuple(None if pd.isna(v) else (str(v).strip() if k not in NUM else float(v)) for k, v in r.items())
             + (os.path.basename(ruta),) for r in df.to_dict("records")]
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '10min'")     # el COPY de ~14 mil filas pasa el limite por defecto
            cur.execute("CREATE TEMP TABLE cli_carga (LIKE cliente_maestro INCLUDING DEFAULTS) ON COMMIT DROP")
            cols = ", ".join(list(COLS.values()) + ["archivo"])
            with cur.copy(f"COPY cli_carga ({cols}) FROM STDIN") as cp:
                for f in filas:
                    cp.write_row(f)
            cur.execute("DELETE FROM cliente_maestro")
            cur.execute("INSERT INTO cliente_maestro SELECT * FROM cli_carga")
        conn.commit()
    print(f"{len(filas)} clientes cargados desde {ruta}")


if __name__ == "__main__":
    main()
