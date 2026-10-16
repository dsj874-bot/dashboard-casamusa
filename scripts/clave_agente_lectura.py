"""Fija (o cambia) la contrasena del rol agente_lectura (migracion 021) y deja
la conexion en .env como DATABASE_URL_LECTURA. Esa misma linea hay que
cargarla en Vercel (Environment Variables, tipo Secret).

La conexion usa el mismo pooler que DATABASE_URL, con el usuario
agente_lectura.<ref-del-proyecto>.

Uso:  python scripts/clave_agente_lectura.py
"""
import os
import re
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db  # noqa: E402  (carga el .env)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(RAIZ, ".env")

url = os.environ["DATABASE_URL"]
m = re.match(r"postgres(?:ql)?://([^.:]+)\.([^:]+):[^@]+@([^/]+)/(.+)", url)
if not m:
    raise SystemExit("No se reconoce el formato de DATABASE_URL")
ref, hostport, base = m.group(2), m.group(3), m.group(4)
clave = secrets.token_urlsafe(32)

with db.get_connection() as c:
    c.execute(f"ALTER ROLE agente_lectura PASSWORD '{clave}'")
    c.commit()

nueva = f"postgresql://agente_lectura.{ref}:{clave}@{hostport}/{base}"
texto = open(ENV, encoding="utf-8").read() if os.path.exists(ENV) else ""
if "DATABASE_URL_LECTURA=" in texto:
    texto = re.sub(r"(?m)^DATABASE_URL_LECTURA=.*$", "DATABASE_URL_LECTURA=" + nueva, texto)
else:
    texto = texto.rstrip("\n") + "\n\nDATABASE_URL_LECTURA=" + nueva + "\n"
open(ENV, "w", encoding="utf-8").write(texto)
print("Listo: contrasena nueva y DATABASE_URL_LECTURA actualizada en .env")
