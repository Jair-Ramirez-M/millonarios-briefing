"""
Recolectores de fuentes.

  - YouTube: cada canal expone un feed RSS gratuito. Solo hay que convertir el
    @handle en el channel_id (UC...). Eso se hace una vez y se cachea.
  - Tineus:  agregador de prensa. Se raspa la pagina de seccion de Millonarios.
  - RSS:     lector generico para cualquier web con feed propio.

Todos los recolectores devuelven una lista de "items crudos" con esta forma:

  {
    "origin":        "youtube" | "tineus" | "rss",
    "source":        "Nombre legible de la fuente",
    "source_handle": "@handle  o  dominio.com",   <- clave estable de la fuente
    "source_type":   "oficial | periodista | partidario | prensa | agregador",
    "source_weight": 0.5,
    "title":         "...",
    "summary":       "...",
    "url":           "https://...",
    "published":     "2026-05-16T05:33:00-05:00"  (ISO 8601, puede ser None),
    "thumbnail":     "https://..."  (puede ser "")
  }
"""
from __future__ import annotations
import re
import time
import datetime as dt
from urllib.parse import urlparse

import requests
import feedparser
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; MillosBriefingBot/1.0; +github-actions)"}
YT_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
UTC = dt.timezone.utc

# Instancias Nitter (frontend alternativo de X) con RSS gratuito.
# Se prueban en orden; si una falla se intenta la siguiente.
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://xcancel.com",
    "https://nitter.1d4.us",
]

# ── Estadísticas del último run (se rellenan en collect_all) ────────────────
_LAST_STATS: list[dict] = []

def get_last_stats() -> list[dict]:
    """Devuelve las estadísticas de recolección del último run."""
    return list(_LAST_STATS)

# Python 3.12+ genera SSLEOFError con YouTube. Suprimimos la advertencia y
# usamos verify=False solo para peticiones a dominios de Google/YouTube
# (datos públicos, no enviamos credenciales).
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Nombres legibles para dominios de prensa conocidos (mejora la presentacion;
# si un dominio no esta aqui se usa el dominio tal cual).
KNOWN_MEDIA = {
    "elespectador.com": "El Espectador",
    "colombia.com": "Colombia.com",
    "primertiempo.co": "Primer Tiempo",
    "futbolred.com": "Futbolred",
    "eltiempo.com": "El Tiempo",
    "antena2.com": "Antena 2",
    "win.com.co": "Win Sports",
    "winsports.co": "Win Sports",
    "marca.com": "Marca Claro",
    "as.com": "AS Colombia",
    "elpais.com.co": "El Pais Cali",
    "rcnradio.com": "RCN Radio",
    "caracol.com.co": "Caracol Radio",
    "bluradio.com": "Blu Radio",
    "noticiasrcn.com": "Noticias RCN",
    "semana.com": "Semana",
    "goal.com": "Goal",
    "espn.com.co": "ESPN",
    "pulzo.com": "Pulzo",
    "vbar.caracol.com.co": "VBar Caracol",
}

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
         "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}

# "16 may., 05:33 a.m."  ->  dia, mes, hora, minuto, a/p
_TS = re.compile(
    r"(\d{1,2})\s+([a-záéíóú]{3})\.?,?\s*(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\.?",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _get(url: str, timeout: int = 25) -> str:
    r = requests.get(url, headers=UA, timeout=timeout, verify=False)
    r.raise_for_status()
    return r.text


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return "fuente-desconocida"


def _iso_from_struct(struct) -> str | None:
    """Convierte un time.struct_time (UTC, de feedparser) a ISO 8601."""
    if not struct:
        return None
    try:
        return dt.datetime(*struct[:6], tzinfo=UTC).isoformat()
    except Exception:
        return None


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------
def resolve_channel_id(handle: str, cache: dict) -> str | None:
    """
    Convierte un @handle de YouTube en su channel_id (UC...).
    Usa una cache (config/channels.lock.json) para no repetir el trabajo.
    """
    handle = handle.strip()
    if handle in cache and cache[handle]:
        return cache[handle]

    url = f"https://www.youtube.com/{handle}"
    try:
        html = _get(url)
    except Exception as exc:
        print(f"  ! No se pudo abrir {handle}: {exc}")
        return None

    # El patrón más fiable: YouTube incrusta el link al propio RSS en el HTML.
    rss_pat = re.compile(
        r'<link[^>]+type=["\']application/rss\+xml["\'][^>]+'
        r'href=["\'][^"\']*channel_id=(UC[\w-]{20,})["\']',
        re.IGNORECASE,
    )
    m = rss_pat.search(html)
    if not m:
        # También puede aparecer en orden inverso (href antes de type)
        rss_pat2 = re.compile(
            r'<link[^>]+href=["\'][^"\']*channel_id=(UC[\w-]{20,})["\'][^>]+'
            r'type=["\']application/rss\+xml["\']',
            re.IGNORECASE,
        )
        m = rss_pat2.search(html)

    if m:
        cid = m.group(1)
        # El tag <link> es autoreferencial pero validamos igualmente
        try:
            test = requests.head(YT_RSS.format(cid=cid),
                                 headers=UA, timeout=10, verify=False)
            if test.status_code < 500:   # 200 OK o incluso 404 = ID válido
                cache[handle] = cid
                return cid
        except Exception:
            pass
        # Si el HEAD falla del todo, igual confiamos en el tag RSS del HTML
        cache[handle] = cid
        return cid

    # Fallback: recorre patrones en orden de especificidad y valida con HEAD.
    for pattern in (
        r'"CHANNEL_ID"\s*:\s*"(UC[\w-]{20,})"',
        r'"externalChannelId"\s*:\s*"(UC[\w-]{20,})"',
        r'"externalId"\s*:\s*"(UC[\w-]{20,})"',
        r'<link rel="canonical" href="[^"]*?/channel/(UC[\w-]{20,})"',
        r'"channelId"\s*:\s*"(UC[\w-]{20,})"',
    ):
        for match in re.finditer(pattern, html):
            cid = match.group(1)
            try:
                test = requests.head(YT_RSS.format(cid=cid),
                                     headers=UA, timeout=8, verify=False)
                if test.status_code == 200:
                    cache[handle] = cid
                    return cid
            except Exception:
                continue

    print(f"  ! No se encontro channel_id para {handle}")
    return None


def _yt_field(entry, *keys) -> str:
    for k in keys:
        val = entry.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # feedparser a veces anida la descripcion de media RSS
    mg = entry.get("media_group") or {}
    if isinstance(mg, dict) and mg.get("media_description"):
        return str(mg["media_description"]).strip()
    return ""


def _yt_thumb(entry) -> str:
    thumbs = entry.get("media_thumbnail") or []
    if thumbs and isinstance(thumbs, list):
        return thumbs[0].get("url", "")
    return ""


def collect_youtube(channels: list[dict], cache: dict) -> list[dict]:
    """Recolecta los videos recientes de cada canal de YouTube via RSS."""
    items: list[dict] = []
    for ch in channels:
        handle = ch["handle"]
        cid = resolve_channel_id(handle, cache)
        if not cid:
            continue
        try:
            rss_url = YT_RSS.format(cid=cid)
            r = requests.get(rss_url, headers=UA, timeout=20, verify=False)
            if r.status_code != 200:
                # YouTube devuelve 404/500 para algunos canales; no es fatal
                feed = feedparser.parse("")   # feed vacío
            else:
                feed = feedparser.parse(r.text)
        except Exception as exc:
            print(f"  ! Error leyendo feed de {handle}: {exc}")
            continue

        for e in feed.entries:
            items.append({
                "origin": "youtube",
                "source": ch["name"],
                "source_handle": handle,
                "source_type": ch.get("type", "partidario"),
                "source_tier": int(ch.get("tier", 3)),
                "source_weight": float(ch.get("weight", 0.5)),
                "title": e.get("title", "").strip(),
                "summary": _yt_field(e, "summary", "description")[:600],
                "url": e.get("link", ""),
                "published": _iso_from_struct(e.get("published_parsed")),
                "thumbnail": _yt_thumb(e),
            })
        n = len(feed.entries)
        print(f"  + YouTube {ch['name']}: {n} videos")
        _LAST_STATS.append({
            "name": ch["name"], "handle": ch["handle"],
            "type": "youtube", "tier": int(ch.get("tier", 3)),
            "items": n, "status": "ok" if n > 0 else "vacio",
        })
        time.sleep(0.3)  # cortesia
    return items


# --------------------------------------------------------------------------
# Tineus (agregador de prensa)
# --------------------------------------------------------------------------
def _parse_ts(text: str, year: int) -> str | None:
    """Busca un patron de fecha tipo '16 may., 05:33 a.m.' dentro de `text`."""
    m = _TS.search(text)
    if not m:
        return None
    day, mon, hh, mm, ap = m.groups()
    month = MESES.get(mon.lower()[:3])
    if not month:
        return None
    hour = int(hh) % 12
    if ap.lower() == "p":
        hour += 12
    try:
        # hora local de Colombia (UTC-5)
        local = dt.timezone(dt.timedelta(hours=-5))
        return dt.datetime(year, month, int(day), hour, int(mm),
                           tzinfo=local).isoformat()
    except Exception:
        return None


def _source_before_ts(text: str) -> str:
    """El nombre del medio suele ir justo antes de la fecha."""
    m = _TS.search(text)
    if not m:
        return ""
    pre = text[:m.start()].strip(" -|·\n\t")
    words = pre.split()
    return " ".join(words[-4:]) if words else ""


def collect_tineus(url: str, name: str, type_: str, weight: float) -> list[dict]:
    """Raspa la pagina de seccion de Millonarios en Tineus."""
    items: list[dict] = []
    try:
        html = _get(url)
    except Exception as exc:
        print(f"  ! Tineus no disponible: {exc}")
        return items

    soup = BeautifulSoup(html, "html.parser")
    year = dt.date.today().year
    seen: set[str] = set()

    for tag in soup.find_all(["h2", "h3"]):
        a = tag.find("a", href=True)
        if not a:
            continue
        link = a["href"].strip()
        title = a.get_text(" ", strip=True)
        # Los titulares de Tineus enlazan al medio ORIGINAL (dominio externo).
        if not link.startswith("http") or "tineus.co" in link:
            continue
        if len(title) < 15 or link in seen:
            continue
        seen.add(link)

        # Bloque contenedor: subimos un par de niveles para captar fecha/medio.
        block = tag
        for _ in range(3):
            if block.parent:
                block = block.parent
        btext = block.get_text(" ", strip=True)

        published = _parse_ts(btext, year)
        domain = _domain(link)
        nice = KNOWN_MEDIA.get(domain) or _source_before_ts(btext) or domain

        items.append({
            "origin": "tineus",
            "source": nice,
            "source_handle": domain,          # clave estable por dominio
            "source_type": "prensa",
            "source_tier": 2,                 # prensa real pero via agregador
            "source_weight": float(weight),
            "title": title,
            "summary": "",
            "url": link,
            "published": published,
            "thumbnail": "",
        })

    print(f"  + Tineus: {len(items)} titulares de prensa")
    _LAST_STATS.append({
        "name": name, "handle": "tineus.co",
        "type": "scrape", "tier": 2,
        "items": len(items), "status": "ok" if items else "error",
    })
    return items


# --------------------------------------------------------------------------
# RSS generico (para webs que en el futuro tengan feed propio)
# --------------------------------------------------------------------------
def collect_rss(url: str, name: str, type_: str, weight: float,
                tier: int = 2) -> list[dict]:
    items: list[dict] = []
    try:
        r = requests.get(url, headers=UA, timeout=20, verify=False)
        feed = feedparser.parse(r.text if r.status_code == 200 else "")
    except Exception as exc:
        print(f"  ! RSS {name} fallo: {exc}")
        _LAST_STATS.append({"name": name, "handle": _domain(url),
                             "type": "rss", "tier": tier,
                             "items": 0, "status": "error"})
        return items

    for e in feed.entries:
        items.append({
            "origin": "rss",
            "source": name,
            "source_handle": _domain(url),
            "source_type": type_,
            "source_tier": tier,
            "source_weight": float(weight),
            "title": e.get("title", "").strip(),
            "summary": re.sub(r"<[^>]+>", "", e.get("summary", ""))[:600],
            "url": e.get("link", ""),
            "published": _iso_from_struct(e.get("published_parsed")
                                          or e.get("updated_parsed")),
            "thumbnail": "",
        })
    print(f"  + RSS {name}: {len(items)} entradas")
    _LAST_STATS.append({"name": name, "handle": _domain(url),
                         "type": "rss", "tier": tier,
                         "items": len(items),
                         "status": "ok" if items else "vacio"})
    return items


# --------------------------------------------------------------------------
# Twitter / X  (via Nitter RSS — gratuito, sin API key)
# --------------------------------------------------------------------------
def collect_twitter_nitter(accounts: list[dict]) -> list[dict]:
    """Recolecta tweets recientes via Nitter RSS con fallback entre instancias."""
    items: list[dict] = []
    for acc in accounts:
        handle = acc["handle"].lstrip("@")
        found = 0
        status = "error"
        for base in NITTER_INSTANCES:
            try:
                rss_url = f"{base}/{handle}/rss"
                r = requests.get(rss_url, headers=UA, timeout=12, verify=False)
                if r.status_code != 200:
                    continue
                feed = feedparser.parse(r.text)
                if not feed.entries:
                    continue
                for e in feed.entries[:15]:
                    title = re.sub(r"<[^>]+>", "", e.get("title", "")).strip()
                    summary = re.sub(r"<[^>]+>", "", e.get("summary", "")).strip()[:500]
                    items.append({
                        "origin":        "twitter",
                        "source":        acc.get("name", f"@{handle}"),
                        "source_handle": f"@{handle}",
                        "source_type":   acc.get("type", "periodista"),
                        "source_tier":   int(acc.get("tier", 2)),
                        "source_weight": float(acc.get("weight", 0.6)),
                        "title":         title or summary[:120],
                        "summary":       summary,
                        "url":           e.get("link", ""),
                        "published":     _iso_from_struct(e.get("published_parsed")),
                        "thumbnail":     "",
                    })
                found = len(feed.entries)
                status = "ok"
                break   # instancia funcionó, no probamos más
            except Exception:
                continue
        print(f"  + X/Twitter @{handle}: {found} tweets"
              + ("" if status == "ok" else " (sin acceso a Nitter)"))
        _LAST_STATS.append({
            "name": acc.get("name", f"@{handle}"),
            "handle": f"@{handle}",
            "type": "twitter", "tier": int(acc.get("tier", 2)),
            "items": found, "status": status,
        })
    return items


# --------------------------------------------------------------------------
# Orquestador de recoleccion
# --------------------------------------------------------------------------
def collect_all(sources: dict, channel_cache: dict) -> list[dict]:
    """Ejecuta todos los recolectores definidos en config/sources.yaml."""
    global _LAST_STATS
    _LAST_STATS = []   # reset al inicio de cada run
    items: list[dict] = []

    if sources.get("youtube"):
        print("Recolectando YouTube...")
        items += collect_youtube(sources["youtube"], channel_cache)

    for s in sources.get("scrape", []):
        print(f"Recolectando {s['name']}...")
        if s.get("parser") == "tineus":
            items += collect_tineus(s["url"], s["name"],
                                    s.get("type", "agregador"),
                                    s.get("weight", 0.5))

    for s in sources.get("rss", []):
        print(f"Recolectando {s['name']}...")
        items += collect_rss(s["url"], s["name"],
                             s.get("type", "prensa"),
                             s.get("weight", 0.5),
                             int(s.get("tier", 2)))

    if sources.get("twitter"):
        print("Recolectando X / Twitter (via Nitter)...")
        items += collect_twitter_nitter(sources["twitter"])

    print(f"Total recolectado: {len(items)} items crudos")
    return items
