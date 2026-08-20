-- Productos marcados por el usuario como "no se compra" -- afecta
-- solo Plan de Compra (Obligatorios y Segunda Linea): el producto
-- sigue visible con su stock real en Alertas/Distribucion, pero nunca
-- se sugiere comprarlo. Reemplaza tener que pedirle a Claude que
-- agregue un filtro SQL nuevo cada vez que aparece un caso como este
-- (ej. Conduit Fuerte) -- mismo espiritu que vendedor_home.
--
-- Tabla chica, mantenida a mano via /gestionar_productos_compra --
-- sin FK hacia productos.codigo (mismo criterio que
-- productos_obligatorios: la validez del codigo se controla en la
-- app, no en la base).
create table if not exists productos_no_comprar (
    codigo     bigint primary key,
    motivo     text,
    updated_at timestamptz not null default now(),
    updated_by text
);
