"""
Ensamblado del briefing.

Combina el analisis de las publicaciones con las vistas del motor de
credibilidad y produce el JSON que consume el tablero (docs/data/briefing.json).
"""
from __future__ import annotations
import datetime as dt

from . import credibility

COT = dt.timezone(dt.timedelta(hours=-5))   # hora de Colombia
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic"]


def current_shift() -> tuple[str, str]:
    """Decide si es edicion de manana o de tarde segun la hora de Colombia."""
    hour = dt.datetime.now(COT).hour
    if hour < 13:
        return "manana", "Edicion Manana"
    return "tarde", "Edicion Tarde"


def _fmt(iso: str | None) -> tuple[str, str]:
    """ISO -> ('Hoy'/'Ayer'/'12 may', 'HH:MM') en hora de Colombia."""
    if not iso:
        return "s/f", "--:--"
    try:
        d = dt.datetime.fromisoformat(iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        d = d.astimezone(COT)
    except Exception:
        return "s/f", "--:--"
    today = dt.datetime.now(COT).date()
    delta = (today - d.date()).days
    if delta == 0:
        label = "Hoy"
    elif delta == 1:
        label = "Ayer"
    else:
        label = f"{d.day} {MESES[d.month - 1]}"
    return label, d.strftime("%H:%M")


def build(analysis: dict, ledger: dict, settings: dict) -> dict:
    """Construye el objeto briefing completo para el tablero."""
    shift, shift_label = current_shift()

    items_out = []
    for it in analysis["items"]:
        date_lbl, time_lbl = _fmt(it.get("published") or it.get("first_seen"))
        items_out.append({
            "tag": it.get("tag", "Plantel"),
            "tipo": it.get("tipo", "noticia"),
            "title": it["title"],
            "source": it["source"],
            "source_type": it.get("source_type", ""),
            "cred": it.get("cred", "media"),
            "date": date_lbl,
            "time": time_lbl,
            "url": it["url"],
        })

    rumors = credibility.active_rumors(ledger)
    for r in rumors:
        for node in r["timeline"]:
            d, t = _fmt(node["when"])
            node["when_label"] = f"{d} {t}"

    sources = credibility.leaderboard(ledger)

    return {
        "generated_at": dt.datetime.now(COT).isoformat(),
        "shift": shift,
        "shift_label": shift_label,
        "team": settings.get("team", "Millonarios FC"),
        "stats": {
            "items": len(items_out),
            "sources": len({it["source"] for it in analysis["items"]}),
            "rumors": len([r for r in rumors if r["status"] == "desarrollo"]),
        },
        "summary": analysis.get("resumen", ""),
        "items": items_out,
        "rumors": rumors,
        "sources": sources,
    }
