"""Royal Coffee (royalcoffee.com) - Shopify importer.

About 1,245 full-bag listings plus Crown Jewels (22 lb boxes). Full bags are priced per
pound on the site; when a variant for a very heavy bag carries a price that could not
possibly be the whole-bag price we treat it as $/lb. Crown Jewel boxes are priced per box.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from common import (Coffee, GRAMS_PER_LB, clean_title, detect_country, detect_varietals, fill_from_text, grams_from_text,
                    html_to_text, parse_score, product_image, shopify_products, shopify_variants, extract_specs)

log = logging.getLogger("gct.royalcoffee")

KEY = "royalcoffee"
NAME = "Royal Coffee"
BASE = "https://royalcoffee.com"

EXCLUDE_RE = re.compile(r"gift\s*card|merch|t-?shirt|hat\b|tote|mug|book|sample\s*set|membership|subscription|donation|class|course|ticket", re.I)
_BAG_WEIGHT_RE = re.compile(r"(\d{2,3})\s*(kg|lb)s?\b", re.I)


QUOTE_ONLY_TAGS = {"quoteonly", "offline-only"}
TAG_PROCESS = [("wet-hulled", "Wet-hulled"), ("honey-processed", "Honey"), ("anaerobic", "Anaerobic"),
               ("natural", "Natural"), ("washed", "Washed")]
_PREFIX_RE = re.compile(r"^\s*(crown\s+jewels?|royal\s+gems?)\s+", re.I)
_LOT_RE = re.compile(r"^\d{4,6}(?:-\d+)?$")


def split_title(title: str) -> tuple[str, str | None, str | None]:
    """'BRAZIL FAZENDA DUTRA NATURAL - 40724 - Videplast Bags - SPOT RCWHSE' -> (name, lot, warehouse)."""
    parts = [x.strip() for x in re.split(r"\s+-\s+", title) if x.strip()]
    name = parts[0] if parts else title
    lot = next((x for x in parts[1:] if _LOT_RE.match(x)), None)
    wh = parts[-1] if len(parts) > 1 and len(parts[-1]) <= 30 else None
    return name, lot, wh


GRADE_WORDS = {"AA", "AB", "PB", "EP", "SHB", "SHG", "HB", "HG", "FC", "SS", "G1", "G2", "G3", "G4", "FTO", "RFA", "MC",
               "SWP", "EA", "CO2", "WP", "FW", "DP", "FAQ", "GP", "SL", "UTZ", "GB", "DR", "RA", "KVW", "SO", "MG"}


def nice_name(name: str) -> str:
    """Royal shouts in caps; title-case it but keep grades, lot codes and acronyms."""
    words = []
    for w in name.split():
        core = w.strip("()+,")
        if core.upper() in GRADE_WORDS or re.search(r"\d", w) or "/" in w:
            words.append(w.upper())
        elif w.isupper() or w.islower():
            words.append(w.capitalize())
        else:
            words.append(w)
    return " ".join(words)


def is_green(p: dict) -> bool:
    title = p.get("title") or ""
    if EXCLUDE_RE.search(title):
        return False
    tags = {t.lower() for t in (p.get("tags") or []) if isinstance(t, str)}
    if tags & QUOTE_ONLY_TAGS:
        return False  # not buyable online
    pt = (p.get("product_type") or "").lower()
    if any(w in pt for w in ("merch", "gift", "apparel", "event", "equipment", "roasted", "class", "course", "ticket")):
        return False
    variants = p.get("variants") or []
    if not variants:
        return False
    # Royal keeps every past lot online as a sold-out "Full Size Bags" listing (13k+). Keep only what is buyable.
    if "full size" in pt or "bag" in pt:
        return any(v.get("available") for v in variants)
    return True


def _fix_variants(p: dict, variants):
    """Royal sells full bags priced per lb. If a heavy variant's price is implausibly low for the whole bag, scale it."""
    for v in variants:
        if v.grams and v.grams >= 20000 and v.price < 60:
            lb = v.grams / GRAMS_PER_LB
            v.price_per_lb = round(v.price, 2)
            v.price = round(v.price * lb, 2)
        elif v.grams is None:
            g = grams_from_text(p.get("title", "")) or grams_from_text(v.title)
            if g:
                v.grams = g
                v.finalize()
    return variants


def parse_product(p: dict) -> Coffee | None:
    if not is_green(p):
        return None
    raw_title = clean_title(p.get("title", ""))
    name, lot, wh = split_title(raw_title)
    pt = (p.get("product_type") or "")
    tags_l = [t.lower() for t in (p.get("tags") or []) if isinstance(t, str)]
    crown = "crown" in pt.lower() or "crown jewel" in name.lower() or any("crown" in t for t in tags_l)
    gem = "gem" in pt.lower() or "royal gem" in name.lower() or any("royalgem" in t or t == "royal gems" for t in tags_l)
    title = nice_name(_PREFIX_RE.sub("", name))
    body = html_to_text(p.get("body_html"))
    variants = _fix_variants(p, shopify_variants(p))
    for v in variants:
        bits = [v.title]
        if lot:
            bits.append(f"lot {lot}")
        if wh and wh.lower() != v.title.lower():
            bits.append(wh)
        v.title = " · ".join(bits)
    c = Coffee(
        id=f"{KEY}:{p['id']}",
        supplier=NAME,
        supplier_key=KEY,
        title=title,
        url=f"{BASE}/products/{p.get('handle')}",
        image=product_image(p),
        updated_at=p.get("updated_at"),
        wholesale=True,
        tags=([pt] if pt else []) + [t for t in (p.get("tags") or []) if isinstance(t, str)][:10],
        variants=variants,
    )
    specs = extract_specs(body)
    if specs.get("score"):
        c.score = parse_score(specs["score"])
    if c.score is None:
        m = re.search(r"(?:cup\s*score|cupping\s*score|score)\s*[:\-–]?\s*(8\d(?:\.\d{1,2})?|9\d(?:\.\d{1,2})?)", body, re.I)
        if m:
            c.score = float(m.group(1))
    for tag, proc in TAG_PROCESS:
        if tag in tags_l:
            c.process = proc
            break
    c.decaf = "decaf" in tags_l
    c.country = detect_country(title)
    fill_from_text(c, body_text=body, specs=specs)
    c.varietals = detect_varietals(title) or c.varietals
    c.tags = ([ "Crown Jewel"] if crown else []) + (["Royal Gem"] if gem else []) + \
        [t for t in (p.get("tags") or []) if isinstance(t, str) and t in ("top-tier", "fresh-arrivals", "discounted", "staff-picks", "women-produced", "single-farmer")]
    return c.finalize()


def merge_lots(coffees: list[Coffee]) -> list[Coffee]:
    """Royal lists the same coffee once per lot/warehouse. Fold those into one listing with one variant per lot."""
    merged: dict[str, Coffee] = {}
    for c in coffees:
        key = c.title.lower()
        if key not in merged:
            merged[key] = c
            continue
        m = merged[key]
        m.variants.extend(c.variants)
        m.tags = list(dict.fromkeys(m.tags + c.tags))
        if (c.updated_at or "") > (m.updated_at or ""):
            m.updated_at = c.updated_at
        for f in ("producer", "subregion", "process", "altitude", "notes", "score"):
            if getattr(m, f) in (None, "", []) and getattr(c, f) not in (None, "", []):
                setattr(m, f, getattr(c, f))
    out = []
    for m in merged.values():
        m.variants.sort(key=lambda v: (not v.available, v.price_per_lb or 1e9))
        out.append(m.finalize())
    return out


def scrape() -> list[Coffee]:
    products = shopify_products(BASE, max_pages=60)
    types = Counter((p.get("product_type") or "<none>") for p in products)
    log.info("royalcoffee: %d products; product_type counts: %s", len(products), dict(types.most_common(15)))
    coffees = [c for c in (parse_product(p) for p in products) if c]
    merged = merge_lots(coffees)
    log.info("royalcoffee: %d buyable lots -> %d coffees, %d with score", len(coffees), len(merged), sum(1 for c in merged if c.score))
    return merged
