"""
Backfill unico (o re-ejecutable) del dominio Comercial/Ventas: carga los
Excel actuales (data/comercial/*.xlsx) a las tablas Postgres creadas en
migrations/002_fase1_comercial.sql.

Reutiliza data_loader.get_df_2025()/get_df_2026() -- que ya aplican
_normalizar_df() (fix de TOTAL mal calculado por SAP) y
_aplicar_sucursal_logica() (SUCURSAL_LOGICA/VENDEDOR_RPT, incluyendo el
mapeo de traspasos VEND_HOME_DESDE) -- en vez de reimplementar esa
logica aqui. Se ejecuta una sola vez por fila, no en cada consulta.

Uso: python scripts/backfill_fase1_comercial.py
"""
import os
import sys
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import data_loader as dl
import data_loader_pg as dlpg
import db

_valor = dlpg.valor_sql


def backfill_productos(df_2025, df_2026):
    print("Construyendo dimension productos desde Ventas...")
    todo = pd.concat([df_2025, df_2026], ignore_index=True)
    todo = todo.sort_values("FECHA_CONTA")
    ultimo_por_codigo = todo.drop_duplicates(subset="CODIGO_CM", keep="last")

    filas = [
        (
            _valor(r.CODIGO_CM), _valor(r.DESCRIPCION), _valor(r.MARCA),
            _valor(r.FAMILIA), _valor(r.SUBFAMILIA), _valor(r.GRUPO),
            _valor(r.ID_PROCEDENCIA), _valor(r.UNIDAD_MEDIDA),
        )
        for r in ultimo_por_codigo.itertuples(index=False)
    ]

    sql = """
        insert into productos (codigo, descripcion, marca, familia, subfamilia, grupo, id_procedencia, um)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (codigo) do update set
            descripcion = excluded.descripcion,
            marca = excluded.marca,
            familia = excluded.familia,
            subfamilia = excluded.subfamilia,
            grupo = excluded.grupo,
            id_procedencia = excluded.id_procedencia,
            um = excluded.um,
            updated_at = now()
    """
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, filas)
        conn.commit()
    print(f"  {len(filas)} productos cargados/actualizados.")


VENTAS_COLUMNAS = dlpg.VENTAS_COLUMNAS
_DF_COLS = dlpg.VENTAS_DF_COLS


def _lotes(seq, tamano):
    for i in range(0, len(seq), tamano):
        yield seq[i:i + tamano]


def backfill_ventas(df, ano_label, tamano_lote=10000, reintentos=3):
    """
    Carga por lotes via executemany (no COPY) -- el pooler de Supabase
    en modo transaccion resulto muy lento con COPY (con 6374 de
    125915 filas ya se habia agotado el statement_timeout). executemany
    ya funciono bien para productos/metas/etc., asi que se reutiliza el
    mismo mecanismo aqui.

    Una sola conexion reutilizada para todos los lotes del año (abrir
    una conexion nueva por cada lote de 10000 resulto ~6x mas lento por
    el overhead de reconexion contra el pooler). Si un lote puntual
    falla (ej. "server closed the connection unexpectedly"), se cierra
    la conexion rota, se abre una nueva y se reintenta SOLO ese lote --
    no hace falta repetir el año completo.
    """
    print(f"Cargando ventas {ano_label} ({len(df)} filas)...")
    fechas = df["FECHA_CONTA"].dt.date.unique().tolist()
    cols_sql = ", ".join(VENTAS_COLUMNAS)
    placeholders = ", ".join(["%s"] * len(VENTAS_COLUMNAS))
    sql = f"insert into ventas ({cols_sql}) values ({placeholders})"

    filas = [tuple(_valor(getattr(r, c)) for c in _DF_COLS) for r in df.itertuples(index=False)]

    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("delete from ventas where fecha_conta = ANY(%s)", (fechas,))
    conn.commit()

    total = 0
    try:
        for lote in _lotes(filas, tamano_lote):
            for intento in range(1, reintentos + 1):
                try:
                    with conn.cursor() as cur:
                        cur.executemany(sql, lote)
                    conn.commit()
                    break
                except Exception as e:
                    print(f"  {ano_label}: lote fallo (intento {intento}/{reintentos}): {e}")
                    try:
                        conn.close()
                    except Exception:
                        pass
                    if intento == reintentos:
                        raise
                    conn = db.get_connection()
            total += len(lote)
            print(f"  {ano_label}: {total}/{len(filas)} filas insertadas...")
    finally:
        conn.close()

    print(f"  {len(df)} filas de {ano_label} insertadas.")


def backfill_metas():
    print("Cargando metas...")
    df = pd.read_excel(os.path.join(dl.DATA_DIR_COMERCIAL, "metas.xlsx"), sheet_name="Metas")
    filas = [
        (_valor(r.ANO), _valor(r.MES), _valor(r.SUCURSAL), _valor(r.VENDEDOR), _valor(r.META))
        for r in df.itertuples(index=False)
    ]
    sql = """
        insert into metas (ano, mes, sucursal, vendedor, meta) values (%s, %s, %s, %s, %s)
        on conflict (ano, mes, sucursal, vendedor) do update set meta = excluded.meta
    """
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, filas)
        conn.commit()
    print(f"  {len(filas)} metas cargadas.")


def backfill_presupuesto(ano_actual):
    print("Cargando presupuesto...")
    df = pd.read_excel(os.path.join(dl.DATA_DIR_COMERCIAL, "presupuesto.xlsx"), sheet_name="Presupuesto")
    filas = [
        (_valor(r.SUCURSAL), ano_actual, _valor(r.PRESUPUESTO_ANUAL))
        for r in df.itertuples(index=False)
    ]
    sql = """
        insert into presupuesto (sucursal, ano, presupuesto_anual) values (%s, %s, %s)
        on conflict (sucursal, ano) do update set presupuesto_anual = excluded.presupuesto_anual
    """
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, filas)
        conn.commit()
    print(f"  {len(filas)} filas de presupuesto cargadas (ano={ano_actual}, sin dato de ano en el Excel origen).")


def backfill_ne_x_facturar():
    print("Cargando NE x Facturar...")
    datos = dl._leer_ne_x_facturar()
    filas = [(suc, vend, monto) for (suc, vend), monto in datos.items()]
    sql = """
        insert into ne_x_facturar (sucursal, vendedor, monto_ne) values (%s, %s, %s)
        on conflict (sucursal, vendedor) do update set monto_ne = excluded.monto_ne, updated_at = now()
    """
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, filas)
        conn.commit()
    print(f"  {len(filas)} filas de NE x Facturar cargadas.")


def backfill_dias_habiles():
    """
    Solo 2025 y 2026 -- son los unicos anos con feriados reales
    conocidos en FERIADOS_CL (y los unicos con datos de Ventas). Para
    extender a otros anos, agregar los feriados a FERIADOS_CL en
    data_loader.py y volver a correr esta funcion.
    """
    print("Cargando dias_habiles_cl (2025-2026)...")
    import calendar
    from datetime import date as date_cls

    filas = []
    for ano in (2025, 2026):
        for mes in range(1, 13):
            _, ultimo_dia = calendar.monthrange(ano, mes)
            for d in range(1, ultimo_dia + 1):
                f = date_cls(ano, mes, d)
                es_finde = f.weekday() >= 5
                es_feriado = f in dl.FERIADOS_CL
                filas.append((f, not es_finde and not es_feriado, "Feriado" if es_feriado else None))

    sql = """
        insert into dias_habiles_cl (fecha, es_habil, descripcion) values (%s, %s, %s)
        on conflict (fecha) do update set es_habil = excluded.es_habil, descripcion = excluded.descripcion
    """
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, filas)
        conn.commit()
    print(f"  {len(filas)} dias cargados.")


def main():
    df_2025 = dl.get_df_2025()
    df_2026 = dl.get_df_2026()

    backfill_productos(df_2025, df_2026)
    backfill_ventas(df_2025, "2025")
    backfill_ventas(df_2026, "2026")
    backfill_metas()
    backfill_presupuesto(ano_actual=2026)
    backfill_ne_x_facturar()
    backfill_dias_habiles()
    print("Backfill completo.")


if __name__ == "__main__":
    main()
