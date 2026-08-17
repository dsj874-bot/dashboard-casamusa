-- Fase 1: dominio Comercial/Ventas.
--
-- Sin FK enforced hacia productos.codigo todavia -- se agrega despues
-- de correr un chequeo de codigos huerfanos sobre datos reales (ver
-- plan de migracion, seccion "On foreign keys").

create table if not exists usuarios (
    id bigserial primary key,
    email text not null unique,
    password_hash text not null,
    nombre text not null,
    admin boolean not null default false,
    sucursales text[] not null default '{}',
    activo boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Dimension compartida por las 4 fases (Comercial, Inventario,
-- Adquisiciones, Obligatorios). Se crea completa ahora aunque Fase 1
-- solo use algunas columnas, para no tener que alterarla despues.
create table if not exists productos (
    codigo bigint primary key,
    referencia text,
    descripcion text,
    um text,
    familia text,
    subfamilia text,
    grupo text,
    marca text,
    id_procedencia integer,
    procedencia text generated always as (
        case when id_procedencia in (3, 7) then 'Importado' else 'Nacional' end
    ) stored,
    embalaje integer,
    multiplo numeric,
    cup numeric,
    clas_si text,
    clas_lc text,
    clas_mr text,
    clas_mt text,
    clas_csd text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Sin llave unica natural (el export SAP puede repetir DOC_SAP/FOLIO
-- en documentos multilinea). El dedupe en la carga es por rango de
-- fecha: DELETE ... WHERE fecha_conta = ANY(fechas_del_archivo) +
-- INSERT, igual que el mecanismo actual basado en Excel.
create table if not exists ventas (
    id bigserial primary key,
    doc_sap text,
    folio text,
    tipo_doc text,
    fecha_conta date not null,
    fecha_doc date,
    codigo_cliente text,
    nombre_cliente text,
    procedencia text,
    sucursal text,
    sucursal_logica text,
    codigo_cm bigint,
    id_procedencia integer,
    codigo_proveedor text,
    descripcion text,
    marca text,
    unidad_medida text,
    familia text,
    subfamilia text,
    grupo text,
    cantidad numeric,
    costo_cup numeric,
    costo_total numeric,
    precio_unitario numeric,
    total numeric,
    utilidad_bruta numeric,
    mg_bruto numeric,
    vendedor text,
    vendedor_rpt text,
    cond_pago text,
    empresa text,
    proveedor_por_defecto text,
    liquidar text,
    tipo_venta text,
    estatus_sku text,
    ano integer,
    mes integer,
    dia integer,
    producto_key text
);

create index if not exists idx_ventas_fecha_conta on ventas (fecha_conta);
create index if not exists idx_ventas_sucursal_logica_fecha on ventas (sucursal_logica, fecha_conta);
create index if not exists idx_ventas_codigo_cm on ventas (codigo_cm);
create index if not exists idx_ventas_vendedor on ventas (vendedor);

create table if not exists metas (
    ano integer not null,
    mes integer not null,
    sucursal text not null,
    vendedor text not null,
    meta numeric not null default 0,
    primary key (ano, mes, sucursal, vendedor)
);

create table if not exists presupuesto (
    sucursal text not null,
    ano integer not null,
    presupuesto_anual numeric not null default 0,
    primary key (sucursal, ano)
);

create table if not exists ne_x_facturar (
    sucursal text not null,
    vendedor text not null,
    monto_ne numeric not null default 0,
    updated_at timestamptz not null default now(),
    updated_by text,
    primary key (sucursal, vendedor)
);

-- control_datos (reemplazo de data/comercial/fecha_confirmada.txt) ya
-- existe desde la migracion 001_control_datos.sql.

-- Bitacora de cada carga -- reemplaza actualizar_diario.log y le da
-- al boton "Subir archivo" algo que mostrarle al usuario despues de
-- cada carga.
create table if not exists etl_runs (
    id bigserial primary key,
    area text not null,
    archivo_nombre text,
    filas_afectadas integer,
    iniciado_en timestamptz not null default now(),
    terminado_en timestamptz,
    resultado text,
    mensaje text
);

-- Reemplaza el loop Python + set FERIADOS_CL hardcodeado en
-- data_loader.py. Poblada 2020-2035 desde el set actual (ver script
-- de backfill).
create table if not exists dias_habiles_cl (
    fecha date primary key,
    es_habil boolean not null,
    descripcion text
);
