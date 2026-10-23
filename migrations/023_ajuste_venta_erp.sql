-- =====================================================================
-- 023 - Venta y margen iguales al reporte "Margen por vendedor" del ERP
--
-- Por que (2026-10-23): con 015 el margen quedaba en el % del ERP, pero la
-- venta de Musa360 difiere del ERP en algunos vendedor-mes (documentos que
-- cambiaron en el ERP despues de cargarse: notas de credito, cambio de
-- vendedor). Ene-oct 2026: venta $5.249,6 M contra $5.246,4 M del ERP.
-- Decision del usuario: "quiero que en Musa360 aparezca igual que en el
-- ERP", con un ajuste automatico en vez de recargar la venta.
--
-- Como: ajuste_venta_erp guarda, por vendedor y mes, la diferencia de venta
-- y de margen con el ERP. La vista v_ventas las suma como una linea mas
-- (tipo_doc / marca / descripcion 'AJUSTE ERP', sin codigo de producto), asi
-- que la venta y el margen por vendedor, sucursal y total quedan iguales al
-- reporte del ERP. En las vistas por producto, cliente o marca aparece como
-- "AJUSTE ERP". El mes en curso se ajusta solo hasta el dia del reporte: lo
-- que se cargue despues suma normal.
--
-- Los ajustes los calcula recalcular_ajuste_margen_erp_pg() en
-- data_loader_pg.py (al cargar el reporte del ERP y al recargar ventas).
-- Para volver atras basta con vaciar ajuste_venta_erp.
-- =====================================================================

begin;

create table if not exists ajuste_venta_erp (
    id        bigint  generated always as identity,   -- la vista lo usa en negativo como id de la linea
    ano       int     not null,
    mes       int     not null,
    vendedor  text    not null,   -- como en ventas.vendedor (o el nombre del ERP si no tiene venta)
    fecha     date    not null,   -- ultimo dia del mes, o el dia del reporte en el mes en curso
    sucursal  text    not null,   -- sucursal donde mas vendio ese mes (codigo SAP, ej. LC-STK)
    venta     numeric not null,   -- ERP - Musa360
    ganancia  numeric not null,   -- ERP - Musa360 (margen ya ajustado por 015)
    primary key (ano, mes, vendedor)
);

create or replace view v_ventas as
with base as (
    select v.id, v.doc_sap, v.folio, v.tipo_doc, v.fecha_conta, v.fecha_doc, v.codigo_cliente, v.nombre_cliente,
           v.procedencia, v.sucursal, v.codigo_cm, v.id_procedencia, v.codigo_proveedor, v.descripcion, v.marca,
           v.unidad_medida, v.familia, v.subfamilia, v.grupo, v.cantidad, v.costo_cup, v.costo_total,
           v.precio_unitario, v.total,
           case when v.es_ajuste then v.total - v.costo_total
                else v.total - v.costo_total * coalesce(a.factor, 1::numeric) end as utilidad_bruta,
           v.mg_bruto, v.vendedor, v.cond_pago, v.empresa, v.proveedor_por_defecto, v.liquidar, v.tipo_venta,
           v.estatus_sku, v.ano, v.mes, v.dia, v.producto_key,
           v.es_distribucion as es_distribucion_calc,
           h.sucursal as home_suc,
           case
               when v.sucursal = 'SI-STK' and h.sucursal is not null and (h.vigente_desde is null or v.fecha_conta >= h.vigente_desde) then h.sucursal
               when v.sucursal = 'SI-STK' then 'SE'
               else coalesce(case v.sucursal
                                 when 'MT-STK' then 'MT'
                                 when 'LC-STK' then 'LC'
                                 when 'MR-STK' then 'MR'
                                 when 'CH-STK' then 'CH'
                                 when 'MP-STK' then 'MP'
                                 when 'OF-STK' then 'OF'
                                 when 'DM-STK' then 'CANAL DIGITAL'
                                 when 'SE-STK' then 'CANAL DIGITAL'
                                 else null end, v.sucursal)
           end as sucursal_logica_calc
      from (
            select id, doc_sap, folio, tipo_doc, fecha_conta, fecha_doc, codigo_cliente, nombre_cliente, procedencia,
                   sucursal, codigo_cm, id_procedencia, codigo_proveedor, descripcion, marca, unidad_medida, familia,
                   subfamilia, grupo, cantidad, costo_cup, costo_total, precio_unitario, total, mg_bruto, vendedor,
                   cond_pago, empresa, proveedor_por_defecto, liquidar, tipo_venta, estatus_sku, ano, mes, dia,
                   producto_key, es_distribucion, false as es_ajuste
              from ventas
            union all
            -- sin funciones de ventana: una rama simple deja que Postgres empuje los filtros
            -- (ano, fecha, codigo) hasta la tabla ventas y use sus indices
            select -id, 'AJUSTE-ERP-' || ano || '-' || mes, null,
                   'AJUSTE ERP', fecha, fecha, null, 'AJUSTE ERP', null,
                   sucursal, null::bigint, null::int, null, 'AJUSTE ERP (diferencia con el reporte Margen por vendedor)',
                   'AJUSTE ERP', null, 'AJUSTE ERP', 'AJUSTE ERP', 'AJUSTE ERP', 0, 0, venta - ganancia, 0, venta, null,
                   vendedor, null, null, null, null, 'NORMAL', null, ano, mes, extract(day from fecha)::int,
                   null, false, true
              from ajuste_venta_erp
           ) v
      left join vendedor_home h on h.vendedor = v.vendedor
      left join ajuste_costo_erp a on a.ano = v.ano and a.mes = v.mes and a.vendedor = v.vendedor
)
select id, doc_sap, folio, tipo_doc, fecha_conta, fecha_doc, codigo_cliente, nombre_cliente, procedencia, sucursal,
       sucursal_logica_calc as sucursal_logica, codigo_cm, id_procedencia, codigo_proveedor, descripcion, marca,
       unidad_medida, familia, subfamilia, grupo, cantidad, costo_cup, costo_total, precio_unitario, total,
       utilidad_bruta, mg_bruto, vendedor,
       case when home_suc is not null and home_suc = sucursal_logica_calc then vendedor else 'OTROS' end as vendedor_rpt,
       cond_pago, empresa, proveedor_por_defecto, liquidar, tipo_venta, estatus_sku, ano, mes, dia, producto_key,
       es_distribucion_calc as es_distribucion
  from base;

commit;
