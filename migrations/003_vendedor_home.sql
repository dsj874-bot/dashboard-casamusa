-- Elimina la necesidad de resincronizar ventas historicas cada vez que
-- alguien cambia de sucursal, se suma o se va del equipo. Antes,
-- sucursal_logica/vendedor_rpt quedaban grabados fila por fila en
-- ventas (calculados una sola vez al insertar) -- cambiar el mapeo
-- exigia reescribir a mano todas las filas historicas afectadas (ver
-- caso Igor Moya -> Marcelo Gatica). Con esta tabla + vista, esas dos
-- columnas se calculan al consultar: reasignar a alguien es una fila
-- en vendedor_home, sin tocar ventas nunca mas.
--
-- Replica exactamente la logica de data_loader._aplicar_sucursal_logica()
-- (VEND_HOME + VEND_HOME_DESDE + _MAPA_SUC_BASE) -- ver ese docstring
-- para el detalle de reatribucion de SI-STK.

create table if not exists vendedor_home (
    vendedor text primary key,
    sucursal text not null,
    vigente_desde date,
    updated_at timestamptz not null default now(),
    updated_by text
);

-- v_ventas: mismas columnas que ventas, pero sucursal_logica y
-- vendedor_rpt se calculan aqui en vez de leerse de la tabla (las
-- columnas fisicas de ventas quedan sin usar para reportes -- se
-- mantienen solo porque sincronizar_ventas_pg todavia las escribe al
-- insertar; no hace falta borrarlas para que esto funcione).
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
        h.sucursal as home_suc,
        case
            -- SI-STK (bodega compartida): reatribuir a la sucursal home
            -- del vendedor, salvo que la venta sea anterior a su fecha
            -- de traspaso (VEND_HOME_DESDE) -- en ese caso cae al default.
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
    -- Nombre real solo si es home DE ESA sucursal logica (igual que
    -- pair_key.isin(_HOME_PAIRS) en pandas); no depende de la fecha de
    -- traspaso por si sola -- si la reatribucion de arriba no lo dejo
    -- en su sucursal home, esta comparacion ya falla sola.
    case when home_suc is not null and home_suc = sucursal_logica_calc
         then vendedor else 'OTROS' end as vendedor_rpt,
    cond_pago, empresa, proveedor_por_defecto, liquidar, tipo_venta, estatus_sku,
    ano, mes, dia, producto_key
from base;
