-- =====================================================================
-- 019 - Solicitudes de Control de Inventario: niveles de aprobacion
--
-- Pedido del usuario (2026-10-15): todas las solicitudes pasan por dos
-- aprobaciones -- primero dsepulveda, luego aprobacion final de Enrique
-- Musa o Fernando Musa. Las salidas pasan antes por el analisis del ECI
-- (C. Aliaga), que deja un dictamen. Despues de la aprobacion final el ECI
-- hace el movimiento en el ERP y la marca ejecutada.
--
--   SALIDA:      en_analisis -> pend_aprobacion_1 -> pend_aprobacion_2 -> aprobada -> ejecutada
--   otros tipos:                pend_aprobacion_1 -> pend_aprobacion_2 -> aprobada -> ejecutada
--   rechazada: desde cualquier paso de aprobacion (comentario obligatorio)
--
-- Quien aprueba en cada nivel esta en data_loader_solicitudes_pg.py
-- (APROBADOR_1, APROBADORES_FINALES, ECI). Cada cambio queda tambien en
-- solicitud_evento.
-- =====================================================================

begin;

alter table solicitud
    add column if not exists dictamen        text,
    add column if not exists aprobado1_por   text,
    add column if not exists aprobado1_en    timestamptz,
    add column if not exists aprobado2_por   text,
    add column if not exists aprobado2_en    timestamptz,
    add column if not exists ejecutado_por   text,
    add column if not exists ejecutado_en    timestamptz,
    add column if not exists doc_ejecucion   text;

-- Las solicitudes "abiertas" de la etapa 1 pasan a esperar la primera aprobacion
insert into solicitud_evento (solicitud_id, usuario, estado_desde, estado_hasta, comentario)
select id, 'sistema', 'abierta', 'pend_aprobacion_1', 'Pasa a aprobación (se activan los niveles de aprobación)'
  from solicitud where estado = 'abierta';
update solicitud set estado = 'pend_aprobacion_1' where estado = 'abierta';

commit;
