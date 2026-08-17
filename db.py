"""
Conexion a Postgres (Supabase) para las partes del dashboard ya migradas.

Usa el connection string con pooler (puerto 6543, modo transaccion) via la
variable de entorno DATABASE_URL. prepare_threshold=None desactiva los
prepared statements de psycopg, que no son compatibles con el modo
transaccion del pooler (Supavisor).

Un pool de conexiones a nivel de modulo (no una conexion nueva por
consulta) para que, dentro de una misma invocacion de Vercel -- y entre
invocaciones de una instancia "tibia" con Fluid Compute -- no se pague
el handshake TCP+TLS+auth completo (region Oregon, con latencia real
desde Chile) en cada query. Se crea de forma perezosa (al primer uso),
no al importar el modulo.
"""
import os
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("Falta la variable de entorno DATABASE_URL")
        _pool = ConnectionPool(
            DATABASE_URL,
            min_size=0,
            max_size=5,
            kwargs={"prepare_threshold": None, "row_factory": dict_row},
            open=True,
        )
    return _pool


def get_connection():
    """Conexion suelta (no del pool) -- para scripts de backfill/migracion
    que corren una vez y no se benefician de mantener un pool vivo."""
    if not DATABASE_URL:
        raise RuntimeError("Falta la variable de entorno DATABASE_URL")
    return psycopg.connect(DATABASE_URL, prepare_threshold=None, row_factory=dict_row)


def conexion_pool():
    """Conexion del pool (contextmanager) -- usar dentro de app.py para
    que las consultas de una misma request/instancia tibia reutilicen
    conexiones en vez de abrir una nueva cada vez."""
    return _get_pool().connection()


def query_one(sql, params=None):
    with conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def query_all(sql, params=None):
    with conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def execute(sql, params=None):
    with conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
