# CasaMusa — Dashboard de Gerencia

## Stack
- Python 3.14 + Flask 3.1 + pandas 3.0
- Templates Jinja2 + Chart.js (CDN)
- Sin base de datos — lee Excel exports de SAP B1
- Dependencias no estándar: `python-calamine` (lectura rápida de xlsx),
  `pyarrow` (cache en parquet), `pillow` (edición de imágenes, ej. logo)
- Git inicializado (repo local, sin remoto). Identidad configurada solo
  en este repo: David Sepúlveda / dsepulveda@casamusa.cl

## Estructura
```
Dashboard/
├── app.py                    # Rutas Flask
├── data_loader.py            # Lógica de datos (~1500 líneas)
├── iniciar.bat                # Abre Flask en Windows (instala deps)
├── actualizar_diario.py/.bat # Consolida sin necesitar Flask corriendo
│                              # (usado por Tarea Programada Windows, 19:00)
├── generar_plantilla_ne.py   # Regenera data/NE_x_Facturar.xlsx
├── actualizar_diario.log     # Log de cada corrida automática (gitignored)
├── data/
│   ├── Ventas_2026.xlsx      # Export SAP B1 2026 (gitignored)
│   ├── Ventas_2025.xlsx      # Export SAP B1 2025 (gitignored)
│   ├── *.parquet             # Cache rápido, se regenera solo (gitignored)
│   ├── presupuesto.xlsx      # Presupuesto anual por sucursal
│   ├── metas.xlsx            # Metas mensuales por vendedor
│   ├── NE_x_Facturar.xlsx    # Negocios Ganados x Facturar (semanal, gitignored)
│   └── AAMM_Vtas.xlsx        # Archivo mensual a consolidar (temporal, se
│                              # borra solo tras "Actualizar datos")
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
    └── en_construccion.html  # solo /datos la usa por ahora
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
- Si Ventas_2026.xlsx cambia (mod_time), reconstruye cache solo.
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
     GERENTES** — hoy solo dsepulveda@casamusa.cl).
   - Tarea Programada de Windows `CasaMusa_ActualizarVentas`, corre todos
     los días a las 19:00 vía `actualizar_diario.py` (no necesita que
     Flask esté corriendo). Si no hay archivo nuevo, no hace nada — no es
     error, queda registrado en `actualizar_diario.log`.

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
  (`ORDEN_SUCURSALES`).
- Hay una persona sin mapear en `VEND_HOME`: **EMA SEPULVEDA TUREN**
  (~$33M en SI-STK/2025, cae en OTROS/SE) — ya no trabaja en la empresa,
  se dejó así a propósito, no "corregir".

## Reporte genérico por campo
`get_ventas_por_campo(campo, orden_map=None, top_n=None)` es la función
base detrás de Sucursal/Vendedor/Canal/Familia/Marca/Procedencia — agrupa
venta mes/año actual vs anterior por cualquier columna categórica.
Devuelve `items` (posiblemente truncado a `top_n`, ej. Marca top 30) Y
`total` (SIEMPRE calculado sobre el set completo, antes de truncar) — el
frontend debe usar `d.total` para la fila TOTAL GENERAL, nunca sumar
`items` a mano (si hay truncado, la suma de `items` no es el total real,
puede incluso ser mayor si hay categorías negativas fuera del top N).

## NE x Facturar (Negocios Ganados por facturar)
- `data/NE_x_Facturar.xlsx`: 1 fila por vendedor home (más "OTROS" por
  sucursal), columnas Sucursal/Vendedor bloqueadas, solo Monto NE
  editable. Lo actualiza el gerente comercial ~1 vez por semana.
- `generar_plantilla_ne.py`: regenera la plantilla si cambia el equipo
  de ventas (conserva montos ya cargados para quienes siguen).
- En Proyección: `Proy. Lineal + NE = Proy. Lineal + Monto NE`,
  `Mg con NE = Proy. Mg + Monto NE × 20%` (`MG_NE_PCT` en data_loader.py).
- Si el archivo no existe, todo el mecanismo es no-op (monto_ne = 0).

## Colores (base.html `:root`)
`--red` es un rojo real (`#ef4444`). El verde de marca (`#22a347`, usado
en nav activo, botones, hover) es `--accent`, NO `--red` — hubo un bug
donde `--red` estaba puesto en verde por error y todas las variaciones
negativas se veían verdes. No revivir esa confusión.

## Autenticación
Usuarios hardcodeados en `app.py` (dict `GERENTES`). Sin DB.
`"admin": True` en la entrada de un gerente le da acceso al botón
"Actualizar datos" (oculto en el HTML Y rechazado con 403 en el
servidor si no es admin). Por defecto los gerentes nuevos NO son admin.
Session Flask con secret_key.

## Páginas activas
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

## Páginas en construcción (pendiente)
- /datos — Vista de Datos (explorador de transacciones crudas, con
  filtros + búsqueda; diseño aún no definido con el usuario)

## Tabs BIWISER pendientes de replicar
Los reportes siguen la estructura del sistema BIWISER interno:
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
5. Tras cualquier cambio en data_loader.py o templates, reiniciar Flask
   (matar proceso en puerto 5000 y volver a lanzar) — no hay reloader
   activo (`debug=False`).
6. `app.py` fuerza stdout/stderr a UTF-8 al importar — necesario porque
   la consola de Windows por defecto no soporta los caracteres de caja
   del banner ni tildes en prints.

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
