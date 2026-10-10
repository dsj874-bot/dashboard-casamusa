-- =====================================================================
-- 015 - Margen ajustado al reporte de margen del ERP, por vendedor y mes
--
-- Por que: el export de ventas de SAP valoriza el costo con el CUP del
-- dia en que se corre el export (ver 014, "LO QUE ESTO NO ARREGLA"). Ene-jun
-- 2026 se recargaron en agosto, con el CUP de agosto, y el margen quedo
-- $80 M bajo el reporte "Margen por vendedor" del ERP (ene-oct 2026:
-- $1.790,3 M contra $1.870,3 M). La venta si cuadra (0,05%).
--
-- Decision del usuario (2026-10-10, "opcion 2"): usar el reporte del ERP
-- como margen oficial por vendedor y mes, en vez de reexportar de SAP.
--
-- Como: margen_erp guarda el reporte tal cual (venta y ganancia por
-- vendedor y mes). ajuste_costo_erp guarda, por vendedor y mes, el factor
-- que lleva el costo de Musa360 al % de margen del ERP:
--     factor = venta_musa * (1 - ganancia_erp / venta_erp) / costo_musa
-- y la vista calcula utilidad_bruta = total - costo_total * factor. El
-- ajuste se reparte entre todas las lineas del vendedor en el mes, asi
-- que las vistas por sucursal/marca/cliente se mueven en proporcion y
-- siguen sumando el total. Se usa el % y no el monto del ERP para que una
-- diferencia de venta (una factura reasignada de vendedor) no deforme el
-- costo del resto de las lineas.
--
-- Los factores los calcula recalcular_ajuste_margen_erp_pg() en
-- data_loader_pg.py, al cargar el reporte. Sin fila en ajuste_costo_erp
-- (2025, meses posteriores al reporte, o vendedor-mes con venta/costo
-- <= 0) el factor es 1 y el margen queda como antes.
--
-- Efecto simulado antes de aplicar (2026-10-10), ene-oct 2026:
--   $1.790.307.288 (35,36%) -> $1.871.239.814 (36,96%)   ERP $1.870.332.053
--   322 vendedor-mes ajustados, factores entre 0,804 y 1,089
--
-- ventas.costo_total y la columna costo_total de la vista NO cambian: el
-- costo original queda para auditar, y para volver atras basta con
-- vaciar ajuste_costo_erp.
-- =====================================================================

begin;

create table if not exists margen_erp (
    ano          int     not null,
    mes          int     not null,
    vendedor     text    not null,
    venta        numeric not null,
    ganancia     numeric not null,
    cargado_en   timestamptz not null default now(),
    cargado_por  text,
    primary key (ano, mes, vendedor)
);

create table if not exists ajuste_costo_erp (
    ano       int     not null,
    mes       int     not null,
    vendedor  text    not null,   -- tal cual ventas.vendedor (para el join)
    factor    numeric not null,
    primary key (ano, mes, vendedor)
);

create or replace view v_ventas as
with base as (
    select
        v.id, v.doc_sap, v.folio, v.tipo_doc, v.fecha_conta, v.fecha_doc,
        v.codigo_cliente, v.nombre_cliente, v.procedencia, v.sucursal,
        v.codigo_cm, v.id_procedencia, v.codigo_proveedor, v.descripcion,
        v.marca, v.unidad_medida, v.familia, v.subfamilia, v.grupo,
        v.cantidad, v.costo_cup, v.costo_total, v.precio_unitario, v.total,
        (v.total - v.costo_total * coalesce(a.factor, 1)) as utilidad_bruta,
        v.mg_bruto, v.vendedor, v.cond_pago, v.empresa,
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
    left join ajuste_costo_erp a on a.ano = v.ano and a.mes = v.mes and a.vendedor = v.vendedor
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
