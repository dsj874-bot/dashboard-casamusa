"""
Conexion a Postgres (Supabase) para las partes del dashboard ya migradas.

Usa el connection string con pooler (puerto 6543, modo transaccion) via la
variable de entorno DATABASE_URL -- necesario porque cada invocacion
serverless en Vercel abre una conexion nueva y el pooler evita agotar
max_connections de Postgres. prepare_threshold=None desactiva los
prepared statements de psycopg, que no son compatibles con el modo
transaccion del pooler (Supavisor).
"""
import os
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("Falta la variable de entorno DATABASE_URL")
    return psycopg.connect(DATABASE_URL, prepare_threshold=None, row_factory=dict_row)


def query_one(sql, params=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def query_all(sql, params=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def execute(sql, params=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
