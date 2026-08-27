-- Datos Duros de Inventario (Familia/Subfamilia/Grupo/Venta Mensual
-- por sucursal) en Postgres -- antes vivia SOLO como un Excel local
-- (Datos_Duros_Inventario.xlsx) que /api/subir_inventario fusionaba
-- en el momento de cada carga de stock. En Vercel el filesystem es de
-- solo lectura fuera de /tmp, asi que ese Excel no podia persistir
-- entre una subida de Datos Duros y la siguiente subida de Inventario
-- (dos requests separados, semanas de diferencia) -- bug real
-- encontrado 2026-08-27 (el usuario probo subir Datos Duros en
-- produccion y crasheo con "Read-only file system").
--
-- Se preserva la MISMA semantica que tenia el Excel: subir Datos
-- Duros NO actualiza productos/inventario_stock al instante, solo
-- queda disponible para que la SIGUIENTE subida de Inventario lo
-- fusione (ver fusionar_datos_duros_pg en data_loader_inventario_pg.py) --
-- exactamente lo que ya promete la propia pantalla de subida.
--
-- Venta mensual en tabla LARGA (codigo, sucursal) en vez de columnas
-- anchas "VENTA MENSUAL <sucursal>" -- para no requerir una migracion
-- cada vez que se agrega una sucursal nueva (ya paso con Maipu; con
-- columnas anchas habria significado ALTER TABLE + tocar codigo).

create table if not exists datos_duros (
    codigo      bigint primary key,
    familia     text,
    subfamilia  text,
    grupo       text,
    updated_at  timestamptz not null default now()
);

create table if not exists datos_duros_venta_mensual (
    codigo         bigint not null,
    sucursal       text not null,  -- sufijo de "VENTA MENSUAL <sucursal>" del Excel, ej. "MAIPU", "CONSOLIDADA"
    venta_mensual  numeric,
    primary key (codigo, sucursal)
);
