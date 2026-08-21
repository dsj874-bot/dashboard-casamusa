"""
Productos marcados como "no se compra" (productos_no_comprar) --
afecta solo Plan de Compra (Obligatorios y Segunda Linea): el producto
sigue visible con su stock real en Alertas/Distribucion, nunca se
sugiere comprarlo. Pantalla self-service: /gestionar_productos_compra.

Tambien centraliza los 2 prefijos de codigo que son reglas de negocio
permanentes (no listas curadas), para que data_loader_segunda_linea.py
y el buscador de esta pantalla no se desincronicen:
- PREFIJOS_FUERA_SEGUNDA_LINEA: fuera de Segunda Linea completa
  (Alertas, Distribucion y Compra) -- codigos que no son productos
  reales para este proposito.
- PREFIJOS_IMPORTADO_SIN_INJERENCIA: Importados sobre los que el
  usuario no tiene injerencia de compra -- siguen visibles en
  Alertas/Distribucion, solo se excluyen de Plan de Compra.
"""
import db

PREFIJOS_FUERA_SEGUNDA_LINEA = ("6",)
PREFIJOS_IMPORTADO_SIN_INJERENCIA = ("3", "7")
PREFIJOS_EXCLUIDOS_BUSCADOR = PREFIJOS_FUERA_SEGUNDA_LINEA + PREFIJOS_IMPORTADO_SIN_INJERENCIA


def _condicion_prefijos_excluidos(prefijos):
    """SQL "codigo::text not like 'X%' and not like 'Y%' ..." para una
    tupla de prefijos -- evita repetir el patron a mano en cada
    consulta."""
    return " and ".join(f"codigo::text not like '{p}%%'" for p in prefijos)


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


def get_familias_productos():
    """Familias disponibles para el filtro de /gestionar_productos_compra
    -- mismo criterio de exclusion de prefijos que el resto de la
    busqueda (ver buscar_productos): no tiene sentido mostrar
    familias cuyos unicos productos ya estan fuera de consideracion."""
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                select distinct familia from productos
                where familia is not null and {_condicion_prefijos_excluidos(PREFIJOS_EXCLUIDOS_BUSCADOR)}
                order by familia
            """)
            return [r["familia"] for r in cur.fetchall()]


def buscar_productos(query=None, familia=None, limite=200):
    """Busca productos por codigo (si query es numerico) y/o
    descripcion, opcionalmente filtrado por familia -- para el
    buscador de /gestionar_productos_compra. Por palabra (todas deben
    aparecer, en cualquier orden) en vez de substring literal completo
    -- "conduit fuerte" debe encontrar "CONDUIT PVC 1" 32MM ... FUERTE
    3MTS", donde las palabras no quedan juntas.

    Nunca incluye codigos con los prefijos de PREFIJOS_EXCLUIDOS_BUSCADOR
    (6, 3, 7) -- no tiene sentido ofrecer marcarlos "no comprar" a mano
    cuando ya estan fuera de Segunda Linea (6) o ya se excluyen de
    Plan de Compra por regla permanente (3, 7, ver
    data_loader_segunda_linea.get_plan_compra_segunda_linea).

    Devuelve (filas, total) -- algunas familias tienen miles de
    productos (ej. Series Domiciliarias: 2.429), asi que el total real
    se informa aparte para que la pantalla avise "mostrando X de Y" en
    vez de cortar en silencio."""
    query = (query or "").strip()
    if not query and not familia:
        return [], 0

    condiciones = [_condicion_prefijos_excluidos(PREFIJOS_EXCLUIDOS_BUSCADOR)]
    params = {"lim": limite}

    if familia:
        condiciones.append("familia = %(familia)s")
        params["familia"] = familia

    if query:
        if query.isdigit():
            condiciones.append("codigo::text like %(q)s")
            params["q"] = f"{query}%"
        else:
            for i, palabra in enumerate(query.split()):
                condiciones.append(f"descripcion ilike %(p{i})s")
                params[f"p{i}"] = f"%{palabra}%"

    where = " and ".join(condiciones)
    with db.conexion_pool() as conn:
        with conn.cursor() as cur:
            cur.execute(f"select count(*) as n from productos where {where}", params)
            total = cur.fetchone()["n"]
            cur.execute(
                f"select codigo, descripcion, familia, subfamilia from productos "
                f"where {where} order by descripcion limit %(lim)s",
                params,
            )
            filas = cur.fetchall()
    return filas, total
