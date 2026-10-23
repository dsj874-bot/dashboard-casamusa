-- =====================================================================
-- 024 - Fotos historicas del inventario por producto (2026-10-23)
--
-- Por que: inventario_stock es solo la ultima foto (cada carga reemplaza
-- la anterior), asi que no se puede cruzar la venta de una fecha pasada
-- con el stock que habia ese dia (ej. "¿bajo la venta de agosto porque
-- hubo quiebre?"). nivel_servicio_historico solo guarda el valor total
-- por sucursal.
--
-- Decision del usuario ("opcion A"): una foto cada lunes y el ultimo dia
-- de cada mes, no diaria, para cuidar el espacio de la base (~110 MB al
-- año en vez de ~650 MB). Solo productos con stock o transito distinto
-- de cero, en las 6 sucursales y E-commerce.
--
-- La toma el cron diario /api/cron/nivel_servicio_snapshot con
-- data_loader_inventario_foto.guardar_foto_si_corresponde_pg(). Para
-- fechas sin foto, el Analista IA estima el stock de la empresa hacia
-- atras desde la foto siguiente: stock - recepciones + ventas.
-- =====================================================================

begin;

create table if not exists inventario_foto (
    fecha     date    not null,   -- dia del inventario fotografiado (inventario_control.cargado_en, hora de Chile)
    codigo    bigint  not null,
    bodega    text    not null,
    stock     numeric not null,
    transito  numeric not null,
    primary key (fecha, codigo, bodega)
);
-- sin indice extra por codigo: la PK (fecha, codigo, bodega) ya sirve para las consultas
-- por fecha + codigos, y asi cada foto ocupa ~1,9 MB en vez de ~2,2 MB (~110 MB al año).

create or replace view ia.stock_fotos as
select fecha, codigo, bodega as sucursal, stock, transito
  from public.inventario_foto;
grant select on ia.stock_fotos to agente_lectura;

commit;
