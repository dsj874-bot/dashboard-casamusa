-- productos_obligatorios ("Prioridad") pasa de ser un import unico
-- desde Excel a una tabla editable desde /gestionar_prioridad
-- (promover un producto de Segunda Linea, o quitarlo) -- agrega
-- updated_by para saber quien hizo cada cambio, mismo criterio que
-- vendedor_home y productos_no_comprar.
alter table productos_obligatorios add column if not exists updated_by text;
