-- =====================================================================
-- 026 - Finanzas > Proveedores: programa de pagos a proveedores
--
-- Por que (2026-11-17): el usuario quiere controlar cuando pagar a cada
-- proveedor (60 dias desde la recepcion; Philips/Signify y Zama a 30;
-- Legrand a 45 como en el ERP) cruzado con el estado de pago de SAP.
--
-- Fuente: el informe de SAP "Pago a Proveedores" (una fila por OC +
-- recepcion, con total recepcionado, facturado, pagado y estado). Se sube
-- desde la pantalla y cada carga REEMPLAZA a la anterior (es una foto).
-- Se carga por partes con un carga_id; al terminar (/fin) se marca la
-- carga como completa y se borran las filas de cargas anteriores, asi una
-- subida a medias nunca deja la pantalla vacia ni mezclada.
--
-- Montos: tal como vienen del informe (CON IVA).
-- =====================================================================

begin;

create table if not exists pago_proveedor_carga (
    carga_id     bigint generated always as identity primary key,
    archivo      text,
    filas        int,
    completa     boolean not null default false,
    cargado_por  text,
    cargado_en   timestamptz not null default now()
);

create table if not exists pago_proveedor_linea (
    carga_id         bigint not null references pago_proveedor_carga(carga_id) on delete cascade,
    n_oc             bigint,
    fecha_oc         date,
    codigo_proveedor text,
    proveedor        text,
    total_oc         numeric,
    n_recepcion      bigint,
    fecha_recepcion  date,
    total_recepcion  numeric,
    n_facturas       int,
    total_facturado  numeric,
    total_pagado     numeric,
    estado           text
);
create index if not exists idx_pago_prov_linea_carga on pago_proveedor_linea (carga_id);

-- Plazo de pago por proveedor (dias desde la recepcion). Sin fila = 60.
-- sin_vinculo: SAP no amarra las facturas de ese proveedor a sus
-- recepciones (Legrand desde abril-2026), asi que su estado de pago no
-- sirve: se asume pagado lo ya vencido y se programa lo que viene.
create table if not exists plazo_proveedor (
    codigo_proveedor text primary key,
    nombre           text,
    dias             int not null,
    sin_vinculo      boolean not null default false,
    updated_by       text,
    updated_at       timestamptz not null default now()
);

insert into plazo_proveedor (codigo_proveedor, nombre, dias, sin_vinculo, updated_by) values
    ('P76479569-5', 'SIGNIFY CHILENA S.A. (PHILIPS)', 30, false, 'migracion 026'),
    ('P78507060-7', 'PLASTICOS ZAMA LTDA',            30, false, 'migracion 026'),
    ('P79602730-4', 'LEGRAND BTICINO CHILE SPA',      45, true,  'migracion 026')
on conflict (codigo_proveedor) do nothing;

commit;
