-- =====================================================================
-- 017 - Solicitudes de Control de Inventario (etapa 1: registrar)
--
-- Pedido del usuario (2026-10-10): una pantalla para ingresar las
-- solicitudes de control de inventario -- entrada de mercaderia, salida,
-- merma, postventa, garantia. Solo registra el estado de la solicitud;
-- NO mueve stock en el ERP.
--
-- Roles acordados: el solicitante (jefe de sucursal) crea; un aprobador
-- aprueba segun monto/cantidad (etapa 2); el Encargado de Control de
-- Inventario (ECI, C. Aliaga) administra y cierra. Las salidas pasan a
-- analisis del ECI y terminan en un dictamen (etapa 2).
--
-- Respaldo por tipo: entrada y merma con foto obligatoria; salida con
-- explicacion escrita obligatoria; postventa con N de boleta/factura
-- opcional; garantia con la falla descrita.
--
-- Las fotos se guardan en la base (bytea): se comprimen en el celular a
-- ~300 KB antes de subir, y el volumen esperado es bajo. Si crece, se
-- pasan a Supabase Storage sin cambiar el resto.
--
-- Las lineas guardan descripcion_snap y cup_snap: el CUP cambia con cada
-- carga de inventario y el monto de una solicitud no debe cambiar solo.
-- =====================================================================

begin;

create table if not exists solicitud (
    id                 serial primary key,
    tipo               text not null,          -- ENTRADA, SALIDA, MERMA, POSTVENTA, GARANTIA
    subtipo            text,
    estado             text not null default 'abierta',
    sucursal           text not null,          -- CH, MP, MT, MR, LC, SI
    sucursal_destino   text,                   -- traspasos
    solicitante        text not null,          -- correo/usuario de Musa360
    solicitante_nombre text,
    explicacion        text,
    documento          text,                   -- boleta, factura, OC, guia (opcional)
    tercero            text,                   -- cliente o proveedor
    cantidad_total     numeric not null default 0,
    monto_total        numeric not null default 0,
    creado_en          timestamptz not null default now(),
    actualizado_en     timestamptz not null default now()
);
create index if not exists idx_solicitud_estado on solicitud (estado, creado_en desc);
create index if not exists idx_solicitud_sucursal on solicitud (sucursal, creado_en desc);

create table if not exists solicitud_linea (
    id                serial primary key,
    solicitud_id      int not null references solicitud(id) on delete cascade,
    codigo            bigint not null,
    descripcion_snap  text,
    cantidad          numeric not null,
    cup_snap          numeric not null default 0
);
create index if not exists idx_solicitud_linea_sol on solicitud_linea (solicitud_id);
create index if not exists idx_solicitud_linea_cod on solicitud_linea (codigo);

create table if not exists solicitud_foto (
    id            serial primary key,
    solicitud_id  int not null references solicitud(id) on delete cascade,
    nombre        text,
    mime          text not null default 'image/jpeg',
    bytes         int,
    datos         bytea not null,
    subido_por    text,
    subido_en     timestamptz not null default now()
);
create index if not exists idx_solicitud_foto_sol on solicitud_foto (solicitud_id);

create table if not exists solicitud_evento (
    id            serial primary key,
    solicitud_id  int not null references solicitud(id) on delete cascade,
    ts            timestamptz not null default now(),
    usuario       text,
    estado_desde  text,
    estado_hasta  text,
    comentario    text
);
create index if not exists idx_solicitud_evento_sol on solicitud_evento (solicitud_id, ts);

commit;
