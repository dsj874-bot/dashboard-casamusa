-- Historico diario de Nivel de Servicio (general + por sucursal) --
-- pedido explicito del usuario: "necesito que tengamos un dato diario
-- ... necesito ver este dato historico". Antes de esto, Nivel de
-- Servicio era una foto de HOY, sin rastro de dias anteriores.
--
-- Una fila por dia y por sucursal, mas una fila "TOTAL" para el
-- general (mismo shape que devuelve get_nivel_servicio_pg: "general" +
-- "por_sucursal"). Se llena via cron diario (ver /api/cron/
-- nivel_servicio_snapshot en app.py) -- no hay forma de reconstruir
-- dias anteriores a cuando se activo esto: inventario_stock es una
-- foto que se reemplaza entera en cada carga, no queda rastro de como
-- estaba ayer.

create table if not exists nivel_servicio_historico (
    fecha             date not null,
    sucursal          text not null,  -- 'TOTAL' para el general, o el nombre de la sucursal
    valor_inventario  numeric,
    nivel_servicio    numeric,
    capturado_en      timestamptz not null default now(),
    primary key (fecha, sucursal)
);

create index if not exists idx_nivel_servicio_historico_sucursal on nivel_servicio_historico (sucursal);
