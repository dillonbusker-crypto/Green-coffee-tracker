"""Optional enrichment: fill missing producer / varietal / region / process from the
description using Claude Haiku. Runs only when ANTHROPIC_API_KEY is set. Results are
cached by product id + updated_at in scraper/cache/enrich.json (committed), so each
listing costs one call in its lifetime.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import anthropic

from common import iter_chunks, detect_country, region_for

log = logging.getLogger("gct.enrich")

MODEL = os.environ.get("GCT_ENRICH_MODEL", "claude-haiku-5-5")
BATCH = 12
MAX_ITEMS_PER_RUN = int(os.environ.get("GCT_ENRICH_MAX", "400"))

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "country": {"type": ["string", "null"]},
                    "region": {"type": ["string", "null"], "description": "growing region within the country, e.g. Yirgacheffe, Huila"},
                    "producer": {"type": ["string", "null"], "description": "farm, producer, cooperative or washing station"},
                    "varietals": {"type": "array", "items": {"type": "string"}},
                    "process": {"type": ["string", "null"], "description": "one of Washed, Natural, Honey, Anaerobic, Wet-hulled, Pulped natural, Carbonic maceration, Experimental, Monsooned, or null"},
                    "score": {"type": ["number", "null"], "description": "cupping score if stated, 80-100"},
                },
                "required": ["id", "country", "region", "producer", "varietals", "process", "score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

SYSTEM = (
    "You extract structured facts about green (unroasted) coffee listings. For each item use only what the "
    "text states; never guess. Leave a field null (or an empty list) when it is not stated. Normalise country "
    "names in English. Varietals as short canonical names (e.g. 'SL28', 'Gesha', 'Caturra', 'Heirloom'). "
    "Process must be one of: Washed, Natural, Honey, Anaerobic, Wet-hulled, Pulped natural, Carbonic maceration, "
    "Experimental, Monsooned."
)


def _needs(c: dict) -> bool:
    text = (c.get("description") or "")
    if len(text) < 60:
        return False
    return not c.get("producer") or not c.get("varietals") or not c.get("subregion") or not c.get("process") or not c.get("country")


def enrich_all(coffees: list[dict], *, cache_path: Path) -> dict:
    cache: dict[str, dict] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("bad enrich cache, starting fresh: %s", e)
    client = anthropic.Anthropic()
    stats = {"model": MODEL, "cached": 0, "requested": 0, "applied": 0, "errors": 0}

    def ck(c: dict) -> str:
        return f"{c['id']}|{c.get('updated_at') or ''}"

    todo: list[dict] = []
    for c in coffees:
        if not _needs(c):
            continue
        hit = cache.get(ck(c))
        if hit is not None:
            _apply(c, hit)
            stats["cached"] += 1
        else:
            todo.append(c)
    todo = todo[:MAX_ITEMS_PER_RUN]

    for chunk in iter_chunks(todo, BATCH):
        payload = [{
            "id": c["id"], "title": c["title"],
            "known": {k: c.get(k) for k in ("country", "subregion", "producer", "varietals", "process", "score")},
            "text": (c.get("description") or "")[:1500],
            "notes": c.get("notes"),
        } for c in chunk]
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=4000,
                system=SYSTEM,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": "Extract facts for these listings:\n" + json.dumps(payload, ensure_ascii=False)}],
            )
            stats["requested"] += len(chunk)
            if resp.stop_reason == "refusal":
                stats["errors"] += 1
                log.warning("enrichment refused for a batch")
                continue
            text = next((b.text for b in resp.content if b.type == "text"), "{}")
            data = json.loads(text)
            by_id = {it["id"]: it for it in data.get("items", []) if isinstance(it, dict) and it.get("id")}
            for c in chunk:
                item = by_id.get(c["id"])
                if item is None:
                    continue
                cache[ck(c)] = item
                _apply(c, item)
                stats["applied"] += 1
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            stats["errors"] += 1
            log.warning("enrichment batch failed: %s", e)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            stats["errors"] += 1
            log.warning("enrichment parse failed: %s", e)

    # prune cache entries for coffees that no longer exist
    live = {ck(c) for c in coffees}
    cache = {k: v for k, v in cache.items() if k in live}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    log.info("enrichment: %s", stats)
    return stats


def _apply(c: dict, item: dict) -> None:
    changed = False
    if not c.get("country") and item.get("country"):
        canon = detect_country(item["country"]) or item["country"]
        c["country"] = canon
        c["region"] = c.get("region") or region_for(canon)
        changed = True
    if not c.get("subregion") and item.get("region"):
        c["subregion"] = str(item["region"])[:60]
        changed = True
    if not c.get("producer") and item.get("producer"):
        c["producer"] = str(item["producer"])[:80]
        changed = True
    if not c.get("varietals") and item.get("varietals"):
        c["varietals"] = [str(v)[:40] for v in item["varietals"]][:6]
        changed = True
    if not c.get("process") and item.get("process"):
        c["process"] = str(item["process"])[:30]
        changed = True
    if c.get("score") is None and isinstance(item.get("score"), (int, float)) and 60 <= item["score"] <= 100:
        c["score"] = float(item["score"])
        changed = True
    if changed:
        c["enriched"] = True
