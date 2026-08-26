-- Fase 1 Adquisiciones: tablas compras/recepciones (Postgres), reemplazan
-- la lectura directa de data/adquisiciones/Compras_*.xlsx / Recepciones_*.xlsx.
-- Cada tabla es una "foto" del año -- se recarga entera por año (TRUNCATE
-- WHERE ano=X + INSERT), igual que Inventario, no se acumula dia a dia.
--
-- Sin PRIMARY KEY natural (N_ORDEN_COMPRA, CODIGO) -- en la practica hay
-- lineas duplicadas para la misma OC+codigo (multiples precios/monedas),
-- confirmado en data_loader_adquisiciones.py via groupby+sum -- se usa
-- id serial en su lugar.

CREATE TABLE IF NOT EXISTS compras (
    id                  bigserial PRIMARY KEY,
    ano                 integer NOT NULL,
    n_orden_compra      bigint NOT NULL,
    codigo              bigint NOT NULL,
    fecha_creacion      date NOT NULL,
    rut                 text,
    nombre_proveedor    text,
    id_procedencia      integer,
    marca               text,
    referencia          text,
    descripcion         text,
    cantidad_comprada   numeric,
    cup                 numeric,
    desviacion          numeric,
    precio_unitario     numeric,
    precio_total        numeric,
    cantidad_pendiente  numeric,
    total_pendiente     numeric,
    tipo_compra         text,
    sucursal            text,
    cod_pago            text
);
CREATE INDEX IF NOT EXISTS idx_compras_ano            ON compras (ano);
CREATE INDEX IF NOT EXISTS idx_compras_proveedor       ON compras (nombre_proveedor);
CREATE INDEX IF NOT EXISTS idx_compras_fecha_creacion  ON compras (fecha_creacion);
CREATE INDEX IF NOT EXISTS idx_compras_codigo          ON compras (codigo);
CREATE INDEX IF NOT EXISTS idx_compras_n_orden_compra  ON compras (n_orden_compra);
CREATE INDEX IF NOT EXISTS idx_compras_tipo_compra     ON compras (tipo_compra);

CREATE TABLE IF NOT EXISTS recepciones (
    id                  bigserial PRIMARY KEY,
    ano                 integer NOT NULL,
    n_recepcion         bigint,
    n_oc                bigint NOT NULL,
    codigo              bigint NOT NULL,
    fecha_recepcion     date NOT NULL,
    rut_proveedor       text,
    nombre_proveedor    text,
    id_procedencia      integer,
    referencia          text,
    descripcion         text,
    tipo_oc             text,
    comentario_oc       text,
    u_m                 text,
    cantidad            numeric,
    precio              numeric,
    total                numeric,
    precio_clp          numeric,
    total_clp           numeric,
    cond_pago           text,
    sucursal            text,
    num_semana          integer
);
CREATE INDEX IF NOT EXISTS idx_recepciones_ano             ON recepciones (ano);
CREATE INDEX IF NOT EXISTS idx_recepciones_proveedor        ON recepciones (nombre_proveedor);
CREATE INDEX IF NOT EXISTS idx_recepciones_fecha_recepcion  ON recepciones (fecha_recepcion);
CREATE INDEX IF NOT EXISTS idx_recepciones_codigo           ON recepciones (codigo);
CREATE INDEX IF NOT EXISTS idx_recepciones_n_oc             ON recepciones (n_oc);
CREATE INDEX IF NOT EXISTS idx_recepciones_tipo_oc          ON recepciones (tipo_oc);

-- No hay tabla de "control_datos" separada -- a diferencia de Ventas,
-- Adquisiciones no tiene mecanismo de "dia sin datos confirmado"; el
-- corte de fecha es simplemente MAX(fecha_creacion)/MAX(fecha_recepcion)
-- calculado en vivo (igual que _fecha_datos() en data_loader_adquisiciones.py).
