-- Fase 2: dominio Inventario. `productos` (creada en 002_fase1_comercial.sql)
-- ya tiene todas las columnas de producto necesarias (incluye clas_si/lc/
-- mr/mt/csd, cup, familia/subfamilia/grupo/marca) -- se penso para esto.
-- Solo falta el stock por bodega, que es "foto" (se reemplaza entero en
-- cada carga, no se acumula como ventas) -- formato largo (codigo, bodega)
-- en vez de columnas anchas STOCK_X/TRANSITO_X por bodega, para que
-- agrupar por bodega sea un GROUP BY normal en vez de sumar columna por
-- columna en Python.

-- Sin FK hacia productos.codigo -- a proposito, mismo criterio que
-- ventas (ver 002_fase1_comercial.sql): verificar la llave foranea
-- fila por fila hace que una carga de ~250k filas (19072 productos x
-- 13 bodegas) se vuelva ordenes de magnitud mas lenta contra el
-- pooler de Supabase (probado: sin la FK, la misma carga que habia
-- tardado >30 min paso a demorar un par de minutos).
create table if not exists inventario_stock (
    codigo bigint not null,
    bodega text not null,
    stock numeric,             -- null = esta bodega no tiene columna de stock (ej. Servicio Tecnico)
    transito numeric,          -- null = esta bodega no tiene columna de transito
    venta_mensual numeric,     -- null = esta bodega no reporta venta mensual propia
    primary key (codigo, bodega)
);

create index if not exists idx_inventario_stock_bodega on inventario_stock (bodega);

-- Control de la ultima carga de inventario (analogo a control_datos,
-- pero aqui solo importa "cuando se cargo" -- no hay corte de fecha
-- de ventas que calcular, es una foto completa cada vez).
create table if not exists inventario_control (
    id boolean primary key default true check (id),  -- fila unica
    archivo_origen text,
    cargado_en timestamptz not null default now(),
    filas integer
);
