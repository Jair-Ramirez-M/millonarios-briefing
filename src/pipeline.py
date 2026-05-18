"""
Normalizacion y almacenamiento.

Toma los items crudos de los recolectores, les pone un id estable, los filtra
por relevancia, los fusiona con el historial y descarta lo viejo.
"""
from __future__ import annotations
import json
import hashlib
import datetime as dt
from pathlib import Path

UTC = dt.timezone.utc


def item_id(url: str) -> str:
    """Identificador estable basado en la URL (sirve para deduplicar)."""
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]


def load_json(path: str | Path, default):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: str | Path, data) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                 encoding="utf-8")


def _parse_iso(s: str | None):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception:
        return None


def is_relevant(item: dict, settings: dict) -> bool:
    """
    Filtra por relevancia al equipo masculino.
      - Descarta cualquier titulo con una palabra excluida (femenino, sub-20...).
      - A las fuentes generalistas (origin != youtube) les exige mencionar
        al club; los canales de YouTube de la lista ya son 100% Millonarios.
    """
    text = f"{item.get('title','')} {item.get('summary','')}".lower()
    for bad in settings.get("exclude_keywords", []):
        if bad.lower() in text:
            return False
    if item.get("origin") != "youtube":
        inc = settings.get("include_keywords", [])
        if inc and not any(k.lower() in text for k in inc):
            return False
    return True


def normalize(raw_items: list[dict], settings: dict) -> list[dict]:
    """Pone id, marca de recoleccion y filtra. Deduplica dentro del lote."""
    now = dt.datetime.now(UTC).isoformat()
    out: dict[str, dict] = {}
    for it in raw_items:
        if not it.get("url") or not it.get("title"):
            continue
        if not is_relevant(it, settings):
            continue
        iid = item_id(it["url"])
        it = dict(it)
        it["id"] = iid
        it["collected_at"] = now
        out[iid] = it          # dedup por id dentro del lote
    return list(out.values())


def merge_history(new_items: list[dict], history: list[dict],
                  retention_days: int) -> list[dict]:
    """
    Fusiona el lote nuevo con el historial.
      - Si un item ya existia, conserva su fecha de PRIMERA aparicion
        (first_seen): clave para saber 'quien lo dijo primero'.
      - Descarta items mas viejos que `retention_days`.
    """
    by_id: dict[str, dict] = {h["id"]: h for h in history}
    now = dt.datetime.now(UTC)

    for it in new_items:
        iid = it["id"]
        if iid in by_id:
            # ya conocido: no cambiamos su first_seen
            by_id[iid]["last_seen"] = it["collected_at"]
        else:
            it["first_seen"] = it["collected_at"]
            it["last_seen"] = it["collected_at"]
            by_id[iid] = it

    cutoff = now - dt.timedelta(days=retention_days)
    kept = []
    for it in by_id.values():
        ref = _parse_iso(it.get("published")) or _parse_iso(it.get("first_seen"))
        if ref and ref < cutoff:
            continue
        kept.append(it)

    kept.sort(key=lambda x: x.get("published") or x.get("first_seen") or "",
              reverse=True)
    return kept


def recent_items(items: list[dict], window_hours: int) -> list[dict]:
    """Items publicados (o vistos por primera vez) dentro de la ventana."""
    cutoff = dt.datetime.now(UTC) - dt.timedelta(hours=window_hours)
    out = []
    for it in items:
        ref = _parse_iso(it.get("published")) or _parse_iso(it.get("first_seen"))
        if ref and ref >= cutoff:
            out.append(it)
    return out
