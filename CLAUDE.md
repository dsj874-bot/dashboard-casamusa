# CasaMusa — Dashboard de Gerencia

## Stack
- Python 3.x + Flask + pandas
- Templates Jinja2 + Chart.js (CDN)
- Sin base de datos — lee Excel exports de SAP B1

## Estructura
```
Dashboard/
├── app.py               # Rutas Flask (~287 líneas)
├── data_loader.py       # Lógica de datos (~1423 líneas)
├── iniciar.bat          # Abre Flask en Windows
├── data/
│   ├── Ventas_2026.xlsx # Export SAP B1 2026 (14MB, ~76k filas)
│   ├── Ventas_2025.xlsx # Export SAP B1 2025 (22MB)
│   ├── presupuesto.xlsx # Presupuesto anual por sucursal
│   └── metas.xlsx       # Metas mensuales por vendedor
└── templates/
    ├── base.html        # Layout + sidebar + nav
    ├── login.html
    ├── resumen.html     # /resumen
    ├── proyeccion.html  # /proyeccion
    ├── metas.html       # /metas
    ├── ppto.html        # /ppto
    ├── sucursales.html  # /sucursales
    ├── vta_acum.html    # /vta_acum
    ├── vta_mes_mg.html  # /vta_mes_mg
    ├── vta_mg_mensual.html # /vta_mg_mensual
    └── en_construccion.html
```

## Columnas SAP B1 (Ventas_20XX.xlsx)
DOC_SAP, FOLIO, TIPO_DOC, FECHA_CONTA, FECHA_DOC, CODIGO_CLIENTE, NOMBRE_CLIENTE,
PROCEDENCIA, SUCURSAL, CODIGO_CM, ID_PROCEDENCIA, CODIGO_PROVEEDOR, DESCRIPCION,
MARCA, UNIDAD_MEDIDA, FAMILIA, SUBFAMILIA, GRUPO, CANTIDAD, COSTO_CUP, COSTO_TOTAL,
PRECIO_UNITARIO, TOTAL, UTILIDAD_BRUTA, MG_BRUTO, VENDEDOR, COND_PAGO, EMPRESA,
PROVEEDOR_POR_DEFECTO, LIQUIDAR, "TIPO VENTA", ESTATUS_SKU

Nota: "TIPO VENTA" (con espacio) se renombra a TIPO_VENTA al cargar.
Se agregan columnas: ANO, MES, DIA (desde FECHA_CONTA), SUCURSAL_LOGICA, VENDEDOR_RPT.

## Cache
- data_loader crea automáticamente Ventas_20XX.pkl al primer arranque
- Siguientes arranques leen desde pkl (instantáneo)
- Si Ventas_2026.xlsx cambia (mod_time), reconstruye pkl solo
- NUNCA crear el pkl desde Linux/sandbox — genera incompatibilidad con Windows

## Sucursales lógicas (SUCURSAL_LOGICA)
SAP maneja sucursales físicas. La función _aplicar_sucursal_logica() mapea:
- SI-STK → SE / CMD / CANAL DIGITAL según vendedor
- Resto de sucursales mapean directamente
Orden: MT, LC, MR, SE, CMD, CH, MP, CANAL DIGITAL, OF

## Autenticación
Usuarios hardcodeados en app.py (dict GERENTES). Sin DB.
Session Flask con secret_key.

## Páginas activas
| Ruta | Template | Función datos |
|------|----------|---------------|
| /resumen | resumen.html | get_resumen() |
| /proyeccion | proyeccion.html | get_proyeccion(filtros) |
| /metas | metas.html | get_seguimiento_metas(filtros) |
| /ppto | ppto.html | get_seguimiento_ppto() |
| /sucursales | sucursales.html | get_ventas_por_sucursal() |
| /vta_acum | vta_acum.html | get_vta_acum(filtros) |
| /vta_mes_mg | vta_mes_mg.html | get_vta_mes_mg_acum(filtros) |
| /vta_mg_mensual | vta_mg_mensual.html | get_vta_mg_mensual(filtros) |

## Páginas en construcción (próximas)
- /vendedores — Por Vendedor
- /canal — Por Canal de Venta
- /familia — Por Familia / Marca
- /datos — Vista de Datos

## Tabs BIWISER pendientes de replicar
Los reportes siguen la estructura del sistema BIWISER interno:
- Evolutivo por Tipo de Venta
- Evolutivo por Categoría
- Vta Mg
- Cobertura/Alcance

## Problema activo al 2026-07-16
Flask tarda mucho al arrancar o se cuelga cargando datos.
- Causa probable: algún error en data_loader.py o pkl corrupto
- Estado: Ventas_2026.pkl fue borrado, debe recrearse en Windows
- Si sigue colgando: revisar si pd.read_excel() se cuelga con Ventas_2026.xlsx
- Posible fix: agregar timeout o leer con engine='calamine' (más rápido que openpyxl)

## Reglas importantes al editar
1. TODAS las rutas Flask deben ir ANTES de `if __name__ == "__main__":` en app.py
2. Para editar data_loader.py usar escritura byte-level en Python — el Edit tool
   trunca archivos con caracteres acentuados en comentarios
3. Números en CLP: siempre enteros completos, formato es-CL
   JS: `'$' + Math.round(v).toLocaleString('es-CL')`
4. No crear pkl desde sandbox Linux — solo Flask en Windows debe crearlo

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
