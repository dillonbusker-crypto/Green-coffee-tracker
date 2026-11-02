"""Royal Coffee (royalcoffee.com) - Shopify importer.

About 1,245 full-bag listings plus Crown Jewels (22 lb boxes). Full bags are priced per
pound on the site; when a variant for a very heavy bag carries a price that could not
possibly be the whole-bag price we treat it as $/lb. Crown Jewel boxes are priced per box.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from common import (Coffee, GRAMS_PER_LB, clean_title, fill_from_text, grams_from_text, html_to_text, parse_score,
                    product_image, shopify_products, shopify_variants, extract_specs)

log = logging.getLogger("gct.royalcoffee")

KEY = "royalcoffee"
NAME = "Royal Coffee"
BASE = "https://royalcoffee.com"

EXCLUDE_RE = re.compile(r"gift\s*card|merch|t-?shirt|hat\b|tote|mug|book|sample\s*set|membership|subscription|donation|class|course|ticket", re.I)
_BAG_WEIGHT_RE = re.compile(r"(\d{2,3})\s*(kg|lb)s?\b", re.I)


def is_green(p: dict) -> bool:
    title = p.get("title") or ""
    if EXCLUDE_RE.search(title):
        return False
    pt = (p.get("product_type") or "").lower()
    if any(w in pt for w in ("merch", "gift", "apparel", "event", "equipment", "roasted")):
        return False
    return bool(p.get("variants"))


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
    title = clean_title(p.get("title", ""))
    body = html_to_text(p.get("body_html"))
    pt = (p.get("product_type") or "")
    crown = "crown" in pt.lower() or "crown jewel" in title.lower() or any(
        "crown" in t.lower() for t in (p.get("tags") or []) if isinstance(t, str))
    variants = _fix_variants(p, shopify_variants(p))
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
    fill_from_text(c, body_text=body, specs=specs)
    if crown:
        c.tags = ["Crown Jewel"] + [t for t in c.tags if t != "Crown Jewel"]
    return c.finalize()


def scrape() -> list[Coffee]:
    products = shopify_products(BASE, max_pages=60)
    types = Counter((p.get("product_type") or "<none>") for p in products)
    log.info("royalcoffee: %d products; product_type counts: %s", len(products), dict(types.most_common(15)))
    coffees = [c for c in (parse_product(p) for p in products) if c]
    log.info("royalcoffee: %d coffees, %d with score", len(coffees), sum(1 for c in coffees if c.score))
    return coffees
