-- Fase 0 (esqueleto caminante): unica tabla necesaria para probar que
-- Vercel se conecta a Supabase. control_datos ademas es la tabla real
-- que en Fase 1 reemplaza a data/comercial/fecha_confirmada.txt, asi
-- que no es una tabla descartable.

create table if not exists control_datos (
    area text primary key,
    fecha_confirmada date,
    updated_at timestamptz not null default now(),
    updated_by text
);
