#!/usr/bin/env python3
"""Scrape every supplier and write docs/data/coffees.json + docs/data/meta.json.

Usage: python scraper/scrape.py [--only sweetmarias,bodhileaf] [--no-pages] [--no-enrich]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import dump_json, SIZE_LABELS  # noqa: E402
from suppliers import SUPPLIERS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
COFFEES_PATH = DATA_DIR / "coffees.json"
META_PATH = DATA_DIR / "meta.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gct")


RECENTLY_SOLD_OUT_DAYS = 14
ALLOWED_SHIPS_FROM = {"US", "CA"}      # only suppliers shipping from the US or Canada
RETAIL_MAX_LB = 1.15                   # a supplier must sell a size of 1 lb (or 500 g) or less


def supplier_rule_violation(mod, found) -> str | None:
    if getattr(mod, "SHIPS_FROM", "?") not in ALLOWED_SHIPS_FROM:
        return f"ships from {getattr(mod, 'SHIPS_FROM', 'unknown')}, not US/CA"
    if found and not any(v.lb and v.lb <= RETAIL_MAX_LB for c in found for v in c.variants):
        return "no 1 lb offerings (bulk only)"
    return None


def keep_listing(c, prev: dict | None, today: str) -> bool:
    """Only listings that are for sale. A sold-out listing is kept for RECENTLY_SOLD_OUT_DAYS after we
    last saw it buyable (so the page can show "just sold out"); supplier archive pages never appear."""
    if c.in_stock:
        return True
    last = (prev or {}).get("last_in_stock")
    if not last:
        return False
    return (dt.date.fromisoformat(today) - dt.date.fromisoformat(last)).days <= RECENTLY_SOLD_OUT_DAYS


def write_samples(n: int = 6) -> None:
    """Copy a few raw products per store from the raw dumps into scraper/samples/ for offline parser tuning."""
    raw_dir = os.environ.get("GCT_RAW_DIR")
    if not raw_dir or not os.path.isdir(raw_dir):
        return
    out_dir = ROOT / "scraper" / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    for fn in sorted(os.listdir(raw_dir)):
        try:
            with open(os.path.join(raw_dir, fn), encoding="utf-8") as f:
                products = json.load(f).get("products", [])
        except (OSError, ValueError):
            continue
        avail = [p for p in products if any(v.get("available") for v in p.get("variants", []))]
        sample = (avail or products)[:n]
        types = {}
        for p in products:
            types[p.get("product_type") or ""] = types.get(p.get("product_type") or "", 0) + 1
        dump_json(out_dir / fn, {"product_type_counts": types, "products": sample})


def load_previous() -> dict[str, dict]:
    if not COFFEES_PATH.exists():
        return {}
    try:
        with open(COFFEES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {c["id"]: c for c in data.get("coffees", [])}
    except Exception as e:  # noqa: BLE001
        log.warning("could not read previous coffees.json: %s", e)
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma separated supplier keys")
    ap.add_argument("--no-pages", action="store_true", help="skip per-product page fetches (Sweet Maria's scores)")
    ap.add_argument("--no-enrich", action="store_true", help="skip the optional Anthropic enrichment step")
    args = ap.parse_args()

    only = set(args.only.split(",")) if args.only else None
    if args.no_pages:
        from suppliers import sweetmarias
        sweetmarias.FETCH_PAGES = False

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    previous = load_previous()
    today = dt.date.today().isoformat()
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    coffees: list[dict] = []
    suppliers_meta: dict[str, dict] = {}
    for mod in SUPPLIERS:
        if only and mod.KEY not in only:
            continue
        t0 = time.time()
        entry = {"name": mod.NAME, "ships_from": getattr(mod, "SHIPS_FROM", "?"), "count": 0, "in_stock": 0,
                 "with_score": 0, "error": None, "seconds": 0}
        try:
            found = mod.scrape()
            entry["scraped"] = len(found)
            violation = supplier_rule_violation(mod, found)
            if violation:
                log.error("supplier %s excluded: %s", mod.KEY, violation)
                entry["excluded"] = violation
                found = []
            entry["in_stock"] = sum(1 for c in found if c.in_stock)
            entry["with_score"] = sum(1 for c in found if c.score)
            found = [c for c in found if keep_listing(c, previous.get(c.id), today)]
            entry["count"] = len(found)
            for c in found:
                d = c.to_dict()
                prev = previous.get(d["id"])
                d["first_seen"] = (prev or {}).get("first_seen") or today
                d["last_in_stock"] = today if c.in_stock else (prev or {}).get("last_in_stock")
                coffees.append(d)
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{type(e).__name__}: {e}"[:300]
            log.error("supplier %s failed: %s\n%s", mod.KEY, e, traceback.format_exc())
            # keep yesterday's rows for a supplier that failed today so the page doesn't go blank
            kept = [c for c in previous.values() if c.get("supplier_key") == mod.KEY]
            for c in kept:
                c["stale"] = True
            coffees.extend(kept)
            entry["count"] = len(kept)
            entry["stale"] = True
        entry["seconds"] = round(time.time() - t0, 1)
        suppliers_meta[mod.KEY] = entry
        log.info("%s: %s", mod.KEY, entry)

    # carry over suppliers that were not scraped this run (--only)
    if only:
        for c in previous.values():
            if c.get("supplier_key") not in only and c.get("supplier_key"):
                coffees.append(c)

    if not args.no_enrich and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from enrich import enrich_all
            stats = enrich_all(coffees, cache_path=ROOT / "scraper" / "cache" / "enrich.json")
            suppliers_meta["_enrichment"] = stats
        except Exception as e:  # noqa: BLE001
            log.error("enrichment failed: %s\n%s", e, traceback.format_exc())
            suppliers_meta["_enrichment"] = {"error": f"{type(e).__name__}: {e}"[:300]}

    if os.environ.get("GCT_SAMPLES"):
        write_samples()

    coffees.sort(key=lambda c: (c.get("supplier", ""), c.get("title", "")))
    dump_json(COFFEES_PATH, {"generated_at": now, "count": len(coffees), "coffees": coffees})
    meta = {
        "generated_at": now,
        "count": len(coffees),
        "in_stock": sum(1 for c in coffees if c.get("in_stock")),
        "new_today": sum(1 for c in coffees if c.get("first_seen") == today),
        "suppliers": suppliers_meta,
        "size_labels": SIZE_LABELS,
    }
    dump_json(META_PATH, meta)
    log.info("wrote %d coffees to %s", len(coffees), COFFEES_PATH)
    failed = [k for k, v in suppliers_meta.items() if v.get("error") and not k.startswith("_")]
    if failed and len(failed) == len([m for m in SUPPLIERS if not only or m.KEY in only]):
        log.error("every supplier failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
