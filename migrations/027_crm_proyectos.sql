-- =====================================================================
-- 027 - CRM de proyectos (piloto San Isidro)
--
-- Por que (2026-11-26): en 2026 se cotizaron 11.349 MM en proyectos de
-- mas de $20 MM con 5% de conversion y sin seguimiento: nadie registra
-- que paso despues de enviar la cotizacion, por que se perdio ni quien la
-- tiene (16 oportunidades por 421 MM quedaron sin dueño por renuncias).
--
-- Modelo (decidido con el usuario sobre una maqueta):
-- - Oportunidad = cotizaciones de $5 MM o mas del mismo cliente con el
--   mismo vendedor, a menos de 60 dias entre versiones. La arma sola
--   data_loader_crm_pg.sincronizar_pg() desde cotizacion_linea.
-- - Etapas: cotizada -> seguimiento -> negociacion -> ganada | perdida.
--   Ganada es automatica (pedido o factura en la cotizacion); las del
--   medio y perdida las mueve el vendedor.
-- - Gestiones: lo que hizo el vendedor (llamada, visita...) + su proximo
--   compromiso con fecha.
-- - Alertas: compromiso vencido o falta de gestiones. La escalera
--   (vendedor -> jefe de sucursal -> lider de ventas -> GG) se calcula
--   desde las fechas; crm_alerta guarda el registro de lo avisado.
-- =====================================================================

begin;

create table if not exists crm_oportunidad (
    id               bigint generated always as identity primary key,
    codigo_cliente   text,
    cliente          text,
    vendedor         text not null,          -- vendedor original (ERP)
    dueno            text not null,          -- vendedor a cargo hoy (reasignable)
    sucursal         text,                   -- sucursal de vendedor_home del dueño
    etapa            text not null default 'cotizada'
                     check (etapa in ('cotizada','seguimiento','negociacion','ganada','perdida')),
    probabilidad     int check (probabilidad between 0 and 100),
    monto            numeric not null default 0,   -- ultima version cotizada
    monto_ganado     numeric,
    primera_cot      date,
    ultima_cot       date,
    fecha_cierre_est date,
    obra             text,
    comuna           text,
    competidor       text,
    motivo_perdida   text,
    notas            text,
    proxima_accion   text,
    fecha_compromiso date,
    ultima_gestion   timestamptz,
    ganada_en        date,
    cerrada_en       date,
    creada_en        timestamptz not null default now(),
    actualizada_en   timestamptz not null default now(),
    actualizada_por  text
);
create index if not exists crm_oportunidad_dueno_idx on crm_oportunidad (dueno);
create index if not exists crm_oportunidad_abiertas_idx on crm_oportunidad (codigo_cliente, vendedor)
    where etapa not in ('ganada','perdida');

create table if not exists crm_oportunidad_cotizacion (
    nro_cotizacion  bigint primary key,
    oportunidad_id  bigint not null references crm_oportunidad(id) on delete cascade,
    fecha           date,
    monto           numeric,
    convertida      boolean not null default false,
    monto_convertido numeric,
    fecha_conversion date
);
create index if not exists crm_opcot_op_idx on crm_oportunidad_cotizacion (oportunidad_id);

create table if not exists crm_gestion (
    id               bigint generated always as identity primary key,
    oportunidad_id   bigint not null references crm_oportunidad(id) on delete cascade,
    tipo             text not null,   -- llamada, visita, correo, whatsapp, reunion, nota, cambio, sistema
    nota             text,
    proxima_accion   text,
    fecha_compromiso date,
    usuario          text,
    creada_en        timestamptz not null default now()
);
create index if not exists crm_gestion_op_idx on crm_gestion (oportunidad_id, creada_en desc);

-- Registro de alertas avisadas (una fila por oportunidad + motivo + fecha
-- de referencia; el nivel sube con la escalera y queda la huella de hasta
-- donde se aviso por correo).
create table if not exists crm_alerta (
    id               bigint generated always as identity primary key,
    oportunidad_id   bigint not null references crm_oportunidad(id) on delete cascade,
    motivo           text not null check (motivo in ('compromiso_vencido','sin_gestion','sin_dueno')),
    referencia       date not null,
    nivel            int not null default 0,
    creada_en        timestamptz not null default now(),
    escalada_en      timestamptz,
    resuelta_en      timestamptz,
    unique (oportunidad_id, motivo, referencia)
);

commit;
