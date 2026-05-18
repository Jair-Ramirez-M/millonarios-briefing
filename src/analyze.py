"""
Capa de analisis.

Si hay GEMINI_API_KEY (capa gratuita de Google), usa IA para:
  - redactar el resumen del briefing,
  - clasificar cada publicacion (tema y nivel de credibilidad),
  - extraer "afirmaciones" para el motor de credibilidad.

Si NO hay clave, todo sigue funcionando en modo basico (reglas por palabras
clave). El sistema nunca se cae por falta de IA: solo pierde finura.
"""
from __future__ import annotations
import os
import re
import json
import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL_DEFAULT = "llama-3.3-70b-versatile"

# Palabras clave para la clasificacion basica (sin IA).
TAG_RULES = [
    ("Mercado",    ["fichaje", "refuerzo", "llega", "llegar", "contrata",
                     "venta", "vende", "transferencia", "interes", "sale",
                     "salida", "negociaci", "oferta", "prestamo"]),
    ("Renovacion", ["renov", "extiende", "amplia contrato", "continua"]),
    ("Lesiones",   ["lesion", "lesionado", "molestia", "recuper", "baja"]),
    ("Dirigencia", ["presidente", "junta", "dirigencia", "directiv"]),
    ("Tecnico",    ["dt ", "tecnico", "entrenador", "bustos", "cuerpo tecnico"]),
    ("Partido",    ["gol", "venci", "empat", "perdi", "derrota", "victoria",
                    "partido", "alineaci", "clasico", "rival"]),
]

# Patrones para detectar afirmaciones de mercado sin IA.
_CLAIM_PATTERNS = [
    ("Llegada",    ["llega a", "llega al", "llego a", "llegar a", "ficha con",
                    "fichaje de", "refuerzo de", "contrata a", "se une a",
                    "nuevo refuerzo", "nuevo jugador"]),
    ("Salida",     ["sale de", "salida de", "se va de", "cedido al", "cedido a",
                    "transferido a", "vende a", "abandona", "deja el club"]),
    ("Renovacion", ["renov", "extiende contrato", "nuevo contrato",
                    "amplia su contrato", "seguira en"]),
    ("Tecnico",    ["nuevo dt", "nuevo tecnico", "nuevo entrenador",
                    "director tecnico", "reemplaza al"]),
    ("Interes",    ["interes en", "interesado en", "oferta por", "negoci",
                    "sondeo", "seguimiento a", "apunta a", "quiere a"]),
]

_SKIP_ENTITY = {"millonarios", "millos", "bogota", "colombia", "liga",
                "betplay", "dimayor", "futbol", "equipo", "club", "bogotá"}


def _entity_slug(title: str, categoria: str) -> str:
    """Slug representativo: categoria + primera entidad nombrada del titulo."""
    names = re.findall(
        r'\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})*\b',
        title,
    )
    for name in names:
        norm = name.lower()
        if norm not in _SKIP_ENTITY and not any(s in norm for s in _SKIP_ENTITY):
            slug = re.sub(r'\s+', '-', norm)
            return f"{categoria.lower()}-{slug}"
    words = re.sub(r'[^a-z0-9\s]', '', title.lower()).split()[:3]
    return f"{categoria.lower()}-{'-'.join(words)}"


def _extract_afirmaciones_basic(items: list[dict]) -> list[dict]:
    """Detecta afirmaciones de mercado/plantel con reglas de palabras clave."""
    result = []
    seen = set()
    for it in items:
        if it["id"] in seen:
            continue
        t = it["title"].lower()
        matched = None
        for cat, patterns in _CLAIM_PATTERNS:
            if any(p in t for p in patterns):
                matched = cat
                break
        if not matched:
            continue
        seen.add(it["id"])
        result.append({
            "item_id":       it["id"],
            "source":        it["source"],
            "source_handle": it["source_handle"],
            "source_type":   it["source_type"],
            "source_tier":   it.get("source_tier", 3),
            "url":           it["url"],
            "published":     it.get("published"),
            "first_seen":    it.get("first_seen"),
            "tema":          _entity_slug(it["title"], matched),
            "titulo":        it["title"],
            "categoria":     matched,
            "postura":       "afirma",
        })
    return result


# --------------------------------------------------------------------------
# Cliente Groq (Llama 3.3 — capa gratuita generosa)
# --------------------------------------------------------------------------
def ai_available() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


def _call_ai(prompt: str, settings: dict, max_tokens: int = 4096) -> str:
    key = os.environ["GROQ_API_KEY"]
    model = settings.get("groq_model", GROQ_MODEL_DEFAULT)
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        },
        timeout=90,
        verify=False,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _extract_json(text: str):
    """Quita las vallas ```json y parsea el primer objeto JSON del texto."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("respuesta sin JSON")
    return json.loads(text[start:end + 1])


# --------------------------------------------------------------------------
# Clasificacion basica (sin IA)
# --------------------------------------------------------------------------
def _basic_tag(title: str) -> str:
    t = title.lower()
    for tag, words in TAG_RULES:
        if any(w in t for w in words):
            return tag
    return "Plantel"


def _basic_cred(item: dict) -> str:
    st = item.get("source_type", "")
    if st == "oficial":
        return "alta"
    if st in ("periodista", "prensa", "agregador"):
        return "media"
    return "media"


def _basic_analysis(briefing_items: list[dict], analysis_items: list[dict],
                    settings: dict) -> dict:
    """Analisis por reglas cuando no hay IA disponible."""
    enriched = []
    for it in briefing_items:
        enriched.append({**it,
                          "tag": _basic_tag(it["title"]),
                          "tipo": "noticia",
                          "cred": _basic_cred(it)})
    n_src = len({it["source_handle"] for it in briefing_items})
    titulares = "; ".join(it["title"] for it in briefing_items[:3])
    resumen = (
        f"Se procesaron {len(briefing_items)} publicaciones de {n_src} fuentes "
        f"en las ultimas horas sobre {settings.get('team','el equipo')}. "
        f"Lo mas reciente: {titulares}." if briefing_items else
        "No hubo publicaciones nuevas de las fuentes monitoreadas en esta "
        "ventana de tiempo."
    )
    afirmaciones = _extract_afirmaciones_basic(analysis_items)
    return {"resumen": resumen, "items": enriched, "afirmaciones": afirmaciones}


# --------------------------------------------------------------------------
# Analisis con IA
# --------------------------------------------------------------------------
def _build_prompt(items: list[dict], team: str) -> str:
    lineas = []
    for i, it in enumerate(items):
        lineas.append(
            f'[{i}] fuente="{it["source"]}" tipo={it["source_type"]} '
            f'fecha={it.get("published") or "?"}\n'
            f'    titulo: {it["title"]}\n'
            f'    extracto: {(it.get("summary") or "")[:240]}'
        )
    bloque = "\n".join(lineas)
    return f"""Eres analista de prensa deportiva especializado en {team}.
Te paso publicaciones recientes de distintas fuentes. Responde UNICAMENTE con
un objeto JSON valido (sin texto adicional, sin vallas de codigo) con esta forma:

{{
  "resumen": "Dos parrafos en espanol. Primer parrafo: lo mas importante del
              dia sobre el equipo. Segundo parrafo: el panorama de rumores y
              mercado, separando lo confirmado de lo especulativo ('humo').",
  "items": [
    {{"i": 0, "tag": "Mercado|Plantel|Lesiones|Renovacion|Dirigencia|Tecnico|Partido",
      "tipo": "noticia|rumor|opinion",
      "cred": "alta|media|baja"}}
  ],
  "afirmaciones": [
    {{"i": 0,
      "tema": "slug-corto-del-rumor",
      "titulo": "Frase corta que describe el rumor",
      "categoria": "Llegada|Salida|Renovacion|Tecnico|Interes|Otro",
      "postura": "afirma|niega|matiza|confirma"}}
  ]
}}

Reglas:
- Incluye en "afirmaciones" SOLO las publicaciones que hacen una afirmacion
  concreta sobre llegadas, salidas, renovaciones o cambios de tecnico.
- Usa el mismo "tema" (slug) para publicaciones que hablan del MISMO rumor,
  asi se agrupan.
- "postura": afirma = lo da por hecho/probable; niega = lo desmiente;
  matiza = lo pone en duda o con condiciones; confirma = es la fuente oficial.
- "cred": alta si la fuente es oficial o muy seria; baja si parece humo.

PUBLICACIONES:
{bloque}
"""


def analyze(briefing_items: list[dict], analysis_items: list[dict],
            settings: dict) -> dict:
    """
    Devuelve {"resumen", "items" (con tag/tipo/cred), "afirmaciones"}.
    briefing_items: ventana corta (24h) para el resumen visual.
    analysis_items: ventana larga (720h) para deteccion de rumores.
    Cae a modo basico ante cualquier problema con la IA.
    """
    if not ai_available():
        print("  (sin GROQ_API_KEY: analisis en modo basico)")
        return _basic_analysis(briefing_items, analysis_items, settings)

    try:
        prompt = _build_prompt(briefing_items, settings.get("team", "el equipo"))
        raw = _call_ai(prompt, settings)
        parsed = _extract_json(raw)
    except Exception as exc:
        print(f"  ! Analisis con IA fallo ({exc}); uso modo basico")
        return _basic_analysis(briefing_items, analysis_items, settings)

    # Fusiona la clasificacion de la IA con los items del briefing.
    cls = {c["i"]: c for c in parsed.get("items", []) if "i" in c}
    enriched = []
    for i, it in enumerate(briefing_items):
        c = cls.get(i, {})
        enriched.append({**it,
                         "tag": c.get("tag", _basic_tag(it["title"])),
                         "tipo": c.get("tipo", "noticia"),
                         "cred": c.get("cred", _basic_cred(it))})

    # Afirmaciones de la IA (ventana corta) + basicas de ventana larga.
    seen_ids = set()
    afirmaciones = []
    for a in parsed.get("afirmaciones", []):
        i = a.get("i")
        if i is None or i >= len(briefing_items):
            continue
        src = briefing_items[i]
        seen_ids.add(src["id"])
        afirmaciones.append({
            "item_id":       src["id"],
            "source":        src["source"],
            "source_handle": src["source_handle"],
            "source_type":   src["source_type"],
            "source_tier":   src.get("source_tier", 3),
            "url":           src["url"],
            "published":     src.get("published"),
            "first_seen":    src.get("first_seen"),
            "tema":          a.get("tema", "sin-tema"),
            "titulo":        a.get("titulo", src["title"]),
            "categoria":     a.get("categoria", "Otro"),
            "postura":       a.get("postura", "afirma"),
        })

    # Complementa con deteccion basica sobre el historial completo.
    for afc in _extract_afirmaciones_basic(analysis_items):
        if afc["item_id"] not in seen_ids:
            afirmaciones.append(afc)

    return {"resumen": parsed.get("resumen", ""),
            "items": enriched,
            "afirmaciones": afirmaciones}
