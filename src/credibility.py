"""
Motor de credibilidad  ·  el corazon del sistema: "quien es quien".

Idea central: una afirmacion solo se puede CALIFICAR cuando se RESUELVE.

  1. Cada afirmacion (rumor) se guarda con su fuente y su hora exacta.
  2. Las afirmaciones del mismo rumor se agrupan en un "cluster" (por tema).
  3. Cuando el rumor se resuelve  ->  se reparte credito/castigo:
        - acerto  -> suma a "aciertos"  (y si fue el primero, "primicia")
        - fallo   -> suma a "fallos"
        - humo    -> afirmar algo que resulto falso suma a "humo"
  4. Con eso, cada fuente acumula su historial: % de acierto, primicias y humo.

Resolver un cluster:
  - Automatico: si el canal OFICIAL publica una afirmacion "confirma" sobre
    ese tema, el cluster se da por "confirmado".
  - Manual: tu marcas el desenlace ->  python run.py resolve <tema> <outcome>
    outcome = confirmado | desmentido | humo
"""
from __future__ import annotations
import hashlib
import datetime as dt

UTC = dt.timezone.utc
OUTCOMES = ("confirmado", "desmentido", "humo")

# Score base según el mejor tier que reporta el rumor afirmativamente.
# Tier 0 = oficial, 1 = muy confiable … 4 = clickbait.
_TIER_BASE = {0: 97, 1: 80, 2: 62, 3: 35, 4: 12}


def empty_ledger() -> dict:
    return {"claims": [], "clusters": {}, "sources": {}}


def _claim_id(item_id: str, tema: str) -> str:
    return hashlib.sha1(f"{item_id}|{tema}".encode()).hexdigest()[:14]


def _ts(claim: dict) -> str:
    """Marca de tiempo para ordenar (primicia)."""
    return claim.get("published") or claim.get("first_seen") or "9999"


# --------------------------------------------------------------------------
# Registro de afirmaciones nuevas
# --------------------------------------------------------------------------
def register(ledger: dict, afirmaciones: list[dict]) -> int:
    """Agrega afirmaciones nuevas al libro y actualiza clusters. Idempotente."""
    known = {c["id"] for c in ledger["claims"]}
    added = 0

    for a in afirmaciones:
        cid = _claim_id(a["item_id"], a["tema"])
        if cid in known:
            continue
        claim = {
            "id": cid,
            "item_id": a["item_id"],
            "source": a["source"],
            "source_handle": a["source_handle"],
            "source_type": a["source_type"],
            "source_tier": a.get("source_tier", 3),
            "url": a["url"],
            "published": a.get("published"),
            "first_seen": a.get("first_seen"),
            "tema": a["tema"],
            "titulo": a["titulo"],
            "categoria": a["categoria"],
            "postura": a["postura"],
        }
        ledger["claims"].append(claim)
        known.add(cid)
        added += 1

        cl = ledger["clusters"].get(a["tema"])
        if not cl:
            ledger["clusters"][a["tema"]] = {
                "tema": a["tema"],
                "titulo": a["titulo"],
                "categoria": a["categoria"],
                "status": "desarrollo",
                "outcome": None,
                "created": dt.datetime.now(UTC).isoformat(),
                "resolved_at": None,
            }

    _mark_first_reporters(ledger)
    auto_resolve(ledger)
    recompute(ledger)
    return added


def _mark_first_reporters(ledger: dict) -> None:
    """Por cada cluster marca quien publico primero (la primicia)."""
    by_tema: dict[str, list] = {}
    for c in ledger["claims"]:
        by_tema.setdefault(c["tema"], []).append(c)
    for tema, claims in by_tema.items():
        claims.sort(key=_ts)
        for idx, c in enumerate(claims):
            c["is_first"] = (idx == 0)
        cl = ledger["clusters"].get(tema)
        if cl and claims:
            cl["first_handle"] = claims[0]["source_handle"]
            cl["first_source"] = claims[0]["source"]


# --------------------------------------------------------------------------
# Resolucion de clusters
# --------------------------------------------------------------------------
def auto_resolve(ledger: dict) -> None:
    """Si una fuente OFICIAL confirma un tema, el cluster queda confirmado."""
    for c in ledger["claims"]:
        if c["source_type"] == "oficial" and c["postura"] in ("afirma",
                                                               "confirma"):
            cl = ledger["clusters"].get(c["tema"])
            if cl and cl["status"] == "desarrollo":
                cl["status"] = "confirmado"
                cl["outcome"] = "confirmado"
                cl["resolved_at"] = dt.datetime.now(UTC).isoformat()


def resolve(ledger: dict, tema: str, outcome: str) -> bool:
    """Marca manualmente el desenlace de un cluster y recalcula puntajes."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome debe ser uno de {OUTCOMES}")
    cl = ledger["clusters"].get(tema)
    if not cl:
        return False
    status = {"confirmado": "confirmado", "desmentido": "desmentido",
              "humo": "humo"}[outcome]
    cl["status"] = status
    cl["outcome"] = outcome
    cl["resolved_at"] = dt.datetime.now(UTC).isoformat()
    recompute(ledger)
    return True


# --------------------------------------------------------------------------
# Calculo de puntajes por fuente
# --------------------------------------------------------------------------
def rumor_score(cluster: dict, all_claims: list[dict]) -> int:
    """
    Devuelve la credibilidad del rumor como entero 0-100.

    Lógica:
      - Resuelto confirmado  → 98
      - Resuelto desmentido/humo → 3
      - Canal oficial niega  → 3  (override inmediato)
      - Base = mejor tier entre fuentes que afirman el rumor
      - +5 por cada fuente independiente adicional (máx +20)
      - +10 si 2+ fuentes de Tier ≤ 2 coinciden (cross-confirmation)
    """
    status = cluster.get("status", "desarrollo")
    if status == "confirmado":
        return 98
    if status in ("desmentido", "humo"):
        return 3

    tema = cluster["tema"]
    claims = [c for c in all_claims if c["tema"] == tema]
    if not claims:
        return 0

    # Denegación oficial → colapso inmediato
    for c in claims:
        if c.get("source_tier", 3) == 0 and c["postura"] == "niega":
            return 3

    affirming = [c for c in claims
                 if c["postura"] in ("afirma", "confirma", "matiza")]
    if not affirming:
        return 10

    best_tier = min(c.get("source_tier", 3) for c in affirming)
    base = _TIER_BASE.get(best_tier, 30)

    n_independent = len({c["source_handle"] for c in affirming})
    boost = min(20, (n_independent - 1) * 5)

    hq_handles = {c["source_handle"] for c in affirming
                  if c.get("source_tier", 3) <= 2}
    if len(hq_handles) >= 2:
        boost += 10

    return min(95, base + boost)


def _score_claim(outcome: str, postura: str) -> str:
    """
    Devuelve 'hit' | 'miss' | 'humo' | 'neutral' segun el desenlace del rumor
    y la postura que habia tomado la fuente.
    """
    if outcome == "confirmado":
        if postura in ("afirma", "confirma", "matiza"):
            return "hit"
        if postura == "niega":
            return "miss"
    elif outcome == "desmentido":
        if postura == "niega":
            return "hit"
        if postura in ("afirma", "confirma"):
            return "miss"
    elif outcome == "humo":
        if postura == "niega":
            return "hit"
        if postura in ("afirma", "confirma"):
            return "humo"
    return "neutral"


def _tier_earned(source_type: str, accuracy, humo_rate: float,
                 n_resolved: int) -> str:
    """Tier ganado basado en historial real (Alta/Media/Baja/Vigilada/Sin datos)."""
    if source_type == "oficial":
        return "Alta"
    if n_resolved < 4 or accuracy is None:
        return "Sin datos"
    if humo_rate >= 0.5:
        return "Vigilada"
    if accuracy >= 0.8 and humo_rate <= 0.15:
        return "Alta"
    if accuracy >= 0.55:
        return "Media"
    return "Baja"


def recompute(ledger: dict) -> None:
    """Recalcula desde cero el historial de todas las fuentes."""
    src: dict[str, dict] = {}

    # Toda fuente con al menos una afirmacion aparece en el tablero.
    for c in ledger["claims"]:
        h = c["source_handle"]
        if h not in src:
            src[h] = {"handle": h, "name": c["source"], "type": c["source_type"],
                      "tier": c.get("source_tier", 3),
                      "n_claims": 0, "hits": 0, "misses": 0,
                      "scoops": 0, "humo": 0, "resolved": 0}
        src[h]["n_claims"] += 1

    # Reparto de credito/castigo solo en clusters resueltos.
    for c in ledger["claims"]:
        cl = ledger["clusters"].get(c["tema"])
        if not cl or cl["status"] == "desarrollo" or not cl["outcome"]:
            continue
        s = src[c["source_handle"]]
        result = _score_claim(cl["outcome"], c["postura"])
        if result == "neutral":
            continue
        s["resolved"] += 1
        if result == "hit":
            s["hits"] += 1
            if c.get("is_first"):
                s["scoops"] += 1
        elif result == "miss":
            s["misses"] += 1
        elif result == "humo":
            s["misses"] += 1
            s["humo"] += 1

    # Metricas derivadas.
    for s in src.values():
        decided = s["hits"] + s["misses"]
        s["accuracy"] = round(100 * s["hits"] / decided) if decided else None
        s["humo_rate"] = round(100 * s["humo"] / s["n_claims"]) \
            if s["n_claims"] else 0
        s["tier_earned"] = _tier_earned(
            s["type"],
            (s["accuracy"] / 100 if s["accuracy"] is not None else None),
            s["humo_rate"] / 100, s["resolved"])

    ledger["sources"] = src


# --------------------------------------------------------------------------
# Vistas para el tablero
# --------------------------------------------------------------------------
def leaderboard(ledger: dict) -> list[dict]:
    """Fuentes ordenadas para la seccion 'Quien es Quien'."""
    earned_rank = {"Alta": 0, "Media": 1, "Sin datos": 2, "Baja": 3, "Vigilada": 4}
    rows = list(ledger["sources"].values())
    rows.sort(key=lambda s: (s.get("tier", 3),
                             earned_rank.get(s["tier_earned"], 9),
                             -(s["accuracy"] or 0), -s["scoops"]))
    return rows


def active_rumors(ledger: dict) -> list[dict]:
    """Clusters con sus afirmaciones, para el 'Radar de Rumores'."""
    out = []
    for tema, cl in ledger["clusters"].items():
        claims = [c for c in ledger["claims"] if c["tema"] == tema]
        claims.sort(key=_ts)
        score = rumor_score(cl, ledger["claims"])
        out.append({
            "tema": tema,
            "titulo": cl["titulo"],
            "categoria": cl["categoria"],
            "status": cl["status"],
            "score": score,
            "first_source": cl.get("first_source", ""),
            "timeline": [{
                "source": c["source"],
                "handle": c["source_handle"],
                "tier": c.get("source_tier", 3),
                "postura": c["postura"],
                "when": c.get("published") or c.get("first_seen"),
                "url": c["url"],
                "is_first": c.get("is_first", False),
            } for c in claims],
        })
    order = {"desarrollo": 0, "confirmado": 1, "desmentido": 2, "humo": 3}
    out.sort(key=lambda r: (order.get(r["status"], 9),
                            -r["score"], -len(r["timeline"])))
    return out
