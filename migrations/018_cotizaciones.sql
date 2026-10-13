-- =====================================================================
-- 018 - Cotizaciones (pantalla "Cotizaciones" de Comercial)
--
-- Pedido del usuario (2026-10-13), tras el analisis de las cotizaciones
-- 2026: el reporte de cotizaciones del ERP trae una fila por linea
-- cotizada con su pedido, entrega y factura/boleta. Nro_Factura_Boleta
-- es el doc_sap de v_ventas (cruza 100%), asi que la conversion sale de
-- la propia cotizacion, sin aproximar.
--
-- Carga: el Excel se lee en el navegador y llega por partes (el de enero
-- pesa 4,7 MB y Vercel corta los cuerpos sobre 4,5 MB). Cada parte trae
-- cotizaciones completas; se borran esas cotizaciones y se insertan de
-- nuevo, asi que volver a subir un mes actualiza pedidos y facturas sin
-- duplicar.
--
-- Codigos 100000/100005 (marca SERV) = productos NO creados en el
-- catalogo. Si el cliente acepta se crea un codigo especial 6xxxxx y se
-- factura con ese codigo (ver memoria del analisis).
-- =====================================================================

begin;

create table if not exists cotizacion_linea (
    id               bigserial primary key,
    nro_cotizacion   bigint  not null,
    fecha            date    not null,
    almacen          text    not null,       -- SI-STK, LC-STK, ...
    codigo_cliente   text,
    nombre_cliente   text,
    vendedor         text,
    codigo           bigint  not null,
    descripcion      text,
    marca            text,
    grupo            text,                   -- Nacional / Importado
    familia          text,
    cantidad         numeric not null,
    precio_unitario  numeric not null,
    total            numeric not null,
    nro_pedido       bigint,
    fecha_pedido     date,
    nro_entrega      bigint,
    fecha_entrega    date,
    nro_factura      bigint,                 -- = v_ventas.doc_sap
    fecha_factura    date
);

create index if not exists cotizacion_linea_nro_idx      on cotizacion_linea (nro_cotizacion);
create index if not exists cotizacion_linea_fecha_idx    on cotizacion_linea (fecha);
create index if not exists cotizacion_linea_codigo_idx   on cotizacion_linea (codigo);

create table if not exists cotizacion_carga (
    id            serial primary key,
    archivo       text,
    filas         integer not null,
    cotizaciones  integer not null,
    fecha_min     date,
    fecha_max     date,
    cargado_por   text,
    cargado_en    timestamptz not null default now()
);

commit;
