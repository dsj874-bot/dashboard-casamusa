-- =====================================================================
-- 028 - Cotizador (demo solo dsepulveda, 2026-11-26)
--
-- Por que: los requerimientos de los clientes llegan con su propia forma
-- de escribir los productos. El cotizador reconoce cada linea contra el
-- catalogo y sugiere importado primero, luego la marca que pidio el
-- cliente, luego nacional, con el precio de la lista de SAP; despues se
-- exporta a Excel / portapapeles para crear la cotizacion en SAP.
--
-- precio_lista: export "Precio_Venta" de SAP (data/comercial/Precios/<mes>/
--   AAMM_Precio_Venta.xlsx), una fila por codigo con las 6 listas. Cada
--   carga reemplaza la anterior (foto). Medido el 26-11: D1..D4 = 60/64/66/68%
--   de la base, y lo vendido en noviembre calza con D1 (0,99).
-- cotizador_aprendizaje: lo que el usuario confirmo (texto del cliente ->
--   codigo), para reconocerlo solo la proxima vez.
-- =====================================================================

begin;

create table if not exists precio_lista (
    codigo       bigint primary key,
    lp_base_cm   numeric,
    lp_base_cmd  numeric,
    lp_d1_cm     numeric,
    lp_d2_cm     numeric,
    lp_d3_cm     numeric,
    lp_d4_cm     numeric,
    archivo      text,
    cargado_en   timestamptz not null default now()
);

create table if not exists cotizador_aprendizaje (
    texto_norm     text not null,
    codigo         bigint not null,
    veces          int not null default 1,
    usuario        text,
    actualizado_en timestamptz not null default now(),
    primary key (texto_norm, codigo)
);

commit;
