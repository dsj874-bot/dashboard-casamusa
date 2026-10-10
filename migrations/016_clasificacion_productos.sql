-- =====================================================================
-- 016 - Clasificacion de productos (Forecast): ABC x XYZ + ciclo de vida
--
-- Pedido del usuario (2026-10-10): clasificar los productos de forma
-- profesional, dentro de Forecast, para la empresa y para cada sucursal,
-- con los ultimos 12 meses cerrados.
--
--   ABC  aporte al margen (v_ventas, ya ajustado al ERP): A hasta el 80%
--        del margen acumulado, B hasta el 95%, C el resto.
--   XYZ  regularidad de la demanda: coeficiente de variacion de las
--        unidades mensuales (X <= 0,5 < Y <= 1,0 < Z), contado desde la
--        primera venta si el producto es nuevo.
--   ciclo Nuevo (primera venta en los ultimos 6 meses), Activo, En declive
--        (ultimos 3 meses bajo la mitad del promedio de los 9 anteriores)
--        o Sin venta (con stock y sin venta en los 12 meses).
--
-- Cada recalculo es un clasificacion_calculo nuevo: las filas se insertan
-- por lotes y recien al final el calculo se marca completo; la pantalla
-- lee siempre el ultimo calculo completo. Asi nunca se ve un calculo a
-- medias, y un recalculo que falla no borra el anterior.
-- =====================================================================

begin;

create table if not exists clasificacion_calculo (
    id             serial primary key,
    desde          date not null,
    hasta          date not null,
    calculado_en   timestamptz not null default now(),
    calculado_por  text,
    completo       boolean not null default false
);

create table if not exists clasificacion_producto (
    calculo_id       int     not null references clasificacion_calculo(id) on delete cascade,
    alcance          text    not null,   -- 'EMPRESA' o codigo de sucursal (CH, MP, MT, MR, LC, SI)
    codigo           bigint  not null,
    venta            numeric not null default 0,
    margen           numeric not null default 0,
    unidades         numeric not null default 0,
    meses_con_venta  int     not null default 0,
    cv               numeric,
    abc              char(1),            -- null = sin venta en el periodo
    xyz              char(1),
    ciclo            text    not null,
    primera_venta    date,
    stock            numeric not null default 0,
    valor_inventario numeric not null default 0,
    venta_mensual    numeric not null default 0,   -- datos duros (igual que Plan de Compra)
    cobertura        numeric,                      -- meses; null = sin venta mensual
    abastecimiento   text,                         -- 'Stock', 'Pedido', 'Stock y pedido', 'Sin compras'
    proveedor        text,
    primary key (calculo_id, alcance, codigo)
);

create index if not exists idx_clasif_prod_alcance on clasificacion_producto (calculo_id, alcance, abc, xyz);

commit;
