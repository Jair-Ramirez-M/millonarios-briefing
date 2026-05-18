#!/usr/bin/env python3
"""
Briefing Millonarios FC  ·  orquestador.

Uso:
  python run.py                      Ejecucion completa (recolecta + analiza +
                                     arma el briefing). Es lo que corre el cron.
  python run.py collect              Solo recolecta y guarda items (sin IA).
  python run.py rumors               Lista los rumores y su 'tema' (slug).
  python run.py resolve <tema> <x>   Marca el desenlace de un rumor.
                                     <x> = confirmado | desmentido | humo
"""
import sys
import os
from pathlib import Path

import yaml

# Carga .env si existe (para desarrollo local; en CI usa secrets del repo).
_env = Path(__file__).resolve().parent / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from src import collectors, pipeline, analyze, credibility, briefing

ROOT = Path(__file__).resolve().parent
CFG = ROOT / "config"
DATA = ROOT / "data"
SITE_DATA = ROOT / "docs" / "data"

ITEMS_FILE = DATA / "items.json"
LEDGER_FILE = DATA / "ledger.json"
LOCK_FILE = CFG / "channels.lock.json"
FEEDBACK_FILE = CFG / "feedback.json"
BRIEFING_FILE = SITE_DATA / "briefing.json"
SOURCES_FILE = SITE_DATA / "sources.json"


def _load_cfg():
    sources = yaml.safe_load((CFG / "sources.yaml").read_text(encoding="utf-8"))
    settings = yaml.safe_load((CFG / "settings.yaml").read_text(encoding="utf-8"))
    return sources, settings


def cmd_collect(settings, sources) -> list:
    """Recolecta, normaliza y fusiona con el historial. Devuelve items."""
    cache = pipeline.load_json(LOCK_FILE, {})
    raw = collectors.collect_all(sources, cache)
    pipeline.save_json(LOCK_FILE, cache)          # guarda IDs de canal resueltos

    history = pipeline.load_json(ITEMS_FILE, [])
    fresh = pipeline.normalize(raw, settings)
    merged = pipeline.merge_history(fresh, history,
                                    settings.get("retention_days", 21))
    pipeline.save_json(ITEMS_FILE, merged)
    print(f"Historial: {len(merged)} items ({len(fresh)} en este lote)")

    # Publica el estado de las fuentes para el dashboard.
    import datetime as _dt
    stats = collectors.get_last_stats()
    pipeline.save_json(SOURCES_FILE, {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "sources": stats,
    })
    return merged


def cmd_run():
    """Ejecucion completa: recolecta -> analiza -> credibilidad -> briefing."""
    sources, settings = _load_cfg()

    items = cmd_collect(settings, sources)

    # Feedback del usuario (items descartados manualmente desde el tablero).
    feedback = pipeline.load_json(FEEDBACK_FILE, {"dismissed": {}})
    n_dismissed = len(feedback.get("dismissed", {}))
    if n_dismissed:
        print(f"Feedback: {n_dismissed} items descartados por el usuario")

    # Ventana corta para el briefing visual (noticias del dia).
    briefing_items = pipeline.apply_feedback(
        pipeline.recent_items(items, settings.get("briefing_window_hours", 24)),
        feedback, settings)
    print(f"Publicaciones en la ventana del briefing: {len(briefing_items)}")

    # Ventana larga para el analisis de rumores (hasta 30 dias de historial).
    analysis_hours = settings.get("analysis_window_hours", 720)
    analysis_items = pipeline.apply_feedback(
        pipeline.recent_items(items, analysis_hours),
        feedback, settings)
    print(f"Publicaciones para analisis de rumores: {len(analysis_items)}")

    print("Analizando...")
    analysis = analyze.analyze(briefing_items, analysis_items, settings)

    ledger = pipeline.load_json(LEDGER_FILE, credibility.empty_ledger())
    added = credibility.register(ledger, analysis.get("afirmaciones", []))
    print(f"Motor de credibilidad: {added} afirmaciones nuevas, "
          f"{len(ledger['clusters'])} rumores en seguimiento")
    pipeline.save_json(LEDGER_FILE, ledger)

    brief = briefing.build(analysis, ledger, settings)
    pipeline.save_json(BRIEFING_FILE, brief)

    # Copia historica fechada (registro permanente).
    stamp = brief["generated_at"][:16].replace(":", "")
    pipeline.save_json(SITE_DATA / "history" / f"{stamp}.json", brief)

    print(f"\nBriefing listo  ->  {BRIEFING_FILE}")
    print(f"   {brief['shift_label']} · {brief['stats']['items']} items · "
          f"{brief['stats']['rumors']} rumores activos")


def cmd_rumors():
    """Lista los rumores en seguimiento con su slug 'tema'."""
    ledger = pipeline.load_json(LEDGER_FILE, credibility.empty_ledger())
    if not ledger["clusters"]:
        print("Aun no hay rumores registrados.")
        return
    print(f"{'TEMA (slug)':<32} {'ESTADO':<12} TITULO")
    print("-" * 78)
    for tema, cl in ledger["clusters"].items():
        print(f"{tema:<32} {cl['status']:<12} {cl['titulo']}")
    print("\nPara cerrar uno:  python run.py resolve <tema> "
          "<confirmado|desmentido|humo>")


def cmd_resolve(tema, outcome):
    """Marca el desenlace de un rumor y recalcula los puntajes."""
    ledger = pipeline.load_json(LEDGER_FILE, credibility.empty_ledger())
    try:
        ok = credibility.resolve(ledger, tema, outcome)
    except ValueError as exc:
        print(f"Error: {exc}")
        return
    if not ok:
        print(f"No existe el rumor '{tema}'. Usa 'python run.py rumors'.")
        return
    pipeline.save_json(LEDGER_FILE, ledger)
    print(f"Rumor '{tema}' marcado como '{outcome}'. Puntajes actualizados.")
    print("Vuelve a correr 'python run.py' para refrescar el tablero.")


def main():
    args = sys.argv[1:]
    if not args:
        cmd_run()
    elif args[0] == "collect":
        sources, settings = _load_cfg()
        cmd_collect(settings, sources)
    elif args[0] == "rumors":
        cmd_rumors()
    elif args[0] == "resolve" and len(args) == 3:
        cmd_resolve(args[1], args[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
