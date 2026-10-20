-- Venta mensual sin proyectos de los productos prioritarios, precalculada
-- (2026-10-20). La usa el Plan de Compra de Prioritarios en vez de recorrer
-- v_ventas en cada carga de la pantalla (que la dejaba en 10+ s).
-- Se recalcula cada noche en /api/cron/confirmar_fecha y, si falta el mes
-- vigente, la primera vez que se abre la pantalla.
-- Ver data_loader_obligatorios_pg.recalcular_venta_sin_proyectos_pg().
CREATE TABLE IF NOT EXISTS venta_sin_proyectos (
    codigo             bigint PRIMARY KEY,
    venta              numeric NOT NULL,   -- unidades/mes sin facturas de proyecto
    proyectos          numeric NOT NULL,   -- unidades/mes en facturas de proyecto
    facturas_proyecto  integer NOT NULL,
    desde              date NOT NULL,
    hasta              date NOT NULL,
    calculado_en       timestamptz NOT NULL DEFAULT now()
);
