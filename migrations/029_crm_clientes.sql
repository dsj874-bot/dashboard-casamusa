-- =====================================================================
-- 029 - CRM de clientes (2026-11-27)
--
-- Por que: el CRM seguia cotizaciones, no clientes. En San Isidro hay 17
-- clientes frecuentes que llevan 60+ dias sin comprar (264 MM/año), 39
-- grandes que compran menos de la mitad de su ritmo (672 MM/año) y 357
-- clientes cuyo vendedor ya no esta (1.112 MM en 24 meses).
--
-- cliente_maestro: maestro de socios de negocio de SAP (data/comercial/
--   Clientes/Maestro de Clientes.xlsx), se carga con scripts/cargar_clientes.py
--   y cada carga REEMPLAZA la anterior. Trae datos personales (contacto,
--   telefono, correo, RUT de personas naturales): NO exponer en el esquema `ia`
--   del Analista IA ni fuera de las pantallas del CRM.
-- crm_cliente: lo que el CRM agrega al cliente (dueño manual, notas,
--   proximo compromiso). El dueño por defecto se calcula (ver
--   data_loader_crm_clientes_pg).
-- crm_cliente_gestion: llamadas, visitas, etc. con el cliente.
-- =====================================================================

begin;

create table if not exists cliente_maestro (
    codigo_cliente     text primary key,
    razon_social       text,
    rut                text,
    direccion          text,
    comuna             text,
    ciudad             text,
    region             text,
    contacto           text,
    telefono           text,
    celular            text,
    correo             text,
    vendedor_sap       text,
    condicion_pago     text,
    linea_credito      numeric,
    saldo_cuenta       numeric,
    pedidos_pendientes numeric,
    credito_disponible numeric,
    congelado          text,
    archivo            text,
    cargado_en         timestamptz not null default now()
);

create table if not exists crm_cliente (
    codigo_cliente   text primary key,
    dueno            text,              -- asignado a mano (NULL = el calculado)
    notas            text,
    proxima_accion   text,
    fecha_compromiso date,
    ultima_gestion   timestamptz,
    actualizada_en   timestamptz not null default now(),
    actualizada_por  text
);

create table if not exists crm_cliente_gestion (
    id               bigint generated always as identity primary key,
    codigo_cliente   text not null,
    tipo             text not null,     -- llamada, visita, correo, whatsapp, reunion, nota, cambio
    nota             text,
    proxima_accion   text,
    fecha_compromiso date,
    usuario          text,
    creada_en        timestamptz not null default now()
);
create index if not exists crm_cliente_gestion_idx on crm_cliente_gestion (codigo_cliente, creada_en desc);

commit;

-- La ficha del cliente consulta ventas por codigo_cliente (sin indice tomaba ~4 s por cliente).
create index if not exists idx_ventas_codigo_cliente_fecha on ventas (codigo_cliente, fecha_conta);

-- Cartera precalculada (la consulta de 24 meses sobre ventas toma ~25 s): la deja el cron diario
-- del CRM y la pantalla la lee; si falta o tiene mas de un dia, se recalcula al vuelo.
create table if not exists crm_cartera_cache (
    id            int primary key default 1 check (id = 1),
    calculado_en  timestamptz not null default now(),
    datos         jsonb not null
);
