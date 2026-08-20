"""
Productos marcados como "no se compra" (productos_no_comprar) --
afecta solo Plan de Compra (Obligatorios y Segunda Linea): el producto
sigue visible con su stock real en Alertas/Distribucion, nunca se
sugiere comprarlo. Pantalla self-service: /gestionar_productos_compra.
"""
import db


def get_productos_no_comprar():
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select n.codigo, n.motivo, n.updated_at, n.updated_by,
                       p.descripcion, p.familia, p.subfamilia
                from productos_no_comprar n
                left join productos p on p.codigo = n.codigo
                order by n.updated_at desc
            """)
            filas = cur.fetchall()
    return [
        {
            "codigo":      f["codigo"],
            "descripcion": f["descripcion"] or "(código no encontrado en productos)",
            "familia":     f["familia"],
            "subfamilia":  f["subfamilia"],
            "motivo":      f["motivo"],
            "updated_at":  f["updated_at"].strftime("%d/%m/%Y") if f["updated_at"] else None,
            "updated_by":  f["updated_by"],
        }
        for f in filas
    ]


def codigos_excluidos_compra():
    """Set de codigos excluidos -- usado por
    data_loader_obligatorios_pg.get_plan_compra_reposicion_pg() y
    data_loader_segunda_linea.get_plan_compra_segunda_linea() para
    forzar cantidad_a_comprar=0 sin tocar el resto del calculo."""
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute("select codigo from productos_no_comprar")
            return {r["codigo"] for r in cur.fetchall()}


def agregar_no_comprar(codigo, motivo=None, updated_by=None):
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into productos_no_comprar (codigo, motivo, updated_by) values (%s, %s, %s) "
                "on conflict (codigo) do update set motivo=excluded.motivo, updated_by=excluded.updated_by, updated_at=now()",
                (codigo, motivo, updated_by),
            )
        conn.commit()


def quitar_no_comprar(codigo):
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from productos_no_comprar where codigo = %s", (codigo,))
        conn.commit()


def buscar_productos(query, limite=20):
    """Busca productos por codigo (si query es numerico) o descripcion
    -- para el buscador de /gestionar_productos_compra. Por palabra
    (todas deben aparecer, en cualquier orden) en vez de substring
    literal completo -- "conduit fuerte" debe encontrar "CONDUIT PVC
    1" 32MM ... FUERTE 3MTS", donde las palabras no quedan juntas."""
    query = (query or "").strip()
    if not query:
        return []
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            if query.isdigit():
                cur.execute(
                    "select codigo, descripcion, familia, subfamilia from productos "
                    "where codigo::text like %(q)s order by codigo limit %(lim)s",
                    {"q": f"{query}%", "lim": limite},
                )
            else:
                palabras = query.split()
                condiciones = " and ".join(f"descripcion ilike %(p{i})s" for i in range(len(palabras)))
                params = {f"p{i}": f"%{p}%" for i, p in enumerate(palabras)}
                params["lim"] = limite
                cur.execute(
                    f"select codigo, descripcion, familia, subfamilia from productos "
                    f"where {condiciones} order by descripcion limit %(lim)s",
                    params,
                )
            return cur.fetchall()
