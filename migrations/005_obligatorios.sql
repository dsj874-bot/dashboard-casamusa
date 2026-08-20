-- Productos Obligatorios: la lista de productos criticos que "no
-- pueden faltar" en ninguna sucursal (antes Productos_Obligatorios.xlsx,
-- mantenida a mano, Familia por Familia). Tabla chica (~530 filas hoy),
-- sin problema de performance de carga como ventas/inventario_stock --
-- no necesita el patron de "sin FK" ni carga por lotes.
--
-- codigo_obligatorio es la llave natural: no hay dos filas para el
-- mismo codigo en el Excel original (verificado antes de migrar).
create table if not exists productos_obligatorios (
    codigo_obligatorio      bigint primary key,
    familia                 text not null,
    subfamilia              text not null,
    grupo                   text,
    descripcion             text not null,
    procedencia_obligatoria text not null,
    codigo_equivalente      bigint,
    meses_objetivo          numeric,
    updated_at              timestamptz not null default now()
);
