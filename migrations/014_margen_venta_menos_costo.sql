-- =====================================================================
-- 014 - El margen se calcula como venta - costo, no con la columna
--       utilidad_bruta del export de SAP
--
-- Por que: el export de ventas de SAP trae utilidad_bruta = 0 en los
-- codigos de servicio y administrativos aunque tengan venta y costo cero.
-- El caso mas grande: "RETORNO CUMPLIMIENTO METAS", un rebate de
-- proveedor facturado como venta ($30.435.586 solo en enero 2026), que
-- tiene margen 100% y en el export aparece con utilidad 0. El reporte de
-- margen de SAP si los cuenta, asi que el dashboard quedaba bajo SAP.
--
-- Decision del usuario (2026-10-01): los FLETES cobrados al cliente
-- cuentan como margen, igual que los rebates -- aunque su costo de
-- despacho se registre como gasto fuera de esta tabla. Con eso el
-- margen queda en linea con el reporte de margen de SAP, que tambien
-- los cuenta. Fletes 2026: 2.822 lineas, $13.098.153.
--
-- Efecto medido antes de aplicar (2026-09-30):
--   2025  $1.712.654.548 (30,9%)  ->  $1.760.094.042 (31,8%)   +$47,4 M
--   2026  $1.648.360.590 (34,4%)  ->  $1.687.176.798 (35,2%)   +$38,8 M
--   enero 2026 pasa de 29,1% a 34,7%; el resto de los meses +0,1 a +0,4 pts
--
-- Se cambia en la VISTA y no en la tabla a proposito: ventas.utilidad_bruta
-- conserva el valor tal como lo exporta SAP, para poder auditarlo o
-- volver atras recreando la 013. Todas las consultas de margen del
-- dashboard (data_loader_pg.py) leen de v_ventas, asi que el cambio
-- les llega a todas desde este unico punto.
--
-- Sin nulos que perder: se verifico que total y costo_total no tienen
-- ningun NULL (con un NULL, total - costo_total daria NULL y esa venta
-- saldria del margen en vez de contar completa).
--
-- LO QUE ESTO NO ARREGLA: el export de SAP valoriza cada venta con el CUP
-- del dia en que se corre el export, no con el costo del dia de la venta
-- (verificado: los 2.014 codigos vendidos en 3+ meses de ene-jun 2026
-- tienen un solo CUP cada uno). Eso deja todavia ~$79 M de brecha contra
-- el reporte de margen de SAP en 2026 y solo se corrige en la consulta
-- que genera el export, del lado de SAP.
--
-- mg_bruto (margen % por linea) no se recalcula: ninguna consulta del
-- dashboard lo agrega.
-- =====================================================================

begin;

create or replace view v_ventas as
with base as (
    select
        v.id, v.doc_sap, v.folio, v.tipo_doc, v.fecha_conta, v.fecha_doc,
        v.codigo_cliente, v.nombre_cliente, v.procedencia, v.sucursal,
        v.codigo_cm, v.id_procedencia, v.codigo_proveedor, v.descripcion,
        v.marca, v.unidad_medida, v.familia, v.subfamilia, v.grupo,
        v.cantidad, v.costo_cup, v.costo_total, v.precio_unitario, v.total,
        (v.total - v.costo_total) as utilidad_bruta, v.mg_bruto, v.vendedor, v.cond_pago, v.empresa,
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
