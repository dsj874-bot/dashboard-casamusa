-- =====================================================================
-- 020 - Analista IA: registro de preguntas y respuestas
--
-- Pedido del usuario (2026-10-16): un agente de IA dentro de Musa360 que
-- responde preguntas de ventas y compras (agente_ia.py). Cada pregunta queda
-- registrada: sirve para revisar que las respuestas sean correctas, ver que
-- herramientas uso y controlar el uso del plan gratuito de Gemini.
-- =====================================================================

begin;

create table if not exists agente_log (
    id              bigserial primary key,
    creado_en       timestamptz not null default now(),
    usuario         text not null,
    pregunta        text not null,
    respuesta       text,
    pasos           jsonb,            -- herramientas usadas con sus parametros
    modelo          text,
    tokens_entrada  integer,
    tokens_salida   integer,
    ms              integer,
    error           text
);
create index if not exists idx_agente_log_usuario on agente_log (usuario, creado_en desc);

commit;
