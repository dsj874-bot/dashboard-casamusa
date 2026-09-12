-- Clientes de Distribucion: lista maestra que mantiene el usuario
-- subiendo un Excel exportado de SAP (columnas "Codigo SN" / "Nombre
-- SN"; son los clientes con lista de precios 53 / distribuidor
-- LD_DD_CMD). Sirve para poder mirar ese negocio por separado del
-- resto en los reportes de Comercial.
--
-- Se guarda la lista COMPLETA, incluidos los clientes que todavia no
-- tienen ninguna venta cargada (al 2026-09-12 eran 44 de 107): son
-- cuentas dadas de alta en SAP que aun no compran, y dejarlas
-- registradas hace que queden marcadas solas el dia que compren, sin
-- tener que volver a subir el Excel.

create table if not exists clientes_distribucion (
    codigo_cliente text primary key,        -- tal cual viene en el Excel
    rut_norm       text not null,           -- ver nota de normalizacion
    nombre_cliente text,
    actualizado_en timestamptz not null default now(),
    actualizado_por text
);

-- El calce se hace por RUT normalizado y no por el codigo tal cual:
-- el mismo cliente puede venir en el Excel sin el cero a la izquierda
-- que si trae la tabla de ventas (caso real: C9713599-1 en el Excel
-- contra C09713599-1 en ventas). Normalizar = sacar el prefijo C,
-- puntos y guiones, y los ceros a la izquierda.
create index if not exists idx_clientes_distribucion_rut on clientes_distribucion (rut_norm);

-- v_ventas + es_distribucion. Misma definicion de
-- migrations/003_vendedor_home.sql (ver ahi el detalle de
-- sucursal_logica/vendedor_rpt), sumando la marca de Distribucion.
-- Medido: el join normalizado no encarece las consultas agregadas
-- (0.68s contra 0.72s sin el).
create or replace view v_ventas as
with base as (
    select
        v.id, v.doc_sap, v.folio, v.tipo_doc, v.fecha_conta, v.fecha_doc,
        v.codigo_cliente, v.nombre_cliente, v.procedencia, v.sucursal,
        v.codigo_cm, v.id_procedencia, v.codigo_proveedor, v.descripcion,
        v.marca, v.unidad_medida, v.familia, v.subfamilia, v.grupo,
        v.cantidad, v.costo_cup, v.costo_total, v.precio_unitario, v.total,
        v.utilidad_bruta, v.mg_bruto, v.vendedor, v.cond_pago, v.empresa,
        v.proveedor_por_defecto, v.liquidar, v.tipo_venta, v.estatus_sku,
        v.ano, v.mes, v.dia, v.producto_key,
        (cd.codigo_cliente is not null) as es_distribucion_calc,
        h.sucursal as home_suc,
        case
            -- SI-STK (bodega compartida): reatribuir a la sucursal home
            -- del vendedor, salvo que la venta sea anterior a su fecha
            -- de traspaso (VEND_HOME_DESDE) -- en ese caso cae al default.
            when v.sucursal = 'SI-STK' and h.sucursal is not null
                 and (h.vigente_desde is null or v.fecha_conta >= h.vigente_desde)
                then h.sucursal
            when v.sucursal = 'SI-STK' then 'SE'
            else coalesce(
                case v.sucursal
                    when 'MT-STK' then 'MT' when 'LC-STK' then 'LC' when 'MR-STK' then 'MR'
                    when 'CH-STK' then 'CH' when 'MP-STK' then 'MP' when 'OF-STK' then 'OF'
                    when 'DM-STK' then 'CANAL DIGITAL' when 'SE-STK' then 'CANAL DIGITAL'
                end,
                v.sucursal
            )
        end as sucursal_logica_calc
    from ventas v
    left join vendedor_home h on h.vendedor = v.vendedor
    left join clientes_distribucion cd
           on cd.rut_norm = ltrim(regexp_replace(upper(coalesce(v.codigo_cliente, '')), '^C|[.-]', '', 'g'), '0')
)
select
    id, doc_sap, folio, tipo_doc, fecha_conta, fecha_doc, codigo_cliente,
    nombre_cliente, procedencia, sucursal,
    sucursal_logica_calc as sucursal_logica,
    codigo_cm, id_procedencia, codigo_proveedor, descripcion, marca, unidad_medida,
    familia, subfamilia, grupo, cantidad, costo_cup, costo_total, precio_unitario,
    total, utilidad_bruta, mg_bruto, vendedor,
    -- Nombre real solo si es home DE ESA sucursal logica (igual que
    -- pair_key.isin(_HOME_PAIRS) en pandas); no depende de la fecha de
    -- traspaso por si sola -- si la reatribucion de arriba no lo dejo
    -- en su sucursal home, esta comparacion ya falla sola.
    case when home_suc is not null and home_suc = sucursal_logica_calc
         then vendedor else 'OTROS' end as vendedor_rpt,
    cond_pago, empresa, proveedor_por_defecto, liquidar, tipo_venta, estatus_sku,
    ano, mes, dia, producto_key,
    es_distribucion_calc as es_distribucion
from base;
