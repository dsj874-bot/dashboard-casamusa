-- RENDIMIENTO: materializar es_distribucion en la tabla ventas.
--
-- La migracion 012 marcaba al cliente de Distribucion con un LEFT JOIN
-- que normalizaba el RUT en tiempo de consulta:
--
--   left join clientes_distribucion cd
--          on cd.rut_norm = ltrim(regexp_replace(upper(...), '^C|[.-]', '', 'g'), '0')
--
-- Eso obliga a Postgres a correr un upper + regexp_replace + ltrim por
-- CADA una de las 226.000 filas de ventas, en CADA consulta que toque
-- v_ventas -- o sea, en todos los reportes comerciales, aunque el
-- usuario no este usando el filtro de Distribucion.
--
-- La nota de la 012 decia "medido: el join no encarece las consultas
-- agregadas (0.68s contra 0.72s)". Esa medicion estaba tomada desde el
-- cliente, donde la latencia Chile-Oregon tapaba la diferencia. Medido
-- del lado del servidor (explain analyze), el costo real es:
--
--   select sum(total) from v_ventas where ano in (2025,2026)
--     con el join regexp ....... 2171 ms
--     sin el join (esta migr.) .. 159 ms      <- 13,6x
--
-- Solucion: guardar la marca como columna booleana en ventas y sacar el
-- join de la vista. Se mantiene sola en dos puntos:
--   1) trigger antes de INSERT/UPDATE en ventas (cubre la carga diaria,
--      que entra por COPY -- los triggers de fila si se disparan en COPY);
--   2) recalculo completo al reemplazar la lista maestra, dentro de
--      cargar_clientes_distribucion_pg().
--
-- Verificado antes de aplicar: el resultado es identico al del join
-- (0 discrepancias sobre las 226.307 filas, 3.159 marcadas en ambos).

begin;

alter table ventas add column if not exists es_distribucion boolean not null default false;

-- Misma normalizacion que _norm_rut() en data_loader_pg.py y que el
-- join de la 012: sacar prefijo C, puntos, guiones y ceros a la izquierda.
create or replace function ventas_rut_norm(codigo text)
returns text language sql immutable as $$
    select ltrim(regexp_replace(upper(coalesce(codigo, '')), '^C|[.-]', '', 'g'), '0')
$$;

create or replace function ventas_marcar_distribucion()
returns trigger language plpgsql as $$
begin
    new.es_distribucion := exists (
        select 1 from clientes_distribucion cd
         where cd.rut_norm = ventas_rut_norm(new.codigo_cliente)
    );
    return new;
end;
$$;

drop trigger if exists trg_ventas_distribucion on ventas;
create trigger trg_ventas_distribucion
    before insert or update of codigo_cliente on ventas
    for each row execute function ventas_marcar_distribucion();

-- Backfill de lo ya cargado.
update ventas v
   set es_distribucion = exists (
       select 1 from clientes_distribucion cd
        where cd.rut_norm = ventas_rut_norm(v.codigo_cliente)
   )
 where v.es_distribucion is distinct from exists (
       select 1 from clientes_distribucion cd
        where cd.rut_norm = ventas_rut_norm(v.codigo_cliente)
   );

-- La consulta de fecha de datos (_fecha_datos_pg, se ejecuta en CADA
-- request comercial) filtra por ano+mes, para lo que no habia indice:
-- eran ~380 ms de seq scan por pantalla.
create index if not exists idx_ventas_ano_mes on ventas (ano, mes);

-- v_ventas identica a la de la 012 pero SIN el join de distribucion:
-- ahora es_distribucion sale directo de la columna.
create or replace view v_ventas as
with base as (
    select
        v.id, v.doc_sap, v.folio, v.tipo_doc, v.fecha_conta, v.fecha_doc,
        v.codigo_cliente, v.nombre_cliente, v.procedencia, v.sucursal,
        v.codigo_cm, v.id_procedencia, v.codigo_proveedor, v.descripcion,
        v.marca, v.unidad_medida, v.familia, v.subfamilia, v.grupo,
        v.cantidad, v.costo_cup, v.costo_total, v.precio_unitario, v.total,
        v.utilidad_bruta, v.mg_bruto, v.vendedor, v.cond_pago, v.empresa,
        v.proveedor_por_defecto, v.liquidar, v.tipo_venta, v.estatus_sku,
        v.ano, v.mes, v.dia, v.producto_key,
        v.es_distribucion as es_distribucion_calc,
        h.sucursal as home_suc,
        case
            when v.sucursal = 'SI-STK' and h.sucursal is not null
                 and (h.vigente_desde is null or v.fecha_conta >= h.vigente_desde)
                then h.sucursal
            when v.sucursal = 'SI-STK' then 'SE'
            else coalesce(
                case v.sucursal
                    when 'MT-STK' then 'MT' when 'LC-STK' then 'LC' when 'MR-STK' then 'MR'
                    when 'CH-STK' then 'CH' when 'MP-STK' then 'MP' when 'OF-STK' then 'OF'
                    when 'DM-STK' then 'CANAL DIGITAL' when 'SE-STK' then 'CANAL DIGITAL'
                end,
                v.sucursal
            )
        end as sucursal_logica_calc
    from ventas v
    left join vendedor_home h on h.vendedor = v.vendedor
)
select
    id, doc_sap, folio, tipo_doc, fecha_conta, fecha_doc, codigo_cliente,
    nombre_cliente, procedencia, sucursal,
    sucursal_logica_calc as sucursal_logica,
    codigo_cm, id_procedencia, codigo_proveedor, descripcion, marca, unidad_medida,
    familia, subfamilia, grupo, cantidad, costo_cup, costo_total, precio_unitario,
    total, utilidad_bruta, mg_bruto, vendedor,
    case when home_suc is not null and home_suc = sucursal_logica_calc
         then vendedor else 'OTROS' end as vendedor_rpt,
    cond_pago, empresa, proveedor_por_defecto, liquidar, tipo_venta, estatus_sku,
    ano, mes, dia, producto_key,
    es_distribucion_calc as es_distribucion
from base;

commit;

analyze ventas;
