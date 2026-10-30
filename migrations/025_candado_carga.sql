-- =====================================================================
-- 025 - Candado para que no corran dos cargas de ventas a la vez
--
-- Por que (2026-10-30): dos sincronizaciones de ventas al mismo tiempo
-- (carga diaria local + /subir_ventas) borraron las mismas fechas y luego
-- insertaron las dos: 1.465 filas duplicadas el 26, 27 y 28/10/2026
-- (+$70,6 MM; respaldo en ventas_dup_respaldo_20261030). pg_advisory_lock
-- no sirve porque la conexion pasa por el pooler de Supabase en modo
-- transaccion (el candado de sesion se pierde al hacer commit).
--
-- Como: una fila por candado con "arriendo" (tomado_en). Quien la toma la
-- marca con su token; si el proceso muere, el candado vence solo a los
-- 20 minutos. Ver tomar_candado()/soltar_candado() en data_loader_pg.py.
-- =====================================================================

begin;

create table if not exists candado_carga (
    nombre      text primary key,
    tomado_en   timestamptz,
    tomado_por  text
);

insert into candado_carga (nombre) values ('sync_ventas') on conflict do nothing;

commit;
