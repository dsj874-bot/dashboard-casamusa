"""
Cotizador (migracion 028, 2026-11-26). Demo solo para dsepulveda
(USUARIOS_COTIZADOR en app.py).

El vendedor pega el requerimiento del cliente tal como llega (texto o filas
copiadas de Excel). Por cada linea:
  1. Se separa cantidad y descripcion (reglas; o Gemini con "Interpretar con IA").
  2. Se busca en el catalogo (productos, 19 mil codigos) sin IA: codigo exacto,
     referencia del fabricante exacta, lo aprendido antes y, si no, palabras de
     la descripcion ponderadas por lo raras que son (una medida que no calza
     castiga fuerte: un cable de 2,5 no es uno de 1,5).
  3. Se proponen hasta 3 opciones, en el orden que pidio el usuario:
     IMPORTADO primero (codigos 3/7), luego la MARCA que pidio el cliente, luego
     NACIONAL. Los codigos 6 (especiales por proyecto) solo entran si no hay otra.
  4. Precio de la lista de SAP elegida (precio_lista), SIN costo ni margen (es para vendedores), y
     stock de las 6 sucursales (sin Merma: ver memoria del cable en Merma).
Lo que el vendedor confirma queda en cotizador_aprendizaje y se reconoce solo
la proxima vez.
"""
import io
import json
import math
import os
import re
import time
import unicodedata
from collections import defaultdict

import db

LISTAS = {
    "lp_d1_cm": "D1 (60% de la base)", "lp_d2_cm": "D2 (64%)", "lp_d3_cm": "D3 (66%)",
    "lp_d4_cm": "D4 (68%)", "lp_base_cm": "Base CM", "lp_base_cmd": "Base CMD",
}
LISTA_DEFECTO = "lp_d1_cm"     # lo vendido en noviembre calza con D1 (0,99)
BODEGAS = ["San Isidro", "Matta", "Las Condes", "Manuel Rodríguez", "Chicureo", "Maipú"]
CORTO = {"San Isidro": "SI", "Matta": "MT", "Las Condes": "LC", "Manuel Rodríguez": "MR", "Chicureo": "CH", "Maipú": "MP"}
IMPORTADOS = {3, 7}
ESPECIALES = {6}
MAX_LINEAS = 200

STOP = {"de", "la", "el", "los", "las", "con", "para", "y", "en", "un", "una", "por", "del", "al", "sin", "tipo",
        "x", "c", "s", "und", "un", "unidad", "unidades", "uds", "ud", "u", "cant", "cantidad", "item", "color",
        "m", "rollo", "valor", "tira"}
COLORES = {"blanco", "negro", "rojo", "azul", "verde", "gris", "amarillo", "cafe", "beige", "plata", "aluminio"}
SINONIMOS = {
    # "alambre" NO es sinonimo de cable: en el catalogo ALAMBRE = H07V (NYA), CABLE = flexible / LH / THHN.
    "conductor": "cable", "mts": "m", "mt": "m", "metro": "m", "metros": "m", "mtrs": "m",
    "mm2": "mm", "pulgada": "pulg", "pulgadas": "pulg", "plg": "pulg", "termomagnetico": "automatico",
    "tomacorriente": "enchufe", "toma": "enchufe", "lampara": "ampolleta", "ampolletas": "ampolleta",
    "foco": "proyector", "blanca": "blanco", "negra": "negro", "roja": "rojo", "amarilla": "amarillo",
    "canaleta": "moldura", "canalizacion": "moldura", "tubos": "tubo", "cajas": "caja", "rollos": "rollo",
    "amperes": "a", "amper": "a", "amperios": "a", "amp": "a", "watts": "w", "watt": "w", "volts": "v", "volt": "v",
    "reflector": "proyector", "reflectores": "proyector", "focos": "proyector",
}
MARCAS_ALIAS = {
    "legrand": "LEGR", "bticino": "BTIC", "schneider": "SCHN", "philips": "PHIL", "signify": "PHIL",
    "siemens": "SIEM", "vimar": "VIMAR", "3m": "3M", "steck": "STECK", "general": None, "ge": "GE",
    "saime": "SAIM", "stanford": "STFD", "vkb": "VKB", "technolamp": "TECH", "lexo": "LEXO",
}
UNIDADES = r"(?:un|und|unid|unidades|uds|ud|u|m|mt|mts|metros|metro|pzas?|piezas?|jgo|juegos?|pares?|(?:rollos?|cajas?|bolsas?|kits?)\s+de)"

_cache = {"t": 0}


def _norm(txt):
    t = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode().lower()
    t = t.replace('"', " pulg ").replace("''", " pulg ").replace("ø", " ")
    t = re.sub(r"(?<=\d),(?=\d)", ".", t)
    # Polos: "2x16A" en electricidad es 2 polos de 16 A (el catalogo dice "2P 16A").
    t = re.sub(r"\b([1-4])\s*x\s*(\d+(?:\.\d+)?)\s*a\b", r"\1p \2 a", t)
    t = re.sub(r"\b([1-4])\s*polos?\b", r"\1p", t)
    for a, b in (("unipolar", "1p"), ("monopolar", "1p"), ("bipolar", "2p"), ("tripolar", "3p"), ("tetrapolar", "4p"),
                 ("luz fria", " 6500 "), ("blanco frio", " 6500 "), ("fria", " 6500 "), ("frio", " 6500 "),
                 ("luz calida", " 3000 "), ("calida", " 3000 "), ("calido", " 3000 "), ("luz neutra", " 4000 ")):
        t = re.sub(rf"\b{a}\b", b, t)
    t = re.sub(r"(\d)\s*(awg|mm2|mm|cm|mts|mt|m|w|v|a|k|lm|ma|kw|kv|ip)\b", r"\1 \2", t)
    t = re.sub(r"\bn[°o]?\s*(\d{1,2})\s+awg\b", r"\1 awg", t)          # "N°12 AWG" -> "12 awg"
    return t


def _tokens(txt):
    out = []
    for tok in re.findall(r"[a-z0-9]+(?:[./][0-9]+)*", _norm(txt)):
        tok = SINONIMOS.get(tok, tok)
        if tok in STOP or len(tok) == 0:
            continue
        # 20x10 -> 20x10 (medida compuesta) + 20 + 10
        if re.fullmatch(r"\d+(?:\.\d+)?x\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?", tok):
            out.append(tok)
            out += tok.split("x")
            continue
        out.append(tok)
    # Medida con su unidad como una sola pieza ("10awg", "2.5mm", "16a", "50w"): asi "10 AWG" no
    # calza con "10 PARES 24AWG", y "16A" no con un "16" cualquiera.
    medidas = [a + b for a, b in zip(out, out[1:]) if _es_num(a) and "x" not in a and b in UNIDADES_MEDIDA]
    return out + medidas


UNIDADES_MEDIDA = {"awg", "mm", "cm", "a", "w", "v", "k", "ma", "kw", "kv", "lm"}
_RE_MEDIDA = re.compile(r"\d+(?:\.\d+)?(?:awg|mm|cm|a|w|v|k|ma|kw|kv|lm)")


def _es_num(t):
    return bool(re.fullmatch(r"\d+(?:[./]\d+)?(?:x\d+(?:\.\d+)?)*", t))


def _catalogo():
    if time.time() - _cache["t"] < 1800:
        return _cache
    prods = db.query_all(
        """SELECT p.codigo, p.referencia, p.descripcion, p.marca, p.familia, p.id_procedencia, p.procedencia,
                  p.embalaje, pl.lp_base_cm, pl.lp_base_cmd, pl.lp_d1_cm, pl.lp_d2_cm, pl.lp_d3_cm, pl.lp_d4_cm
             FROM public.productos p LEFT JOIN precio_lista pl ON pl.codigo = p.codigo
            WHERE coalesce(p.marca,'') <> 'SERV' AND coalesce(p.familia,'') <> 'SERVICIOS'""")
    stock = defaultdict(dict)
    for r in db.query_all("SELECT codigo, bodega, stock FROM inventario_stock WHERE bodega = ANY(%s) AND stock > 0", (BODEGAS,)):
        stock[r["codigo"]][CORTO[r["bodega"]]] = float(r["stock"])
    venta = {r["codigo"]: float(r["venta"] or 0) for r in db.query_all(
        """SELECT codigo, venta FROM clasificacion_producto
            WHERE alcance='EMPRESA' AND calculo_id = (SELECT max(calculo_id) FROM clasificacion_producto)""")}
    equiv = defaultdict(set)
    for r in db.query_all("SELECT codigo_obligatorio a, codigo_equivalente b FROM productos_obligatorios WHERE codigo_equivalente IS NOT NULL"):
        equiv[r["a"]].add(r["b"]); equiv[r["b"]].add(r["a"])
    P, inv, ref, marcas = {}, defaultdict(set), {}, set()
    for p in prods:
        c = p["codigo"]
        toks = set(_tokens(p["descripcion"]))
        P[c] = {**p, "toks": toks, "stock": stock.get(c, {}), "venta": max(venta.get(c, 0.0), 0.0)}
        for t in toks:
            inv[t].add(c)
        r = _norm(p["referencia"]).replace(" ", "")
        if len(r) >= 4 and r not in (".", "-"):
            ref.setdefault(r, set()).add(c)
        if p["marca"]:
            marcas.add(p["marca"].upper())
    n = len(P)
    idf = {t: math.log(1 + n / len(cs)) for t, cs in inv.items()}
    prefijos = defaultdict(set)
    for t in inv:
        if len(t) >= 5 and t.isalpha():
            prefijos[t[:5]].add(t)
    vmax = max(venta.values() or [1])
    _cache.update(t=time.time(), P=P, inv=inv, idf=idf, ref=ref, marcas=marcas, prefijos=prefijos, equiv=equiv, vmax=vmax, n=n)
    return _cache


# ── Lectura del requerimiento ─────────────────────────────────────────
def _num(s):
    try:
        return float(str(s).replace(".", "").replace(",", ".")) if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", str(s)) \
            else float(str(s).replace(",", "."))
    except ValueError:
        return None


def separar_lineas(texto):
    """Cantidad + descripcion por linea, sin IA."""
    out = []
    for raw in (texto or "").splitlines():
        linea = raw.strip().strip("-•*·").strip()
        if not re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{3,}", linea):
            continue
        cant, desc = None, linea
        if "\t" in raw:
            celdas = [c.strip() for c in raw.split("\t") if c.strip()]
            nums = [c for c in celdas if re.fullmatch(r"\d+(?:[.,]\d+)*", c)]
            textos = [c for c in celdas if c not in nums]
            if nums:
                cant = _num(nums[0])
            desc = " ".join(textos) or linea
        else:
            m = re.match(rf"^(\d+(?:[.,]\d+)?)\s*{UNIDADES}?\.?\s*(?:x|de)?\s+(.+)$", linea, re.I)
            if m:
                cant, desc = _num(m.group(1)), m.group(2)
            else:
                m = re.match(rf"^(.+?)\s*(?:[-:;]|cant\.?|cantidad)?\s+(\d+(?:[.,]\d+)?)\s*{UNIDADES}\.?$", linea, re.I)
                if m:
                    desc, cant = m.group(1), _num(m.group(2))
        if re.search(r"^(item|descripci[oó]n|producto|cant)", desc, re.I) and cant is None:
            continue     # encabezado de tabla
        out.append({"texto": linea, "cantidad": cant or 1, "cantidad_detectada": cant is not None,
                    "descripcion": desc.strip(), "marca_pedida": None, "referencia": None})
        if len(out) >= MAX_LINEAS:
            break
    return out


def interpretar_con_ia(texto):
    """Gemini convierte el texto desordenado en lineas {cantidad, descripcion, marca, referencia}."""
    clave = os.environ.get("GEMINI_API_KEY")
    if not clave:
        raise RuntimeError("Falta configurar la clave de Gemini (GEMINI_API_KEY).")
    from google import genai
    from google.genai import types
    cliente = genai.Client(api_key=clave, http_options=types.HttpOptions(timeout=40_000))
    prompt = ("Eres asistente de una distribuidora de materiales eléctricos en Chile. Del siguiente requerimiento de un "
              "cliente, extrae SOLO los productos pedidos. Devuelve JSON: lista de objetos con 'cantidad' (número, 1 si no "
              "se indica), 'descripcion' (el producto con sus medidas, sin la cantidad), 'marca' (si el cliente la menciona, "
              "si no null) y 'referencia' (código del fabricante si aparece, si no null). No inventes productos.\n\n"
              + texto[:15000])
    modelos = [m for m in os.environ.get("GEMINI_MODELOS_COTIZADOR",
                                          "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.8-flash").split(",") if m]
    ultimo = None
    for modelo in modelos:
        try:
            resp = cliente.models.generate_content(
                model=modelo, contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0))
            items = json.loads(resp.text or "[]")
            if isinstance(items, dict):
                items = next((v for v in items.values() if isinstance(v, list)), [])
            out = []
            for it in items[:MAX_LINEAS]:
                desc = str(it.get("descripcion") or "").strip()
                if not desc:
                    continue
                cant = it.get("cantidad")
                try:
                    cant = float(cant)
                except (TypeError, ValueError):
                    cant = None
                out.append({"texto": desc, "cantidad": cant or 1, "cantidad_detectada": cant is not None,
                            "descripcion": desc, "marca_pedida": it.get("marca"), "referencia": it.get("referencia")})
            return out, modelo
        except Exception as e:     # cuota, modelo saturado o JSON invalido: probar el siguiente
            ultimo = e
    raise RuntimeError(f"Gemini no respondió ({ultimo}).")


# ── Busqueda ──────────────────────────────────────────────────────────
def _marca_de(tokens, marca_txt, cat):
    for t in ([_norm(marca_txt)] if marca_txt else []) + tokens:
        t = t.strip()
        if not t:
            continue
        if t in MARCAS_ALIAS and MARCAS_ALIAS[t]:
            return MARCAS_ALIAS[t]
        if t.upper() in cat["marcas"] and len(t) >= 3 and not _es_num(t):
            return t.upper()
        if len(t) >= 5 and t[:4].upper() in cat["marcas"]:
            return t[:4].upper()
    return None


def buscar(descripcion, referencia=None, limite=30):
    cat = _catalogo()
    P, inv, idf = cat["P"], cat["inv"], cat["idf"]
    toks = _tokens(descripcion)
    exactos = set()
    for t in toks + ([_norm(referencia).replace(" ", "")] if referencia else []):
        if re.fullmatch(r"\d{6}", t) and int(t) in P:
            exactos.add(int(t))
        if t in cat["ref"]:
            exactos |= cat["ref"][t]
    # La marca escrita por el cliente ("legrand") no esta en las descripciones: se usa para la
    # opcion "marca pedida", no para puntuar. Los colores pesan la mitad (el catalogo no siempre los dice).
    marcas_txt = {t for t in toks if t in MARCAS_ALIAS or (t.upper() in cat["marcas"] and not _es_num(t))}
    # Largos y empaques ("rollo 100 mts", "caja de 50", "x 100") no son medidas del producto: no puntuan
    # ni castigan. La medida compuesta 20x10 tampoco castiga (ya estan sus partes 20 y 10).
    n_txt = _norm(descripcion)
    opcionales = set(re.findall(r"(\d+(?:\.\d+)?)\s*(?:m|mt|mts|metros?)\b", n_txt))
    opcionales |= set(re.findall(r"(?:rollos?|cajas?|bolsas?|paquetes?)\s+(?:de\s+)?(\d+(?:\.\d+)?)", n_txt))
    opcionales |= set(re.findall(r"\bx\s+(\d+)\b", n_txt))
    opcionales |= {t for t in toks if _es_num(t) and "x" in t}
    # Plurales del cliente ("focos", "tableros", "diferenciales") al singular del catalogo.
    def _singular(t):
        if t in idf or not t.isalpha():
            return t
        for corte in (2, 1):
            if len(t) > 4 and t.endswith(("es", "s")[2 - corte]) and t[:-corte] in idf:
                return SINONIMOS.get(t[:-corte], t[:-corte])
        return t
    toks = [_singular(t) for t in toks]
    qtok = [t for t in dict.fromkeys(toks) if t not in marcas_txt and t not in opcionales
            and (t in idf or (len(t) >= 5 and t.isalpha()))]
    colores_q = {t for t in qtok if t in COLORES}
    peso = {t: idf.get(t, math.log(1 + cat["n"])) * (0.5 if t in COLORES else 1.0) for t in qtok}
    total = sum(peso.values()) or 1
    puntos = defaultdict(float)
    for t in qtok:
        for c in inv.get(t, ()):
            puntos[c] += peso[t]
        if len(t) >= 5 and t.isalpha():          # prefijos: "electrogalvanizada" ~ "electrogalv"
            for t2 in cat["prefijos"].get(t[:5], ()):
                if t2 != t and (t2.startswith(t) or t.startswith(t2)):
                    for c in inv[t2]:
                        if t not in P[c]["toks"]:
                            puntos[c] += 0.8 * peso[t]
    # Medidas obligatorias: las que traen unidad ("10awg") y los numeros sueltos que no son parte de una.
    con_unidad = [t for t in qtok if _RE_MEDIDA.fullmatch(t)]
    partes = {re.match(r"\d+(?:\.\d+)?", t).group(0) for t in con_unidad}
    nums = con_unidad + [t for t in qtok if _es_num(t) and t not in partes]
    res = []
    for c in set(puntos) | exactos:
        p = P[c]
        s = min(puntos.get(c, 0) / total, 1.0)
        # Precision: si el producto tiene muchas palabras importantes que el cliente no pidio
        # ("TAPA DE EXTREMO p/moldura") pesa menos que el producto justo ("MOLDURA").
        propio = sum(idf.get(t, 0) for t in p["toks"]) or 1
        comun = sum(idf.get(t, 0) for t in p["toks"] if t in peso)
        s *= 0.8 + 0.2 * min(comun / propio, 1.0)
        faltan = sum(1 for t in nums if t not in p["toks"])
        s *= 0.55 ** faltan
        otros_colores = (p["toks"] & COLORES) - colores_q
        if colores_q and otros_colores and not (p["toks"] & colores_q):
            s *= 0.6                                 # pidio blanco y el producto es negro
        if c in exactos:
            s = 1.5
        if not (p["lp_base_cm"] and float(p["lp_base_cm"]) > 1):
            s *= 0.7                                 # sin precio en la lista
        if p["id_procedencia"] in ESPECIALES and c not in exactos:
            s *= 0.75                                # especial de proyecto, no es de reventa
        s += 0.08 * math.log1p(p["venta"]) / math.log1p(cat["vmax"]) + (0.02 if p["stock"] else 0)
        res.append((s, c))
    res.sort(reverse=True)
    return [(s, c) for s, c in res[:limite]], exactos


def _ficha(c, s, lista, tipo=None):
    p = _catalogo()["P"][c]
    precio = float(p[lista] or 0) if p.get(lista) is not None else 0.0
    # Sin costo ni margen (decision del usuario 2026-11-26: el cotizador es para vendedores, y a ellos
    # el margen no se les muestra en ninguna pantalla). No basta con esconderlo: no sale del servidor.
    return {
        "codigo": c, "descripcion": p["descripcion"], "marca": p["marca"], "referencia": p["referencia"],
        "procedencia": "Importado" if p["id_procedencia"] in IMPORTADOS else
                       ("Especial" if p["id_procedencia"] in ESPECIALES else "Nacional"),
        "embalaje": float(p["embalaje"] or 1), "stock": p["stock"], "stock_total": sum(p["stock"].values()),
        "precio": precio, "score": round(s, 3), "tipo": tipo,
    }


def _aprendidos(textos):
    norms = list({re.sub(r"\s+", " ", _norm(t)).strip() for t in textos})
    if not norms:
        return {}
    filas = db.query_all("""SELECT texto_norm, codigo FROM cotizador_aprendizaje WHERE texto_norm = ANY(%s)
                             ORDER BY veces DESC, actualizado_en DESC""", (norms,))
    out = {}
    for f in filas:
        out.setdefault(f["texto_norm"], f["codigo"])
    return out


def _primera(descripcion):
    """Primera palabra del producto en el catalogo (CABLE, ALAMBRE, INTERRUPTOR...)."""
    t = _tokens(descripcion)
    return t[0] if t else ""


def _expandir(lineas):
    """"cable 10/12 awg" son DOS medidas: una linea por medida (misma cantidad, para revisar)."""
    out = []
    for l in lineas:
        m = re.search(r"\b(\d{1,2})\s*/\s*(\d{1,2})\s*(awg)\b", l["descripcion"], re.I)
        if m:
            for medida in (m.group(1), m.group(2)):
                d = l["descripcion"][:m.start()] + f"{medida} AWG" + l["descripcion"][m.end():]
                out.append({**l, "descripcion": d, "cantidad_detectada": False})
        else:
            out.append(l)
    return out[:MAX_LINEAS]


def reconocer(lineas, lista=LISTA_DEFECTO):
    lista = lista if lista in LISTAS else LISTA_DEFECTO
    lineas = _expandir(lineas)
    cat = _catalogo()
    aprendido = _aprendidos([l["descripcion"] for l in lineas])
    out = []
    for l in lineas:
        res, exactos = buscar(l["descripcion"], l.get("referencia"))
        marca = _marca_de(_tokens(l["descripcion"]), l.get("marca_pedida"), cat)
        clave = re.sub(r"\s+", " ", _norm(l["descripcion"])).strip()
        opciones, usados = [], set()

        def agregar(c, s, tipo):
            if c not in usados and c in cat["P"]:
                usados.add(c); opciones.append(_ficha(c, s, lista, tipo))

        if clave in aprendido:
            agregar(aprendido[clave], 2.0, "aprendido")
        for s, c in res:
            if c in exactos:
                agregar(c, s, "exacto")
        top = res[0][0] if res else 0
        corte = max(0.42, top * 0.72)
        pool = [(s, c) for s, c in res if s >= corte]
        # Mismo tipo de producto: la primera palabra del cliente ("cable", "interruptor", "tablero")
        # tiene que estar en la alternativa; si no, un "terminal 2,5mm" pasaria por cable 2,5mm.
        tipo = next((t for t in _tokens(l["descripcion"]) if t.isalpha() and len(t) >= 3
                     and t not in MARCAS_ALIAS and t.upper() not in cat["marcas"]), None)
        if tipo and any(tipo in cat["P"][c]["toks"] for _, c in pool):
            pool = [(s, c) for s, c in pool if tipo in cat["P"][c]["toks"]]
        orden = lambda x: (bool(cat["P"][x[1]]["stock"]), x[0])   # con stock primero, luego parecido
        imp = sorted([x for x in pool if cat["P"][x[1]]["id_procedencia"] in IMPORTADOS], key=orden, reverse=True)
        # Equivalente importado declarado en obligatorios para el mejor nacional
        if not imp and res:
            for e in cat["equiv"].get(res[0][1], ()):
                if e in cat["P"] and cat["P"][e]["id_procedencia"] in IMPORTADOS:
                    imp = [(res[0][0] * 0.95, e)]
        if imp:
            agregar(imp[0][1], imp[0][0], "importado")
        if marca:
            m = [x for x in res if (cat["P"][x[1]]["marca"] or "").upper() == marca and x[0] >= max(0.3, top * 0.5)]
            if m:
                agregar(max(m, key=orden)[1], max(m, key=orden)[0], "marca")
        nac = sorted([x for x in pool if cat["P"][x[1]]["id_procedencia"] not in IMPORTADOS | ESPECIALES], key=orden, reverse=True)
        if nac:
            agregar(nac[0][1], nac[0][0], "nacional")
        # Otra linea de producto que el cliente tambien nombro: "cable alambre 4mm" puede ser
        # CABLE libre de halogeno o ALAMBRE NYA; se muestran las dos.
        qset = set(_tokens(l["descripcion"]))
        primeras = {_primera(cat["P"][o["codigo"]]["descripcion"]) for o in opciones}
        for s, c in sorted(res, key=orden, reverse=True):
            pw = _primera(cat["P"][c]["descripcion"])
            if s >= max(0.35, top * 0.6) and pw in qset and pw not in primeras \
                    and cat["P"][c]["id_procedencia"] not in ESPECIALES:
                agregar(c, s, "otra_linea")
                break
        if not opciones and res:
            agregar(res[0][1], res[0][0], "parecido")
        otras = [_ficha(c, s, lista) for s, c in res[:10] if c not in usados][:6]
        mejor = max((o["score"] for o in opciones), default=0)
        conf = "alta" if mejor >= 1.0 or (opciones and opciones[0]["tipo"] == "aprendido") else \
               "media" if mejor >= 0.7 else "baja" if opciones else "sin"
        out.append({**l, "marca_detectada": marca, "opciones": opciones, "otras": otras, "confianza": conf,
                    "elegida": opciones[0]["codigo"] if opciones else None})
    return {"lista": lista, "listas": LISTAS, "lineas": out}


def buscar_manual(q, lista=LISTA_DEFECTO):
    lista = lista if lista in LISTAS else LISTA_DEFECTO
    res, _ = buscar(q, limite=12)
    return {"resultados": [_ficha(c, s, lista) for s, c in res]}


def aprender(pares, usuario):
    filas = []
    for p in pares or []:
        try:
            cod = int(p.get("codigo"))
        except (TypeError, ValueError):
            continue
        t = re.sub(r"\s+", " ", _norm(p.get("texto"))).strip()[:300]
        if t:
            filas.append((t, cod, usuario))
    if filas:
        with db.conexion_pool() as conn:
            with conn.cursor() as cur:
                # Lo ultimo que eligio el vendedor para ese texto reemplaza lo aprendido antes
                # (si no, un error aprendido seguiria ganando por tener mas "veces").
                cur.executemany("DELETE FROM cotizador_aprendizaje WHERE texto_norm = %s AND codigo <> %s",
                                [(t, c) for t, c, _ in filas])
                cur.executemany("""INSERT INTO cotizador_aprendizaje (texto_norm, codigo, usuario) VALUES (%s,%s,%s)
                                   ON CONFLICT (texto_norm, codigo) DO UPDATE
                                   SET veces = cotizador_aprendizaje.veces + 1, usuario = EXCLUDED.usuario, actualizado_en = now()""",
                                filas)
            conn.commit()
    return {"aprendidos": len(filas)}


def excel(lineas, cliente=""):
    """Excel para SAP: codigo, descripcion, cantidad, precio unitario neto, total."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cotizacion"
    ws.append([f"Cotización Musa360{' - ' + cliente if cliente else ''}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    enc = ["Código", "Descripción", "Marca", "Cantidad", "Precio unitario", "Total", "Texto del cliente"]
    ws.append(enc)
    for c in ws[3]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="22A347")
    for l in lineas:
        cant = float(l.get("cantidad") or 0); precio = float(l.get("precio") or 0)
        ws.append([int(l["codigo"]), l.get("descripcion"), l.get("marca"), cant, precio, round(cant * precio), l.get("texto")])
    fila_total = ws.max_row + 1
    ws.cell(fila_total, 5, "Total neto").font = Font(bold=True)
    ws.cell(fila_total, 6, f"=SUM(F4:F{fila_total - 1})").font = Font(bold=True)
    for col, ancho in zip("ABCDEFG", (10, 60, 9, 10, 15, 15, 50)):
        ws.column_dimensions[col].width = ancho
    for fila in ws.iter_rows(min_row=4, min_col=5, max_col=6):
        for c in fila:
            c.number_format = '#,##0'
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
