"""Analista IA de Musa360 (fase 1: ventas y compras).

El "cerebro" es Gemini (Google), plan gratuito, elegido por el usuario el
2026-10-16: en ese plan Google puede usar las preguntas y respuestas para
mejorar sus productos (lo acepto el usuario). Por eso las herramientas
(agente_herramientas.py) mandan solo resultados ya calculados y cortos.

El cerebro esta aislado en _GeminiCerebro: cambiar a otro proveedor es
escribir otra clase con el mismo metodo `turno()`, sin tocar herramientas
ni pantalla.

Variables de entorno: GEMINI_API_KEY (obligatoria), GEMINI_MODEL (opcional).
"""
import json
import os
import time
from datetime import date

import db
import agente_herramientas as H

# gemini-3.6-flash es el principal: en el plan gratuito 3.8-flash se satura y
# no respondia en 60 s (prueba del 2026-10-16), mientras 3.6 contesta en segundos.
MODELO = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
# Si el modelo principal esta saturado (503) o agoto su cuota gratuita diaria
# (429: 20 consultas por modelo al dia, medido el 2026-10-16) se pasa a estos,
# en orden. Cada modelo tiene su propia cuota: en total ~80 consultas al dia.
MODELOS_RESPALDO = [m for m in os.environ.get("GEMINI_MODELOS_RESPALDO", "gemini-3.8-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite").split(",") if m]
MAX_PASOS = 8          # llamadas a herramientas por pregunta
MAX_HISTORIAL = 8      # mensajes previos que se le recuerdan

SISTEMA = """Eres el Analista de Musa360, el sistema de gestión de Casa Musa (Chile), distribuidora y retail de
materiales eléctricos e iluminación. Respondes preguntas de la gerencia sobre ventas, márgenes, stock y compras.

REGLAS
- Responde SIEMPRE en español de Chile, claro y breve: primero la respuesta directa, después el detalle.
- NUNCA inventes cifras. Todo número sale de una herramienta. Si ninguna herramienta lo entrega, dilo.
- Responde SOLO lo que las herramientas miden. No reemplaces un dato por otro parecido: "documentos emitidos"
  NO son despachos. Hoy no tienes datos de logística (despachos, retiros, choferes), cotizaciones ni solicitudes
  de inventario: si te preguntan por eso, di que todavía no tienes acceso a esa información.
- Si la pregunta es ambigua (período, sucursal), asume lo más razonable y dilo en una línea al final.
- Indica siempre el período de los datos que usaste.
- Montos en pesos chilenos con punto de miles ($12.345.678). Sobre $1.000.000 usa millones con "MM"
  (divide por 1.000.000 y usa punto de miles y coma decimal). Ejemplos exactos:
  12345678 -> $12,3 MM | 543274567 -> $543,3 MM | 5104210000 -> $5.104,2 MM | 1030326278 -> $1.030,3 MM.
  Nunca escribas "$5.1 MM" para miles de millones. Porcentajes con un decimal y coma (12,5%).
- Usa tablas en formato markdown cuando compares más de 3 elementos. Máximo 15 filas.
- Si no conoces el código de una marca, familia, vendedor, cliente o proveedor, usa buscar_valores primero.
- Termina con una sugerencia de acción solo si los datos la justifican (una línea).

DEFINICIONES DE CASA MUSA
- Venta = venta neta sin IVA; las notas de crédito restan.
- Margen = utilidad bruta ajustada al reporte del ERP (desde 2026). El margen de 2025 o antes NO es comparable:
  no compares márgenes contra años anteriores a 2026 aunque te lo pidan; explica por qué.
- Sucursales (código de venta): LC Las Condes, MR Manuel Rodríguez, MT Matta, CH Chicureo, MP Maipú,
  SE San Isidro empresas, CMD San Isidro distribución (San Isidro completo = SE + CMD), CANAL DIGITAL (e-commerce),
  OF Oficina. San Isidro es además el Centro de Distribución (CD) que abastece a las demás.
- Cobertura = (stock + tránsito + órdenes de compra de stock pendientes) / venta mensual, en meses.
- Prioritarios = productos obligatorios que siempre deben tener stock.
- Clase ABC/XYZ: A/B/C por aporte al margen (A = 80% del margen), X/Y/Z por regularidad de la venta (X = regular).
- CUP = costo unitario promedio; el stock y las compras se valorizan a CUP.
- Marcas frecuentes: BTIC Bticino, LEGR Legrand (RX3 = protecciones Legrand), SNTH Sinthesi, SCHN Schneider,
  PHIL Philips, TECH línea importada propia. Ante la duda usa buscar_valores.

CONTEXTO DE LOS DATOS
- Hoy es {hoy}. Ventas cargadas hasta el {ventas_hasta}. Inventario cargado el {inventario_cargado}.
- "Este mes" = desde el día 1 del mes de la última venta cargada hasta esa fecha. "Este año" = desde el 1 de enero.
"""


class AgenteError(Exception):
    """Error mostrable al usuario (sin clave, limite de Google, etc.)."""


class _GeminiCerebro:
    def __init__(self):
        clave = os.environ.get("GEMINI_API_KEY")
        if not clave:
            raise AgenteError("Falta configurar la clave de Gemini (GEMINI_API_KEY).")
        from google import genai
        from google.genai import types
        self.genai, self.types = genai, types
        # Tope por llamada: Vercel corta la funcion a los 300 s
        self.cliente = genai.Client(api_key=clave, http_options=types.HttpOptions(timeout=30_000))
        self.tiempos = []
        self.tool = types.Tool(function_declarations=[
            types.FunctionDeclaration(name=n, description=h["descripcion"], parameters_json_schema=h["parametros"])
            for n, h in H.HERRAMIENTAS.items()])

    def conversar(self, sistema, historial, pregunta, ejecutar):
        """Corre el ciclo pregunta -> herramientas -> respuesta. `ejecutar(nombre, args)`
        devuelve el resultado de la herramienta. Retorna (texto, tokens_in, tokens_out)."""
        t = self.types
        config = t.GenerateContentConfig(
            system_instruction=sistema, tools=[self.tool],
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True))
        contenidos = [t.Content(role="user" if m["rol"] == "usuario" else "model",
                                parts=[t.Part.from_text(text=m["texto"])]) for m in historial]
        contenidos.append(t.Content(role="user", parts=[t.Part.from_text(text=pregunta)]))
        tin = tout = 0
        for _ in range(MAX_PASOS + 1):
            resp = self._generar(contenidos, config)
            um = getattr(resp, "usage_metadata", None)
            if um:
                tin += um.prompt_token_count or 0
                tout += um.candidates_token_count or 0
            llamadas = resp.function_calls or []
            if not llamadas:
                return (resp.text or "").strip(), tin, tout
            # La respuesta del modelo se reenvia tal cual (trae las firmas de razonamiento)
            contenidos.append(resp.candidates[0].content)
            partes = []
            for ll in llamadas:
                resultado = ejecutar(ll.name, dict(ll.args or {}))
                partes.append(t.Part.from_function_response(name=ll.name, response={"resultado": resultado}))
            # Las respuestas de herramientas van con rol "user" (la API rechaza "tool")
            contenidos.append(t.Content(role="user", parts=partes))
        raise AgenteError("La pregunta necesitó demasiados pasos. Intenta hacerla más específica.")

    def _generar(self, contenidos, config):
        """Llama a Gemini. Ante 503 (modelo saturado) reintenta y pasa a los modelos
        de respaldo; el modelo que respondio queda en self.modelo_usado. Una vez que
        un modelo responde se sigue con el mismo en el resto de la conversacion."""
        modelos = [getattr(self, "modelo_usado", None) or MODELO] + \
            [m for m in MODELOS_RESPALDO if m != (getattr(self, "modelo_usado", None) or MODELO)]
        ultimo, agotados = None, []
        for i, modelo in enumerate(modelos):
            for intento in range(2):
                t0 = time.time()
                try:
                    resp = self.cliente.models.generate_content(model=modelo, contents=contenidos, config=config)
                    self.tiempos.append((modelo, round(time.time() - t0, 1)))
                    self.modelo_usado = modelo
                    return resp
                except Exception as e:  # errores de la API de Google
                    self.tiempos.append((modelo, round(time.time() - t0, 1), str(getattr(e, "code", type(e).__name__))))
                    codigo = getattr(e, "code", None)
                    if codigo is None and "timeout" in type(e).__name__.lower():
                        codigo = 504
                    ultimo = e
                    if codigo == 429:   # cuota gratuita del modelo agotada: siguiente modelo
                        agotados.append(modelo)
                        break
                    if codigo not in (500, 503, 504):
                        raise AgenteError(f"Google no respondió: {e}")
                    if codigo == 504:   # no respondio a tiempo: pasar directo al siguiente modelo
                        break
                    if intento == 0:
                        time.sleep(2)
        if len(agotados) == len(modelos):
            raise AgenteError("Se agotó la cuota gratuita diaria de Google (unas 25 a 35 preguntas al día). "
                              "Se renueva durante la madrugada; vuelve a preguntar más tarde.")
        raise AgenteError("Los modelos de Google están saturados o sin cuota en este momento. "
                          "Intenta de nuevo en unos minutos.")


def _resumen(resultado):
    """Texto corto de lo que devolvio una herramienta, para el '¿cómo lo calculé?'."""
    if isinstance(resultado, dict):
        if "error" in resultado:
            return "Error: " + resultado["error"]
        for k in ("periodo", "criterio"):
            if k in resultado:
                return str(resultado[k])
    return ""


def responder(pregunta, historial, usuario):
    """Responde una pregunta. historial: [{rol: usuario|agente, texto}] de la conversacion.
    Devuelve {respuesta, pasos, ms}. Registra todo en agente_log."""
    pregunta = (pregunta or "").strip()
    if not pregunta:
        raise AgenteError("Escribe una pregunta.")
    if len(pregunta) > 2000:
        raise AgenteError("La pregunta es muy larga (máximo 2.000 caracteres).")
    t0 = time.time()
    f = H.fechas_datos()
    sistema = SISTEMA.format(hoy=date.today().isoformat(), ventas_hasta=f["ventas_hasta"],
                             inventario_cargado=f["inventario_cargado"])
    historial = [m for m in (historial or []) if m.get("rol") in ("usuario", "agente") and m.get("texto")][-MAX_HISTORIAL:]
    pasos = []

    def ejecutar(nombre, args):
        h = H.HERRAMIENTAS.get(nombre)
        if not h:
            res = {"error": f"Herramienta desconocida: {nombre}"}
        else:
            try:
                res = h["fn"](**args)
            except TypeError as e:
                res = {"error": f"Parámetros no válidos: {e}"}
            except ValueError as e:
                res = {"error": str(e)}
            except Exception as e:
                res = {"error": f"Falló la consulta: {e}"}
        res = json.loads(json.dumps(res, ensure_ascii=False, default=str))
        pasos.append({"herramienta": nombre, "parametros": args, "resumen": _resumen(res)})
        return res

    error, texto, tin, tout, cerebro = None, "", 0, 0, None
    try:
        cerebro = _GeminiCerebro()
        texto, tin, tout = cerebro.conversar(sistema, historial, pregunta, ejecutar)
        if not texto:
            texto = "No pude armar una respuesta. Intenta reformular la pregunta."
    except AgenteError as e:
        error = str(e)
    ms = int((time.time() - t0) * 1000)
    try:
        db.execute("""INSERT INTO agente_log (usuario, pregunta, respuesta, pasos, modelo, tokens_entrada, tokens_salida, ms, error)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                   (usuario, pregunta, texto or None, json.dumps(pasos, ensure_ascii=False, default=str), getattr(cerebro, "modelo_usado", MODELO),
                    tin, tout, ms, error))
    except Exception:
        pass  # el registro nunca debe botar la respuesta
    if error:
        raise AgenteError(error)
    return {"respuesta": texto, "pasos": pasos, "ms": ms}


def historial_usuario(usuario, limite=30):
    filas = db.query_all("""SELECT creado_en, pregunta, respuesta, error FROM agente_log
                            WHERE usuario = %s ORDER BY creado_en DESC LIMIT %s""", (usuario, limite))
    return [{"fecha": f["creado_en"].isoformat(), "pregunta": f["pregunta"], "respuesta": f["respuesta"],
             "error": f["error"]} for f in filas]
