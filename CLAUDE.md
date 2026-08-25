# CasaMusa — Dashboard de Gerencia

## Stack
- Python 3.14 + Flask 3.1 + pandas 3.0
- Templates Jinja2 + Chart.js (CDN)
- **Comercial e Inventario corren sobre Postgres (Supabase)** — Comercial
  desde el 2026-08-19, Inventario desde el 2026-08-20/21 (ver "Fase 1:
  Comercial en Postgres" y "Fase 1: Inventario en Postgres" más abajo).
  Excel/pandas (`data_loader.py`, `data_loader_inventario.py`,
  `data_loader_obligatorios.py`) siguen existiendo intactos como fuente
  independiente para el diff de validación, pero el dashboard real (local
  Y Vercel) ya no los lee para esos dos dominios. Adquisiciones sigue
  100% en Excel — próximo en la lista, y a diferencia de Comercial/
  Inventario el usuario quiere REARMAR sus reportes, no solo portarlos.
- Dependencias no estándar: `python-calamine` (lectura rápida de xlsx),
  `pyarrow` (cache en parquet), `pillow` (edición de imágenes, ej. logo),
  `psycopg[binary]` + `psycopg-pool` (Postgres), `openpyxl` (escribir
  Excel, ej. `generar_plantilla_ne.py`)
- Deploy en Vercel (rama `fase-1-comercial`) + Supabase Postgres
  (región Oregon/us-west-2) — ver sección Vercel/Supabase más abajo.
- Git con remoto en GitHub (privado): `dsj874-bot/dashboard-casamusa`
  (configurado 2026-07-27, respaldo del código). Identidad configurada
  solo en este repo: David Sepúlveda / dsepulveda@casamusa.cl
- Respaldo diario de `data/` (Excel/parquet) a OneDrive
  (`C:\Users\Marcelo\OneDrive\CasaMusa_Dashboard_Backup`), corre junto
  con `actualizar_diario.py` a las 19:00 — ver `_respaldar_datos()`.
- **El Flask local corre como Servicio de Windows** (`CasaMusaDashboard`,
  instalado con NSSM — NO se abre a mano con `iniciar.bat` en el uso
  normal). Reiniciarlo: `services.msc` → "CasaMusa Dashboard" → clic
  derecho → Reiniciar (requiere permisos de administrador que Claude
  Code no tiene en esta máquina — pedirle al usuario que lo haga).
  Necesario después de CUALQUIER cambio a `.py` (no solo templates),
  porque Python no recarga código en caliente (`debug=False`).

## Estructura
```
Dashboard/
├── app.py                    # Rutas Flask
├── data_loader.py            # Lógica de datos Excel/pandas (~1900 líneas,
│                              # sin cambios funcionales — ground truth del diff)
├── data_loader_pg.py          # Equivalente Postgres de cada funcion de
│                              # data_loader.py (Fase 1, Comercial) + helpers
│                              # de gestion (asignar_vendedor_home, etc.)
├── data_loader_inventario_pg.py   # Equivalente Postgres de los 6 reportes
│                                    # core de data_loader_inventario.py
│                                    # (Resumen/Bodegas/Familia/Marca/
│                                    # Clasificacion/Procedencia)
├── data_loader_obligatorios_pg.py # Alertas/Plan de Compra/Distribucion
│                                    # sobre Productos Prioritarios (tabla
│                                    # productos_obligatorios) + Nivel de
│                                    # Servicio reusa sus helpers internos
│                                    # (_stock_combinado_pg, etc.)
├── data_loader_segunda_linea.py   # Mismo trio (Alertas/Compra/Distribucion)
│                                    # pero para "2a Linea" (AAA/M05 fuera de
│                                    # Obligatorios) -- Postgres-only, sin
│                                    # equivalente Excel
├── data_loader_exclusion_compra.py # Productos No Comprar + buscador de
│                                     # productos (usado por las 2 paginas
│                                     # de autoservicio de Inventario)
├── data_loader_nivel_servicio.py  # Fill rate (Sum(min(stock,demanda))/
│                                    # Sum(demanda)) por sucursal, pantalla
│                                    # "Nivel de Servicio"
├── db.py                     # Pool de conexiones Postgres (psycopg_pool)
├── migrations/                # SQL versionado, se aplica a mano (sin
│   ├── 001_control_datos.sql  #  herramienta de migraciones tipo alembic)
│   ├── 002_fase1_comercial.sql
│   ├── 003_vendedor_home.sql  # tabla vendedor_home + vista v_ventas
│   ├── 004_inventario.sql     # tablas inventario_stock/control (Fase 1 Inv.)
│   ├── 005_obligatorios.sql   # tabla productos_obligatorios
│   ├── 006_pedido_total.sql   # columna pedido_total en productos
│   ├── 007_exclusion_compra.sql   # tabla productos_no_comprar
│   └── 008_prioridad_updated_by.sql  # columna updated_by en productos_obligatorios
├── scripts/
│   ├── backfill_fase1_comercial.py  # Carga inicial Excel -> Postgres (uso
│   │                                  # unico, ya corrido; reusa columnas de
│   │                                  # data_loader_pg.VENTAS_COLUMNAS)
│   ├── validar_fase1_comercial.py   # Diff Excel vs Postgres, correr tras
│   │                                  # CUALQUIER cambio en data_loader_pg.py
│   ├── backfill_inventario.py       # Carga inicial Inventario.xlsx -> Postgres
│   │                                  # (uso unico, ya corrido) -- ver gotcha
│   │                                  # dict_row/venta_mensual mas abajo
│   ├── backfill_obligatorios.py     # Carga inicial Obligatorios (Excel-
│   │                                  # curado) -> productos_obligatorios
│   ├── validar_inventario.py        # Diff Excel vs Postgres para los 6
│   │                                  # reportes core de Inventario
│   └── validar_obligatorios.py      # Diff para Alertas/Compra/Distribucion
│                                      # sobre Productos Prioritarios
├── vercel.json                # Config deploy Vercel
├── iniciar.bat                # Abre Flask en Windows (instala deps) --
│                              # NO es como corre hoy en produccion, ver
│                              # nota de Servicio de Windows arriba
├── actualizar_diario.py/.bat # Consolida sin necesitar Flask corriendo
│                              # (usado por Tarea Programada Windows, 19:00)
│                              # -- ahora tambien sincroniza a Postgres
├── generar_plantilla_ne.py   # Regenera data/comercial/NE_x_Facturar.xlsx
│                              # (el Excel local; la fuente real ya es Postgres,
│                              # ver /cargar_ne mas abajo)
├── actualizar_diario.log     # Log de cada corrida automática (gitignored)
├── data/                      # Una subcarpeta por area — cada una la
│   │                          # carga una persona distinta. Solo
│   │                          # comercial/ tiene contenido real hoy.
│   ├── comercial/
│   │   ├── Ventas_2026.xlsx      # Export SAP B1 2026 (gitignored)
│   │   ├── Ventas_2025.xlsx      # Export SAP B1 2025 (gitignored)
│   │   ├── *.parquet             # Cache rápido, se regenera solo (gitignored)
│   │   ├── presupuesto.xlsx      # Presupuesto anual por sucursal (legacy,
│   │   │                          # la fuente real es la tabla presupuesto)
│   │   ├── metas.xlsx            # Metas mensuales por vendedor (legacy, la
│   │   │                          # fuente real es la tabla metas / /cargar_metas)
│   │   ├── NE_x_Facturar.xlsx    # legacy (semanal, gitignored) -- fuente
│   │   │                          # real es la tabla ne_x_facturar / /cargar_ne
│   │   └── AAMM_Vtas.xlsx        # Archivo mensual a consolidar (temporal, se
│   │                              # borra solo tras "Actualizar datos")
│   ├── inventario/            # Inventario.xlsx (nombre fijo, foto del
│   │                          # stock a la fecha — se reemplaza entero
│   │                          # cada vez, no se acumula como Ventas)
│   ├── adquisiciones/         # vacía, sin contenido aun (siguiente en la lista)
│   └── finanzas/              # vacía, sin contenido aun
├── static/img/logo.png       # Fondo transparente (no tocar con fondo solido)
└── templates/
    ├── base.html             # Layout + sidebar + nav + boton admin
    ├── login.html
    ├── resumen.html          # /resumen
    ├── proyeccion.html       # /proyeccion (incluye NE x Facturar)
    ├── metas.html            # /metas
    ├── ppto.html             # /ppto
    ├── sucursales.html       # /sucursales
    ├── vendedores.html       # /vendedores
    ├── canal.html            # /canal
    ├── familia.html          # /familia (toggle Familia/Marca)
    ├── procedencia.html      # /procedencia (Importado/Nacional)
    ├── vta_acum.html         # /vta_acum
    ├── vta_mes_mg.html       # /vta_mes_mg
    ├── vta_mg_mensual.html   # /vta_mg_mensual
    ├── subir_ventas.html     # /subir_ventas (autoservicio, admin)
    ├── cargar_ne.html        # /cargar_ne (autoservicio, admin)
    ├── cargar_metas.html     # /cargar_metas (autoservicio, admin)
    ├── gestionar_vendedores.html  # /gestionar_vendedores (autoservicio, admin)
    ├── en_construccion.html  # solo /datos la usa por ahora
    ├── _inventario_sidebar.html   # sidebar propio de Inventario (incluido por
    │                                # TODAS las paginas de Inventario via
    │                                # {% block sidebar_nav %}, no el default
    │                                # de base.html -- ver bug de sidebar abajo)
    ├── inventario_resumen.html, inventario_bodegas.html,
    │   inventario_familia.html, inventario_marca.html,
    │   inventario_clasificacion.html, inventario_procedencia.html  # 6 core
    ├── inventario_alertas.html, inventario_compras.html,
    │   inventario_distribucion.html  # "Plan de Compra Prioritarios"
    ├── inventario_alertas_segunda_linea.html,
    │   inventario_compras_segunda_linea.html,
    │   inventario_distribucion_segunda_linea.html  # "2a Linea"
    ├── inventario_nivel_servicio.html  # /inventario/nivel_servicio, Chart.js
    │                                     # combo bar($)+linea(%) por sucursal
    ├── subir_inventario.html    # /subir_inventario (autoservicio, admin)
    ├── gestionar_productos_compra.html  # /gestionar_productos_compra (admin)
    └── gestionar_prioridad.html # /gestionar_prioridad (admin)
```

## Columnas SAP B1 (Ventas_20XX.xlsx)
DOC_SAP, FOLIO, TIPO_DOC, FECHA_CONTA, FECHA_DOC, CODIGO_CLIENTE, NOMBRE_CLIENTE,
PROCEDENCIA, SUCURSAL, CODIGO_CM, ID_PROCEDENCIA, CODIGO_PROVEEDOR, DESCRIPCION,
MARCA, UNIDAD_MEDIDA, FAMILIA, SUBFAMILIA, GRUPO, CANTIDAD, COSTO_CUP, COSTO_TOTAL,
PRECIO_UNITARIO, TOTAL, UTILIDAD_BRUTA, MG_BRUTO, VENDEDOR, COND_PAGO, EMPRESA,
PROVEEDOR_POR_DEFECTO, LIQUIDAR, "TIPO VENTA", ESTATUS_SKU

Notas:
- "TIPO VENTA" (con espacio) se renombra a TIPO_VENTA al cargar.
- PROCEDENCIA solo tiene 2 valores limpios: "Importado" / "Nacional".
- CODIGO_PROVEEDOR mezcla tipos (numéricos y texto) — se castea a str en
  `_normalizar_df()`, si no pyarrow falla al escribir el cache parquet.
- Se agregan columnas: ANO, MES, DIA (desde FECHA_CONTA), SUCURSAL_LOGICA,
  VENDEDOR_RPT.

## Cache
- data_loader crea automáticamente Ventas_20XX.parquet (pyarrow instalado)
  al primer arranque. Sin pyarrow caería a .pkl.
- Siguientes arranques leen desde parquet (subsegundo).
- `_leer_archivo()` recarga si cambia el mtime del **.xlsx original O
  del archivo de cache en disco** (arreglado 2026-07-21). La
  consolidación diaria solo toca el cache, nunca el .xlsx — si solo se
  vigilara el .xlsx, un Flask de larga duración (corriendo todo el día)
  nunca notaría un cambio hecho por `actualizar_diario.py`, que corre
  como **proceso separado** (la invalidación de cache en memoria que
  hace `actualizar_desde_archivo_mensual()` solo sirve dentro del mismo
  proceso que la llama). No revertir ese chequeo doble a "solo el xlsx".
- NUNCA crear el cache desde Linux/sandbox — genera incompatibilidad con
  Windows.

## Actualización diaria de datos
Flujo real (no automático desde SAP, alguien copia el archivo a mano):
1. Se exporta de SAP "ventas del mes en curso hasta hoy" con el nombre
   `AAMM_Vtas.xlsx` (ej. `2607_Vtas.xlsx` para julio 2026) y se deja en
   `data/`.
2. Se consolida con `data_loader.actualizar_desde_archivo_mensual()` —
   dedupe por fecha exacta (solo pisa los días que trae el archivo
   nuevo), borra el xlsx al terminar (o lo renombra a `.done` si Windows
   lo tiene bloqueado).
3. Dos formas de disparar la consolidación:
   - Botón "🔄 Actualizar datos" en el topbar → POST `/admin/actualizar`
     (**solo visible/permitido para usuarios con `admin: True` en
     GERENTES** — hoy dsepulveda/emusa/jsantana, ver sección
     Autenticación). Consolida archivos Y (desde 2026-08-24) también
     llama `confirmar_dia_sin_ventas()` incondicionalmente al final,
     igual que `actualizar_diario.py` — ver siguiente sección para el
     por qué. Es el respaldo manual: si un día hábil no se cargó el
     archivo antes de las 19:00, se carga más tarde (aunque sea de
     noche) y se aprieta el botón para forzarlo.
   - Tarea Programada de Windows `CasaMusa_ActualizarVentas`, corre todos
     los días a las 19:00 vía `actualizar_diario.py` (no necesita que
     Flask esté corriendo). `LogonType: S4U` (no requiere sesión
     interactiva activa — antes era `Interactive` y fallaba en
     silencio si `StartWhenAvailable` intentaba recuperar una
     ejecución perdida antes de que hubiera sesión lista, código de
     error `0xC000013A`, sin log). `actualizar_diario.bat` usa la ruta
     absoluta de `python.exe` (Python solo está en el PATH de usuario,
     no en el del sistema — con `S4U` el PATH puede no cargar igual).
     Si el corte de fecha se queda pegado varios días, revisar primero
     `Get-ScheduledTaskInfo -TaskName CasaMusa_ActualizarVentas` y
     `actualizar_diario.log`.

## `confirmar_dia_sin_ventas()` se llama SIEMPRE, no solo si no hay archivo (bug real 2026-08-24)
Hasta el 2026-08-24, `actualizar_diario.py` solo llamaba
`confirmar_dia_sin_ventas()` dentro del `if not resultado.get("ok"):`
(rama "no se encontró archivo"). Eso asumía que si SÍ se encontró y
consolidó un archivo, el corte quedaba al día. Falso: el export de SAP
simplemente OMITE los días sin actividad (ej. domingo) en vez de traer
una fila con venta $0 — así que un archivo real, consolidado con éxito
(`ok=True`) un lunes, puede no traer NINGUNA fila del domingo anterior,
y el corte de "Datos al" quedaba congelado en el último día CON fila,
atrasando la pantalla aunque el domingo ya estuviera resuelto (síntoma
real reportado por el usuario con captura de pantalla: "Datos al
22/08/2026" cuando ya era 23/08 y el domingo 23 no tuvo venta).
**Fix**: `confirmar_dia_sin_ventas()` ahora se llama SIEMPRE al final,
tanto en `actualizar_diario.py`/`main()` como en la ruta
`/admin/actualizar` — es idempotente/monótona (usa `GREATEST()` en
Postgres, solo avanza el corte, nunca lo retrocede), así que llamarla
de más no tiene efecto cuando el archivo sí trajo el día de ayer
cubierto. No volver a poner esta llamada detrás de un `if not ok`.

## Si no hay archivo a las 19:00 (`actualizar_diario.py`)
- Decisión explícita (2026-07-24): se confirma automáticamente el día
  como "sin ventas, dato final" (`confirmar_dia_sin_ventas()`, escribe
  `data/comercial/fecha_confirmada.txt`, gitignored) SIN IMPORTAR si es hábil,
  domingo o feriado — antes solo corría domingo/feriado, pero un día
  hábil sin archivo dejaba el corte congelado, y eso también recortaba
  de menos las comparaciones de año/mes anterior (2025 y el mes
  calendario pasado, que ya están completos) cada vez que el mes en
  curso se atrasaba. Ahora avanza igual; si el día sí tuvo venta real,
  subir el archivo después corrige el número real (la fecha confirmada
  es solo el tope de corte, no fija ningún valor).
- **Ojo con la hora**: el día de HOY solo se da por cerrado desde las
  19:00 en adelante (hora en que corre la tarea programada) —
  `confirmar_dia_sin_ventas()` tiene una guardia explícita
  (`datetime.now().hour >= 19`) que confirma el día ANTERIOR si se
  ejecuta antes de esa hora (ej. a mano, de día). Si se llama fuera de
  ese resguardo (por error, de mañana) puede confirmar prematuramente
  el día de hoy como cerrado — ya pasó una vez, hay que corregir a
  mano el `.txt` si sucede.
- `_fecha_datos()` (el único corte usado en toda la app, ver más abajo)
  toma el MÁXIMO entre la fecha real de datos cargados y la fecha
  confirmada — así que esto no requiere tocar nada más.

## `_fecha_datos()` — el único corte de fechas (no usar `_hoy()` directo)
- `_fecha_datos()`: fecha MÁXIMA con datos realmente cargados de 2026
  para el mes actual. Es el ÚNICO corte usado tanto para mostrar
  "Datos al DD/MM/AAAA" en pantalla como para calcular TODAS las
  comparaciones (días hábiles transcurridos, mismo día del mes/año
  anterior, día del año para proyección anual).
- Por qué un solo corte y no la fecha real de hoy: si el año/mes
  anterior se comparara hasta "hoy" pero el año/mes actual solo tiene
  datos cargados hasta ayer, se compararían periodos de distinta
  longitud (ej. 19 días de 2025 contra 18 días reales de 2026),
  inflando artificialmente el periodo anterior. Usando siempre
  `_fecha_datos()`, año/mes actual y año/mes anterior quedan
  comparados con exactamente los mismos días.
- Avanza sola cuando se consolida un archivo nuevo (manual o vía la
  tarea de las 19:00) — no hace falta lógica adicional para que las
  comparaciones "se pongan al día", se recalculan solas en la
  siguiente carga de cada página.
- `_hoy()` (fecha real del sistema) es SOLO un helper interno de
  `_fecha_datos()` — no llamarlo directo para calcular cortes o
  comparaciones en ninguna función nueva.

## Fase 1 — Comercial en Postgres (arquitectura general)
Hecho en una sesión larga el 2026-08-19. Idea general: `data_loader_pg.py`
tiene una función equivalente a cada función de `data_loader.py` (mismo
nombre + sufijo `_pg`, misma firma, mismo shape de retorno), y `app.py`
elige cuál llamar con un flag:

```python
USAR_POSTGRES_COMERCIAL = os.environ.get("USAR_POSTGRES_COMERCIAL", "1") == "1"
```

Default ON porque en Vercel no existen los `.xlsx` locales — la version
Excel no funcionaria ahi de todas formas. Poner `USAR_POSTGRES_COMERCIAL=0`
en `.env` local para comparar contra la version Excel original si hace
falta debuggear una diferencia.

- **`db.py`**: pool de conexiones a nivel de modulo (`db.conexion_pool()`,
  lazy, `psycopg_pool.ConnectionPool`) — no una conexion nueva por query.
  `DATABASE_URL` (env var) usa el connection string con **pooler** de
  Supabase (puerto 6543, modo transaccion, `Supavisor`) — `get_connection()`
  (conexion suelta, sin pool) es solo para scripts de backfill/migracion
  que corren una vez.
- **Cada funcion `_pg` hace pocas queries** (1-6 tipicamente), consolidando
  agregados por ventana de fecha con `FILTER (WHERE ...)` en un solo
  `GROUP BY` en vez de una consulta separada por ventana — la region de
  Supabase (Oregon) tiene latencia real desde Chile, cada round-trip cuenta.
- **`migrations/*.sql`**: se aplican a mano (leer el archivo y ejecutarlo
  via `db.get_connection()`), no hay herramienta de migraciones. Antes de
  agregar una tabla/columna nueva, crear el archivo `00N_descripcion.sql`
  ahi para que quede versionado, aunque se aplique a mano.
- **`scripts/validar_fase1_comercial.py`**: compara campo-por-campo el
  resultado de cada funcion Excel vs su equivalente Postgres (varias
  combinaciones de filtros). **Correr esto despues de CUALQUIER cambio en
  data_loader_pg.py o en las tablas** — es la unica forma confiable de
  confirmar que no se rompio nada. Un remanente de ~$1-700 en
  `utilidad_bruta` (de un total de ~$100M) es ESPERADO y no es bug — es
  precision float64 (pandas) vs numeric exacto (Postgres) en sumas sobre
  cientos de miles de filas; no perseguirlo.
- **Sincronizacion de ventas (`sincronizar_ventas_pg(df, fechas,
  tamano_lote=5000, reintentos=3)` en data_loader_pg.py)**: delete-by-
  fecha_conta + insert por lotes con commit-y-reintento por lote (ver
  "Gotcha Postgres #2" mas abajo — reescrita 2026-08-24 tras un
  "no hay conexion" real al subir Ventas, causado por el mismo
  antipatron de transaccion unica que ya se habia corregido para
  Inventario). Mismo criterio de dedupe que usa el cache local. Se
  llama desde 3 lugares:
  - `actualizar_diario.py` (tarea 19:00) y el boton "Actualizar datos"
    local, via el parametro `on_nuevo` de
    `data_loader.actualizar_desde_archivo_mensual()` — data_loader.py NO
    importa Postgres directamente (el acoplamiento vive en el caller),
    para mantener su rol de "verdad Excel" para el diff.
  - `/api/subir_ventas` (pagina web, ver mas abajo) — unico camino que
    NO pasa por Excel/cache local en absoluto.
  - **Si alguna vez Postgres se ve con datos de venta desfasados**
    (venta total no coincide con Excel para un dia que deberia estar
    cargado): la causa casi siempre es una consolidacion que paso por un
    proceso Flask corriendo con codigo viejo (sin el callback), o varias
    cargas parciales de un mismo mes sin resincronizar el mes completo
    despues. Fix: `dlpg.sincronizar_ventas_pg(dl.get_df_2026()[mask], fechas)`
    para las fechas afectadas (ver historial de commits `98d75a8`,
    resync de agosto completo tras encontrar 12 fechas con
    `utilidad_bruta` desactualizada).
- **`confirmar_fecha_pg(fecha)`**: equivalente Postgres de escribir
  `fecha_confirmada.txt` — upsert en `control_datos` con `GREATEST()`
  (avanza-solamente, se autocorrige solo si se llama de mas). Se llama
  SIEMPRE desde `confirmar_dia_sin_ventas()` (no solo cuando el corte
  realmente avanza) para que Postgres se autocorrija si se quedo atras.

### Vercel / Supabase — notas de infraestructura
- **Function Region: `pdx1`** (Portland, Oregon, us-west-2) — MISMA
  region que Supabase. Antes estaba en `iad1` (Virginia), agregando un
  salto cruzando EEUU en cada query ademas del salto real Chile-Oregon.
  Cambiar en Vercel → Settings → Functions → Function Region (requiere
  un nuevo deployment para tomar efecto — un commit vacio alcanza).
- **Fluid Compute**: activado (Settings → Functions) — permite reusar
  instancias "tibias" entre requests en vez de abrir conexion nueva
  siempre.
- **Deployment Protection ("Vercel Authentication")**: DESACTIVADO
  (Settings → Deployment Protection) — estaba en "Standard Protection"
  por defecto, lo que exigia que cualquier visitante tuviera cuenta de
  Vercel Y fuera miembro del team antes de llegar siquiera al login
  propio de la app. Con el equipo comercial usando el dashboard (no solo
  Marcelo), esto hay que dejarlo apagado — si Vercel lo reactiva solo en
  un plan nuevo o similar, desactivarlo de nuevo ahi.
- Medicion real de latencia (2026-08-19, desde Chile): conexion Postgres
  fria ~2.5s, tibia ~0.6s por query. Con Fluid Compute + `pdx1` co-ubicado
  con Supabase, el mismo trabajo desde la funcion de Vercel deberia ser
  ~10-90ms (no medido directamente porque no se debe escribir la
  contraseña de la app en un navegador controlado por Claude — pedirle al
  usuario que abra el dashboard el mismo y reporte lo que siente).

### Páginas de autoservicio (sidebar "Administración", solo `admin: True`)
Reemplazan flujos que antes exigian acceso a esta maquina o pedirle a
Claude que corriera un script Python:
- **`/subir_ventas`**: arrastrar el export mensual de SAP (cualquier
  nombre de archivo, las fechas salen de la columna FECHA_CONTA, no del
  nombre) → `_normalizar_df()` + `_aplicar_sucursal_logica()` (reusa
  data_loader.py) → `sincronizar_ventas_pg()`. Funciona igual en Vercel
  que local — no toca el Excel/cache local NUNCA (decision explicita:
  Postgres es la fuente real para esta via).
- **`/cargar_ne`** y **`/cargar_metas`**: tablas web (roster desde
  `vendedor_home`, no desde los Excel) que reemplazan `NE_x_Facturar.xlsx`
  (columnas bloqueadas) y `metas.xlsx` (sin interfaz alguna antes).
  `/cargar_metas` tiene selector de mes/año + boton "Copiar mes anterior".
- **`/gestionar_vendedores`**: formulario sobre
  `asignar_vendedor_home`/`quitar_vendedor_home`/`reemplazar_vendedor`
  (ver seccion Sucursales lógicas mas abajo). El campo de nombre usa un
  `<datalist>` poblado con nombres REALES que aparecen en `ventas` (no
  texto libre sin validar) + vista previa en vivo ("X tiene $Y en Z
  filas") — esto es mas sensible que NE/Metas porque un error de tipeo
  aqui reatribuye venta real en silencio (cae en "OTROS") en vez de
  fallar visiblemente.
- Todas via `/api/gestionar_vendedores`, `/api/cargar_ne`, `/api/cargar_metas`,
  `/api/subir_ventas` — mismo patron: GET trae datos, POST guarda,
  `admin_requerido` en ambos.

## Fase 1 — Inventario en Postgres (2026-08-20/21, extendido durante la semana)
Mismo patrón que Comercial: `USAR_POSTGRES_INVENTARIO` (default "1"),
`data_loader_inventario_pg.py` espeja cada función de
`data_loader_inventario.py` con sufijo `_pg`. Cubre:

- **6 reportes core**: Resumen, Por Bodega, Por Familia, Por Marca, Por
  Clasificación, Por Procedencia. Tabla `inventario_stock` (una fila por
  producto+bodega, reemplazada entera en cada carga — foto, no
  histórico) + `inventario_control` (equivalente a `control_datos`,
  guarda la fecha de la última foto cargada).
- **Productos Prioritarios ("Plan de Compra Prioritarios")**: Alertas de
  Quiebre / Plan de Compra / Distribución desde CD, sobre la tabla
  `productos_obligatorios` (curada originalmente en Excel, ver más
  abajo) — `data_loader_obligatorios_pg.py`.
- **"2ª Línea"** (`data_loader_segunda_linea.py`, **100% Postgres-only,
  sin equivalente Excel**): mismo trio de pantallas pero para productos
  AAA/M05 que NO están en `productos_obligatorios`. Reglas de negocio
  explícitas del usuario:
  - Código que empieza en **6** (`PREFIJOS_FUERA_SEGUNDA_LINEA` en
    `data_loader_exclusion_compra.py`): excluido de TODO el universo
    (ni Alertas, ni Distribución, ni Plan de Compra) — no son productos
    reales de reventa.
  - Código que empieza en **3 o 7** (`PREFIJOS_IMPORTADO_SIN_INJERENCIA`):
    aparecen en Alertas/Distribución, pero excluidos SOLO de las
    sugerencias de Plan de Compra ("son productos importados que no
    tengo ingerencia de compra" — decisión explícita del usuario).
- **Nivel de Servicio** (`/inventario/nivel_servicio`, sección propia en
  el sidebar, sin filtros): fill rate = `Σ min(stock_combinado,
  demanda_diaria) / Σ demanda_diaria`, sobre Productos Prioritarios.
  Reusa los helpers internos de `data_loader_obligatorios_pg.py`
  (`_stock_combinado_pg`/`_venta_combinada_pg`/`_cargar_stock_pg`) con
  `cod_equiv=None` (no hay par equivalente en este contexto). San Isidro
  se trata como sucursal independiente (solo su propia venta, sin ajuste
  de CD — decisión explícita del usuario). Un quiebre total en un
  producto puntual aporta 0% a ese producto (no se excluye ni se trata
  especial) — así es como debe funcionar la fórmula. Página de 2 KPIs
  (Inventario Total $, Nivel de Servicio % general) + gráfico combo
  Chart.js (barras=$ eje izq., línea=% eje der. 0-100) por sucursal.
  **Nunca verificado visualmente en navegador** (preview_start falló:
  puerto 5000 reservado por el OS) — solo verificado vía Flask
  `test_client()` + llamada directa a la función con números plausibles.

### Páginas de autoservicio de Inventario (sidebar "Administración", solo `admin: True`)
- **`/subir_inventario`**: sube el Inventario.xlsx (foto completa,
  reemplaza `inventario_stock` entero) — reusa
  `data_loader_inventario.fusionar_datos_duros(df)` (extraída como
  helper reusable, antes vivía inline en `_leer_inventario()`).
- **`/gestionar_productos_compra`** ("Productos No Comprar"): excluye un
  producto SOLO del Plan de Compra (Prioritarios y 2ª Línea) — NO de
  Alertas ni Distribución, a propósito ("no vamos a comprar los conduit
  fuertes" pero igual hay que saber si hacen falta/redistribuir). Tabla
  `productos_no_comprar`. Buscador con filtro por Familia + búsqueda de
  texto por palabra (no substring literal — "conduit fuerte" no
  matcheaba "CONDUIT PVC ... FUERTE" con substring porque las palabras
  no son adyacentes en la descripción real).
- **`/gestionar_prioridad`**: promueve un producto de 2ª Línea a
  Prioritario (`promover_a_prioridad()`, autocompleta familia/
  subfamilia/grupo/descripción desde la tabla `productos`) o lo devuelve
  a 2ª Línea (`quitar_de_prioridad()`). Motivado por: "necesito que los
  productos puedan pasar desde segunda linea a prioridad".
- Buscador compartido de ambas páginas: `buscar_productos()` en
  `data_loader_exclusion_compra.py`, excluye por defecto
  `PREFIJOS_EXCLUIDOS_BUSCADOR` (dígito 6, y opcionalmente 3/7 según el
  parámetro `prefijos_excluidos` de cada caller) — el dropdown de
  Familia NUNCA debe mostrar productos con código 6 (bug real reportado
  por el usuario: "la lista despegable debiese mostrarme todos los
  productos a Excepcion de los SM0").

### `productos_obligatorios`: acentos/mayúsculas inconsistentes (curación Excel vs SAP)
La tabla se curó originalmente desde Excel (mayúsculas/acentos libres);
`productos` viene de SAP con su propio estándar. Se encontraron y
corrigieron SOLO las discrepancias que eran el mismo valor con
acento/mayúscula distinta (123 filas de `familia`, 80 de `grupo`,
verificado con un script Python `unicodedata`-normalizado antes de
tocar nada) — las 66 diferencias reales de `subfamilia` y 421 de
`grupo` (curación intencional distinta, no error de tipeo) se dejaron
intactas a propósito. Si se promueve un producto nuevo desde
`/gestionar_prioridad` y aparece una "familia duplicada" con/sin
tilde, revisar primero si es un caso real de curación divergente antes
de normalizar en masa.

## Gotcha Postgres #1 — `db.get_connection()`/`db.conexion_pool()` usan `row_factory=dict_row`
Toda conexión de este proyecto devuelve **dicts**, no tuplas. Esto es
invisible la mayoría del tiempo (`row["col"]`), pero es una trampa real
al desempacar con unpacking posicional:
```python
for codigo, bodega, venta in cur.fetchall():   # BUG: recorre las
    ...                                          # LLAVES del dict (strings),
                                                   # no los valores
```
Esto rompió en silencio (sin excepción, sin error visible) la
preservación de `venta_mensual` durante todo `scripts/backfill_inventario.py`
en esta sesión — el rescate-antes-de-truncar quedaba vacío porque el
dict nunca se indexaba de verdad. Fix: siempre `row["columna"]`, nunca
unpacking posicional sobre un cursor de este proyecto.

## Gotcha Postgres #2 — bulk INSERT/UPSERT: commit por lote, nunca una transacción gigante
`DATABASE_URL` usa el **pooler transaccional de Supabase** (Supavisor,
puerto 6543) — puede cortar la conexión a mitad de una transacción larga.
Un DELETE+INSERT de miles de filas en una sola transacción (un solo
`conn.commit()` al final) revienta como "no hay conexión" en el
navegador si el pooler corta a mitad de camino, y deja la tabla en un
estado parcial. Encontrado y corregido TRES veces distintas esta
sesión (`cargar_stock` de Inventario, el rescate de `venta_mensual`
antes de truncar, y `sincronizar_ventas_pg` para el upload de Ventas)
— el patrón correcto, ya probado en `backfill_ventas()`, es:
1. DELETE en su propia transacción, commit aparte.
2. INSERT en lotes (ej. 5000 filas) — **cada lote hace su propio
   `conn.commit()`**, con reintento-y-reconexión (2-3 intentos) si el
   commit del lote falla.

No volver a escribir un bulk write de Postgres en este proyecto como
"un solo `with conn: ... commit()` al final" — siempre por lotes.

## Sucursales lógicas (SUCURSAL_LOGICA) y vendedores
SAP maneja sucursales físicas. La función `_aplicar_sucursal_logica()`:
- Mapea sucursales físicas → lógicas (`_MAPA_SUC_BASE`).
- SI-STK (bodega compartida) se reatribuye a la sucursal HOME real de
  cada vendedor (`_VENDEDOR_HOME_SUC`, derivado de `VEND_HOME`) — no a
  una lista fija de nombres. Si el vendedor no tiene home conocida, se
  queda en SE (default). Esto importa para gente que cambió de
  sucursal (ej. Gisella Norambuena: SE → MT históricamente).
- `VENDEDOR_RPT` = nombre real si es "home" de esa sucursal lógica,
  "OTROS" si no. **Se usa para reportes agrupados por sucursal**
  (Proyección, Por Sucursal).
- **"Por Vendedor" (`get_ventas_por_vendedor`) agrupa por `VENDEDOR`
  crudo, NO por `VENDEDOR_RPT`** — a propósito, para que el 100% de la
  venta de una persona se vea a su nombre sin importar bajo qué
  sucursal/bodega quedó registrada en SAP.
- Orden canónico: MT, LC, MR, SE, CMD, CH, MP, CANAL DIGITAL, OF
  (`ORDEN_SUCURSALES`). **Ya NO se usa para ordenar las pantallas de
  reporte** (Proyección, Por Sucursal, Seguimiento Metas, Seguimiento
  Ppto en el lado `_pg`) — esas ahora ordenan dinámicamente por venta
  real descendente (mayor venta primero, calculado en cada llamada, no
  un índice fijo). `ORDEN_SUCURSALES` sigue vigente tal cual SOLO para
  las pantallas de roster/edición (Gestionar Vendedores, NE x Facturar,
  Cargar Metas), donde un orden estable importa más que el ranking.
- Hay una persona sin mapear en `VEND_HOME`: **EMA SEPULVEDA TUREN**
  (~$33M en SI-STK/2025, cae en OTROS/SE) — ya no trabaja en la empresa,
  se dejó así a propósito, no "corregir".

### En Postgres: cambiar de sucursal / agregar / sacar vendedores (Fase 1)
`_aplicar_sucursal_logica()` de arriba es la version Excel/pandas — sigue
existiendo tal cual, sin cambios, como "verdad" independiente para el
diff de `scripts/validar_fase1_comercial.py`. **Pero el dashboard real
(local y Vercel) corre sobre Postgres, y ahi el mecanismo es distinto
a proposito**, para que reasignar a alguien sea una fila, no una
resincronizacion de historia:

- `vendedor_home` (tabla, migrations/003_vendedor_home.sql): equivalente
  a `VEND_HOME`/`VEND_HOME_DESDE`, pero en Postgres — `(vendedor
  PRIMARY KEY, sucursal, vigente_desde)`.
- `v_ventas` (vista sobre `ventas`): calcula `sucursal_logica`/
  `vendedor_rpt` AL CONSULTAR, uniendo contra `vendedor_home` — replica
  exactamente la logica de `_aplicar_sucursal_logica()` (SI-STK,
  traspasos). Las columnas fisicas `ventas.sucursal_logica`/
  `vendedor_rpt` siguen existiendo (las escribe `sincronizar_ventas_pg`
  al insertar) pero **ya no las lee ningun reporte** — son vestigiales,
  no hace falta mantenerlas sincronizadas. Todas las funciones de
  `data_loader_pg.py` leen de `v_ventas`, no de `ventas`, EXCEPTO
  `_fecha_datos_pg()` (no usa esas columnas) y `sincronizar_ventas_pg()`
  (hace el DELETE/INSERT real, tiene que ser sobre la tabla).
- Para reasignar/agregar/sacar un vendedor, usar (todo en
  `data_loader_pg.py`, no hace falta tocar codigo ni resincronizar
  ventas):
  - `asignar_vendedor_home(vendedor, sucursal, vigente_desde=None)` —
    asigna o cambia su sucursal home. `vigente_desde` solo si es un
    traspaso (ej. caso Gisella) y no debe reatribuirse retroactivamente.
  - `quitar_vendedor_home(vendedor)` — se va de la empresa: su historia
    completa cae en "OTROS" desde ese momento (mismo efecto que Ema/
    Igor Moya, pero sin tocar una sola fila de `ventas`).
  - `reemplazar_vendedor(nombre_viejo, nombre_nuevo, sucursal,
    vigente_desde=None)` — combina las dos anteriores + renombra las
    filas de `metas`/`ne_x_facturar` de nombre_viejo a nombre_nuevo
    (mismo puesto, mismas metas/NE pendientes). Este es el caso real de
    Igor Moya → Marcelo Gatica (hecho a mano antes de que existiera
    esta funcion — ver commit `c7632eb` para la migracion original a
    mano, y `0a81f5d` donde se construyo `v_ventas`/`vendedor_home`
    para que el proximo caso sea automatico).
- `VEND_HOME` (Python) y `vendedor_home` (Postgres) NO estan
  sincronizados automaticamente entre si — son independientes a
  proposito (Postgres no debe depender de Excel ni viceversa). Si se
  reasigna alguien solo en Postgres (el caso normal, ya que ahi vive el
  dashboard real), `scripts/validar_fase1_comercial.py` va a marcar
  diferencias la proxima vez que se corra — es la señal de que
  `VEND_HOME` en `data_loader.py` tambien deberia actualizarse (o de
  que la diferencia es esperada y el script debe ignorarse para ese
  caso puntual).

## Reporte genérico por campo
`get_ventas_por_campo(campo, orden_map=None, top_n=None)` es la función
base detrás de Sucursal/Vendedor/Canal/Familia/Marca/Procedencia/
Cliente/Producto — agrupa venta mes/año actual vs anterior por
cualquier columna categórica. Devuelve `items` (posiblemente truncado
a `top_n`, ej. Marca/Cliente/Producto top 15-30) Y `total` (SIEMPRE
calculado sobre el set completo, antes de truncar) — el frontend debe
usar `d.total` para la fila TOTAL GENERAL, nunca sumar `items` a mano
(si hay truncado, la suma de `items` no es el total real, puede
incluso ser mayor si hay categorías negativas fuera del top N).

**Implementación (importante, no volver a un loop por valor):** usa
`groupby()` vectorizado de pandas (una pasada por métrica), no un
loop en Python filtrando el dataframe completo por cada valor
distinto. Con columnas de baja cardinalidad (sucursal, canal) un loop
era tolerable, pero con CLIENTE (~6000 valores) o DESCRIPCION (~4200)
un loop se cuelga (>30s). El universo de `valores` a listar debe
salir de `df26v[campo].unique() | df25v[campo].unique()` (dataset
completo, sin filtrar por ventana YTD/mes) — si se arma esa unión
solo a partir de los índices de las series agregadas (YTD-filtradas),
alguien que vendió solo fuera de esa ventana desaparece de la lista
en vez de mostrarse en $0 (bug real que apareció al optimizar esto).

## Nro Docs (Proyección) — contar documentos, no filas ni strings
`get_proyeccion()`/`get_proyeccion_pg()` calculan `nro_docs` por
sucursal+vendedor. Un documento (boleta/factura) trae **una fila por
producto vendido**, asi que:
- **NUNCA usar `count(*)`/`("TOTAL","count")`** — cuenta filas/lineas,
  no documentos (bug real encontrado 2026-08-19: 458 "documentos"
  mostrados vs 176 reales para un vendedor en un mes real, ~2.6x
  inflado). El numero correcto es DOC_SAP+FOLIO **distintos**.
- **En pandas, NO concatenar DOC_SAP+FOLIO como string para dedupe**
  (`df["DOC_SAP"].astype(str) + "||" + df["FOLIO"].astype(str)`) — FOLIO
  es float64 (columna con folios faltantes fuerza el tipo) y ese camino
  junta documentos DISTINTOS por error de conversion a texto (bug real:
  2410 "documentos" via string vs 2529 reales comparando las columnas
  crudas para un mes completo). Usar
  `df.groupby([...]).apply(lambda g: g[["DOC_SAP","FOLIO"]].drop_duplicates().shape[0])`
  en su lugar — el numero de grupos (sucursal+vendedor, ~50) es chico,
  `.apply()` ahi es rapido (no es el caso de CLIENTE/DESCRIPCION con
  miles de valores distintos, ver "Reporte generico por campo" abajo).
- En Postgres, `count(DISTINCT (doc_sap, folio))` es correcto y no tiene
  el problema de precision de pandas (ambas columnas son `text` en la
  tabla `ventas`, no float) — no usar `count(*)` ahi tampoco.
- Un documento real puede repartir sus lineas entre 2 vendedores/
  sucursales distintos en casos raros (3 de ~2500 en agosto 2026) — la
  suma de `nro_docs` por fila queda un poco por encima (ej. 2532) del
  total global de documentos distintos (2529); es correcto, no un bug
  (cada grupo cuenta el documento una vez, si aparece en 2 grupos se
  cuenta 2 veces en la suma).

## NE x Facturar (Negocios Ganados por facturar)
- **Fuente real desde 2026-08-19: tabla `ne_x_facturar` en Postgres,
  editable en `/cargar_ne`** (self-service, sin Excel). El Excel local
  (`data/comercial/NE_x_Facturar.xlsx`, 1 fila por vendedor home + "OTROS"
  por sucursal) sigue existiendo pero es legacy — ya no se lee para el
  dashboard real, solo lo usa `data_loader.get_proyeccion()` (lado Excel,
  ground truth del diff).
- `generar_plantilla_ne.py`: sigue regenerando el Excel legacy si cambia
  `VEND_HOME` — util para mantener consistencia si alguna vez se necesita
  comparar el lado Excel, pero ya no es el flujo operativo real.
- En Proyección: `Proy. Lineal + NE = Proy. Lineal + Monto NE`,
  `Mg con NE = Proy. Mg + Monto NE × 20%` (`MG_NE_PCT` en data_loader.py).
- Lado Postgres: si un vendedor no tiene fila en `ne_x_facturar`, el
  monto es 0 (mismo no-op que el lado Excel si el archivo no existe).

## Colores (base.html `:root`)
`--red` es un rojo real (`#ef4444`). El verde de marca (`#22a347`, usado
en nav activo, botones, hover) es `--accent`, NO `--red` — hubo un bug
donde `--red` estaba puesto en verde por error y todas las variaciones
negativas se veían verdes. No revivir esa confusión.

## Selector de Tema (Oscuro / Claro / Sistema)
`base.html` define TODOS los colores como variables CSS en `:root`
(dark, default) — cualquier plantilla nueva debe usar `var(--token)`,
nunca un hex suelto, o no respetará el tema.
- **3 bloques de tokens en `base.html`**: `:root` (oscuro, default),
  `@media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) {...} }`
  ("Sistema" — sigue al SO si el usuario no forzó nada) y
  `:root[data-theme="light"] {...}` (forzado a claro a mano, gana aunque
  el SO esté oscuro). Los 3 bloques deben mantenerse con el mismo set
  de variables — si se agrega un token nuevo, agregarlo en los 3.
- **Selector** (topbar, arriba a la derecha, `#theme-switch`): 3
  botones (🌙/☀️/🖥️) que escriben `localStorage.tema` (`"dark"`
  (default) | `"light"` | `"system"`) y ponen/quitan el atributo
  `data-theme` en `<html>`. Un script inline en `<head>` (antes de
  `</head>`, corre ANTES de pintar) aplica el tema guardado para evitar
  parpadeo — no mover esa lógica a `extra_scripts` (correría demasiado
  tarde, después del primer paint).
- **Logo**: `static/img/logo.png` tiene texto BLANCO (diseñado para
  sidebar oscuro) — sobre sidebar claro se pierde. Existe
  `static/img/logo-light.png` (generado con Pillow, mismo trazo pero
  con el relleno blanco pasado a `#0f172a`) — el JS del selector de
  tema cambia el `src` del `<img>` según el tema efectivo (incluye
  escuchar el evento `change` de `matchMedia` para cuando está en
  "Sistema" y el SO cambia sin recargar la página).
- **Gotcha real (ya paso una vez): Chart.js/Canvas NO entiende
  `var(--token)`** — a diferencia de un `style="color:var(--x)"` en
  HTML (que el navegador sí parsea como CSS real), `ctx.fillStyle` y
  cualquier color que Chart.js reciba en su config se pasan directo al
  Canvas 2D, que no resuelve custom properties. Todas las pantallas con
  `new Chart(...)` deben resolver el valor ANTES de pasarlo:
  ```js
  const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  Chart.defaults.color = cssVar("--muted2");   // NO: "var(--muted2)"
  ```
  Si se agrega un gráfico nuevo, copiar este patrón — nunca poner
  `"var(--x)"` como string literal dentro de un dataset/scale de
  Chart.js. (Los colores de gráficos quedan fijados al momento de
  crear el chart — cambiar de tema sin recargar la página no
  recolorea un gráfico ya dibujado, es una limitación conocida.)
- **`login.html` e `inicio.html` quedaron fuera** (son standalone, no
  extienden `base.html`, no tienen el selector ni los tokens) — decisión
  explícita para acotar el alcance, no un olvido.

## Autenticación
Usuarios hardcodeados en `app.py` (dict `GERENTES`). Sin DB. Emails
como *keys* del dict deben ir en minúscula (`/login` hace
`.strip().lower()` antes de buscar, pero el usuario puede escribir el
correo con cualquier capitalización al iniciar sesión). Session Flask
con secret_key.

Cada entrada de `GERENTES` puede combinar estas llaves (no son
mutuamente excluyentes, ver tabla de restricciones más abajo):
- **`admin: True`**: ve el menú "Administración" (Subir Ventas/
  Inventario, Cargar NE/Metas, Gestionar Vendedores/Productos/
  Prioridad) y el botón "Actualizar datos". Por defecto los usuarios
  nuevos NO son admin.
- **`sucursal`**: string o lista — fuerza todos los reportes de
  Comercial a esa sucursal únicamente (Jefe de Sucursal clásico). Lista
  cuando una persona cubre más de una sucursal (ej. Express = CH+MP).
- **`canal`**: lista de valores de `TIPO_VENTA` — fuerza los reportes a
  ese/esos canales únicamente, en vez de por sucursal (perfil
  e-commerce, ve todas las sucursales pero solo su tipo de venta).
- **`sucursal_ne`**: string — controla SOLO qué filas de NE x Facturar
  puede ver/editar en `/cargar_ne`, independiente de `sucursal`/`canal`
  (existe porque Elennys no tiene una `sucursal` real de SAP, pero sí
  necesita su propia fila de NE bajo "CANAL DIGITAL").

Usuarios actuales (actualizado 2026-08-24):
| Correo | Nombre | Admin | Gerencia |
|---|---|---|---|
| dsepulveda@casamusa.cl | Administrador | ✅ Sí | ✅ Sí |
| emusa@casamusa.cl | G. General | ✅ Sí | ✅ Sí |
| jsantana@casamusa.cl | Comercial | ✅ Sí | ✅ Sí |
| fmusa@casamusa.cl | Importaciones | No | ✅ Sí |
| malvarado@casamusa.cl | Finanzas | No | ✅ Sí |

Jefes de sucursal / canal (NO gerencia — ven solo Comercial, filtrado a
lo suyo, sin "Administración"):
| Correo | Nombre | Restricción | Mecanismo |
|---|---|---|---|
| gcarrasco@casamusa.cl | MT | sucursal MT | `sucursal` + `sucursal_ne` implícito (=sucursal) |
| sarjona@casamusa.cl | LC | sucursal LC | idem |
| evalera@casamusa.cl | MR | sucursal MR | idem |
| jvillegas@casamusa.cl | Express | sucursal CH+MP | idem (lista) |
| eperez@casamusa.cl | E-commerce | canal e-commerce (todas las sucursales) | `canal: CANALES_ECOMMERCE` + `sucursal_ne: "CANAL DIGITAL"` |

`CANALES_ECOMMERCE = ["CASAMUSA.CL", "MERCADO LIBRE CM", "MERCADO LIBRE SC", "MKT PLACE", "VENTA ASISTIDA", "SODIMAC/LEGRAND"]`
(Elennys es la encargada de E-commerce en general — se fue agregando
canal por canal en la conversación real con el usuario, ojo si aparece
un canal e-commerce nuevo en SAP, hay que agregarlo a esta lista a mano).

Las claves siguen el patrón `Rol2026` (ej. `Admin2026`, `Ecommerce2026`,
`Comercial2026`, `GGeneral2026`). **naguilera@casamusa.cl fue eliminado
por completo** (se fue de la empresa) — `login_requerido`/
`admin_requerido` revalidan en CADA request que `session["usuario"]`
siga en `GERENTES`, así que a alguien que se elimina se le corta el
acceso de inmediato (sesión activa incluida), no solo se le bloquea un
login nuevo.

### Restricciones de área (before_request hooks en app.py)
Tres mecanismos independientes, cada uno con su propio criterio — no
confundir uno con otro:
- **`USUARIOS_INVENTARIO_ADQUISICIONES = {"dsepulveda@casamusa.cl"}`** +
  `_restringir_inventario_adquisiciones()`: solo esta cuenta ve
  Inventario/Adquisiciones — para todos los demás, esas rutas ni
  aparecen en `/inicio` ni son accesibles directo por URL.
- **`_restringir_metas_ppto_canal()`**: bloquea `/metas`/`/ppto` (y sus
  `/api/`) para cualquier sesión con `canal` seteado (hoy solo
  Elennys) — decisión explícita del usuario tras confirmar que
  comparar venta parcial (solo su canal) contra la meta del equipo
  completo mostraba un "100% atrasado" engañoso para todo el resto del
  equipo, y $0 sin meta cargada para ella. No es un bug de cálculo
  (`pct_cumpl = (1 - vta/meta_acum)*100` es correcto), es una base de
  comparación que no tiene sentido para un perfil de canal — más
  simple ocultarlas que rehacer la base de comparación.
- **`USUARIOS_GERENCIA = {"dsepulveda@casamusa.cl", "emusa@casamusa.cl", "fmusa@casamusa.cl", "malvarado@casamusa.cl", "jsantana@casamusa.cl"}`**
  (por EMAIL, **independiente del flag `admin`** — fmusa y malvarado son
  gerencia pero NO admin) + `_restringir_areas_no_comercial()`: bloquea
  `/finanzas`, `/logistica`, `/bodega`, `/forecast`, `/tareas` para
  cualquiera fuera de este set, y `/inicio` solo les muestra el mosaico
  de Comercial a los no-gerencia. **Ojo**: el primer intento de esta
  restricción usó `session.get("admin")` como criterio y excluía
  incorrectamente a fmusa/malvarado (no son admin, sí son gerencia) —
  el usuario lo corrigió explícitamente ("los gerentes son EMUSA,
  FMUSA, DSEPULVEDA, MALVARADO y JSANTANA. Todos los demas son jefes de
  sucursales"). `admin` sigue siendo un flag totalmente distinto (visibilidad
  del menú "Administración"), no tocar ese significado.

### NE x Facturar: acceso por Jefe de Sucursal (extendido más allá de admin)
`/cargar_ne` y `/api/cargar_ne` usan el decorador
`admin_o_jefe_sucursal_requerido` (admin O `sucursal` O `sucursal_ne`) —
antes era solo `admin_requerido`. Cada Jefe de Sucursal ve y edita
ÚNICAMENTE la fila de su propia sucursal (validado también en el
servidor, no solo escondiendo filas en el HTML). `_sucursal_ne_forzada()`
es el helper que resuelve la sucursal efectiva a partir de la sesión —
usa `sucursal_ne` si existe (caso Elennys, que no tiene `sucursal` real),
si no cae a `sucursal`.

## Páginas activas
Las 14 de esta tabla corren sobre Postgres desde Fase 1 (2026-08-19) —
la columna "Función datos" lista la version Excel; la real (Vercel y
local) es la misma función + sufijo `_pg` en `data_loader_pg.py`. Las 4
páginas de autoservicio (`/subir_ventas`, `/cargar_ne`, `/cargar_metas`,
`/gestionar_vendedores`) no están en esta tabla porque no muestran un
reporte — ver sección "Páginas de autoservicio" más arriba. Las
páginas de Inventario (6 core + Plan de Compra Prioritarios + 2ª Línea
+ Nivel de Servicio + sus 3 páginas de autoservicio) NO están en esta
tabla — ver la sección "Fase 1 — Inventario en Postgres" más arriba,
que las documenta a todas.

| Ruta | Template | Función datos |
|------|----------|---------------|
| /resumen | resumen.html | get_resumen() |
| /proyeccion | proyeccion.html | get_proyeccion(filtros) |
| /metas | metas.html | get_seguimiento_metas(filtros) |
| /ppto | ppto.html | get_seguimiento_ppto() |
| /sucursales | sucursales.html | get_ventas_por_sucursal() |
| /vendedores | vendedores.html | get_ventas_por_vendedor() |
| /canal | canal.html | get_ventas_por_canal() |
| /familia | familia.html | get_ventas_por_familia(agrupar_por) |
| /procedencia | procedencia.html | get_ventas_por_procedencia() |
| /vta_acum | vta_acum.html | get_vta_acum(filtros) |
| /vta_mes_mg | vta_mes_mg.html | get_vta_mes_mg_acum(filtros) |
| /vta_mg_mensual | vta_mg_mensual.html | get_vta_mg_mensual(filtros) |
| /clientes | clientes.html | get_ventas_por_cliente() (top 15) |
| /productos | productos.html | get_ventas_por_producto() (top 15) |

## Páginas en construcción (pendiente)
- /datos — Vista de Datos (explorador de transacciones crudas, con
  filtros + búsqueda; diseño aún no definido con el usuario). La ruta
  y `en_construccion.html` siguen existiendo, pero el link del menú
  se sacó de `base.html` (2026-07-20) porque no tenía nada construido
  y ocupaba espacio. Si se retoma, agregar de nuevo el `<a>` en el
  sidebar de base.html.

## Filtros de Proyección / Seguimiento Metas
Ambas paginas comparten `/api/filtros_proyeccion`
(`get_filtros_proyeccion()`) y `_aplicar_filtros_comunes()` en
data_loader.py. Si se agrega un filtro nuevo a una, hay que
agregarlo a ambos lugares (la lista de opciones Y el mapeo
clave→columna en `_aplicar_filtros_comunes`) o quedará en el
dropdown pero sin efecto real. **Lado Postgres:** equivalente es
`get_filtros_proyeccion_pg()` y `_filtros_comunes_sql()` en
data_loader_pg.py — un filtro nuevo tambien hay que agregarlo ahi
(y en `_filtros_vta_sql()`/`FILTROS_VTA_COL` si aplica a Vta Acumulada).

## Barra lateral colapsable
`base.html` tiene un botón (`#sidebar-toggle`, borde de la barra,
`calc(var(--sidebar-w) - 12px)`) que colapsa el menú a solo íconos
(64px, clase `body.sidebar-collapsed`, ver CSS ahí mismo). Estado en
`localStorage.sidebarCollapsed`, restaurado con un script al inicio
de `<body>` para evitar parpadeo. **No agregar `transition` a la
propiedad `left` de ese botón** — interfiere con el recálculo de
`calc(var(--sidebar-w) - ...)` cuando cambia la variable (bug ya
encontrado y corregido una vez).

## Contenedores flex/grid con tablas anchas (`min-width: 0`)
Un item de flex o grid NO se achica bajo el ancho de su contenido por
defecto, aunque tenga un hijo con `overflow-x: auto` — hace falta
`min-width: 0` explícito en el item. `.main` y `.content` en
base.html ya lo tienen (protege a cualquier página). Si una página
nueva usa su propio grid de 2 columnas (filtros + tabla, como
proyeccion.html/metas.html vía `.proy-layout`), el item que envuelve
la tabla también necesita su propio `min-width: 0` (ver clase
`.proy-contenido`) — si no, una tabla ancha empuja TODA la página a
scrollear horizontal en vez de quedar contenida en su propio wrapper.

## Tabs BIWIZER pendientes de replicar
Los reportes siguen la estructura del sistema BIWIZER interno:
- Evolutivo por Tipo de Venta
- Evolutivo por Categoría
- Cobertura/Alcance

## Reglas importantes al editar
1. TODAS las rutas Flask deben ir ANTES de `if __name__ == "__main__":` en app.py
2. Escribir archivos .py con la herramienta Write/Edit normal (UTF-8) —
   el problema histórico de truncado con acentos era del entorno, no de
   la herramienta; igual conviene verificar con
   `python -c "import data_loader"` tras editar.
3. Números en CLP: siempre enteros completos, formato es-CL
   JS: `'$' + Math.round(v).toLocaleString('es-CL')`
4. No crear el cache (parquet/pkl) desde sandbox Linux — solo Flask en
   Windows debe crearlo.
5. Tras cualquier cambio en `.py` (data_loader.py, data_loader_pg.py,
   app.py) o templates, reiniciar el Servicio de Windows
   `CasaMusaDashboard` (`services.msc`, no matar-proceso-y-relanzar —
   ver Stack arriba) — no hay reloader activo (`debug=False`), y Claude
   Code no tiene permisos de administrador en esta maquina para
   reiniciarlo solo, hay que pedirselo al usuario cada vez.
6. Tras cualquier cambio en `data_loader_pg.py` o en las tablas de
   Postgres (Comercial), correr `python scripts/validar_fase1_comercial.py`
   antes de dar el cambio por terminado. Para Inventario/Obligatorios,
   `python scripts/validar_inventario.py` y
   `python scripts/validar_obligatorios.py` (2ª Línea y Nivel de
   Servicio no tienen equivalente Excel, no hay script de validación
   posible para esas dos).
6b. Cualquier bulk INSERT/UPSERT nuevo a Postgres debe ir por lotes con
   commit-y-reintento por lote, nunca una sola transacción para todo —
   ver "Gotcha Postgres #2" más abajo, ya causó 3 bugs reales de "no
   hay conexión" en esta sesión.
7. `app.py` fuerza stdout/stderr a UTF-8 al importar — necesario porque
   la consola de Windows por defecto no soporta los caracteres de caja
   del banner ni tildes en prints.
8. El puerto de Flask es configurable via env var `PORT` (default 5000,
   sin cambiar el comportamiento normal) — permite levantar una
   instancia de prueba en otro puerto (`PORT=5051 python app.py`) sin
   afectar el servicio real mientras se prueba algo nuevo.

## Formato numérico (JS)
```js
const fmtM = v => (v == null || v === 0) ? '—' : '$' + Math.round(v).toLocaleString('es-CL');
const fmtPct = (v, pos=true) => {
  if(v == null) return '<span class="vneu">—</span>';
  const cls = v >= 0 ? (pos ? 'vpos' : 'vneg') : (pos ? 'vneg' : 'vpos');
  return `<span class="${cls}">${v >= 0 ? '▲' : '▼'} ${Math.abs(v).toFixed(1)}%</span>`;
};
```

## Patrón API (JS en templates)
```js
const r = await fetch('/api/endpoint', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(filtros)
});
const d = await r.json();
if(d.error) { /* manejar error */ return; }
```
