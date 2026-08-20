-- PEDIDO_TOTAL (cantidad ya pedida al proveedor, por codigo -- total
-- empresa, no por bodega) viene del export SAP crudo (Inventario.xlsx,
-- igual que CUP/EMBALAJE/CLAS_*), pero no se capturo en el backfill
-- inicial de productos (migrations/004_inventario.sql) porque en ese
-- momento ningun reporte ya portado la usaba. Plan de Compra/Reposicion
-- (data_loader_obligatorios.get_plan_compra_reposicion) la necesita
-- para no sugerir comprar de nuevo lo que ya esta en camino.
alter table productos add column if not exists pedido_total numeric;
