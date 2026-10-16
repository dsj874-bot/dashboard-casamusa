-- =====================================================================
-- 021 - Analista IA "mente libre": esquema ia + usuario de solo lectura
--
-- Pedido del usuario (2026-10-16): que el Analista IA pueda cruzar toda la
-- informacion de Musa360 con sus propias consultas SQL. Para eso:
--   * esquema `ia` con VISTAS de solo lo que el analista puede ver, con
--     nombres de columnas claros. Quedan fuera: usuarios (hashes de
--     contrasenas), fotos de solicitudes, agente_log, tablas de control/ETL.
--   * rol `agente_lectura`: solo USAGE sobre `ia` y SELECT sobre sus vistas,
--     transacciones de solo lectura por defecto y tiempo maximo por consulta.
-- La contrasena del rol NO va en este archivo: la fija
-- scripts/clave_agente_lectura.py y queda en DATABASE_URL_LECTURA (.env y Vercel).
--
-- Criterios de negocio que heredan las vistas:
--   * ia.ventas sale de v_ventas: margen ya ajustado al ERP (desde 2026).
--   * ia.stock solo trae las 6 sucursales (la bodega "Todas" viene en 0).
-- =====================================================================

begin;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'agente_lectura') then
        create role agente_lectura login noinherit;
    end if;
end $$;
alter role agente_lectura set default_transaction_read_only = on;
alter role agente_lectura set statement_timeout = '15s';
alter role agente_lectura set idle_in_transaction_session_timeout = '30s';
alter role agente_lectura set search_path = ia;

create schema if not exists ia;

create or replace view ia.ventas as
select fecha_conta as fecha, ano, mes, dia, doc_sap, folio, tipo_doc, codigo_cliente, nombre_cliente,
       sucursal_logica as sucursal, sucursal as almacen, vendedor_rpt as vendedor, codigo_cm as codigo,
       descripcion, marca, familia, subfamilia, grupo, procedencia, tipo_venta as canal, cantidad,
       precio_unitario, total as venta, utilidad_bruta as margen, es_distribucion
  from public.v_ventas;

create or replace view ia.productos as
select codigo, referencia, descripcion, marca, familia, subfamilia, grupo, procedencia, embalaje, cup,
       pedido_total as unidades_en_pedido
  from public.productos;

create or replace view ia.stock as
select codigo, bodega as sucursal, coalesce(stock, 0) as stock, coalesce(transito, 0) as transito,
       coalesce(venta_mensual, 0) as venta_mensual
  from public.inventario_stock
 where bodega in ('San Isidro', 'Las Condes', 'Manuel Rodríguez', 'Matta', 'Chicureo', 'Maipú');

create or replace view ia.compras as
select n_orden_compra, codigo, fecha_creacion as fecha, nombre_proveedor as proveedor, marca, referencia,
       descripcion, cantidad_comprada, cantidad_pendiente, precio_unitario, precio_total as monto,
       total_pendiente as monto_pendiente, tipo_compra, sucursal
  from public.compras;

create or replace view ia.recepciones as
select n_recepcion, n_oc, codigo, fecha_recepcion as fecha, nombre_proveedor as proveedor, descripcion,
       cantidad, precio_clp, total_clp as monto, sucursal
  from public.recepciones;

create or replace view ia.cotizaciones as
select nro_cotizacion, fecha, almacen, codigo_cliente, nombre_cliente, vendedor, codigo, descripcion, marca,
       grupo, familia, cantidad, precio_unitario, total as monto, nro_pedido, fecha_pedido, nro_factura,
       fecha_factura, (nro_factura is not null) as facturada, (codigo in (100000, 100005)) as no_creado
  from public.cotizacion_linea;

create or replace view ia.clasificacion as
select alcance, codigo, abc, xyz, abc || xyz as clase, ciclo, venta as venta_12m, margen as margen_12m,
       meses_con_venta, venta_mensual, cobertura, stock
  from public.clasificacion_producto;

create or replace view ia.prioritarios as
select codigo_obligatorio as codigo, codigo_equivalente, descripcion, familia, subfamilia,
       procedencia_obligatoria as procedencia, meses_objetivo
  from public.productos_obligatorios;

create or replace view ia.no_comprar as select codigo, motivo from public.productos_no_comprar;
create or replace view ia.metas as select ano, mes, sucursal, vendedor, meta from public.metas;
create or replace view ia.presupuesto as select sucursal, ano, presupuesto_anual from public.presupuesto;
create or replace view ia.nivel_servicio as
select fecha, sucursal, valor_inventario, nivel_servicio from public.nivel_servicio_historico;
create or replace view ia.ne_x_facturar as select sucursal, vendedor, monto_ne from public.ne_x_facturar;
create or replace view ia.margen_erp as select ano, mes, vendedor, venta, ganancia from public.margen_erp;
create or replace view ia.dias_habiles as select fecha, es_habil, descripcion from public.dias_habiles_cl;
create or replace view ia.clientes_distribucion as
select codigo_cliente, nombre_cliente from public.clientes_distribucion;

create or replace view ia.solicitudes as
select id, 'SOL-' || lpad(id::text, 5, '0') as numero, tipo, subtipo, estado, sucursal, sucursal_destino,
       solicitante_nombre, explicacion, documento, tercero, cantidad_total, monto_total, creado_en,
       dictamen, aprobado1_en, aprobado2_en, ejecutado_en
  from public.solicitud;
create or replace view ia.solicitud_lineas as
select solicitud_id, codigo, descripcion_snap as descripcion, cantidad, cup_snap as cup
  from public.solicitud_linea;

grant usage on schema ia to agente_lectura;
grant select on all tables in schema ia to agente_lectura;

commit;
