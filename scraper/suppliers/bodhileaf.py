"""Bodhi Leaf Coffee Traders (bodhileafcoffee.com) - Shopify.

Green coffee titles contain "(Green Coffee)". Origin, varietal, process, altitude and
tasting notes are in body_html prose. No scores. Variants come in 1/5/20 lb.
"""
from __future__ import annotations

import logging
import re

from common import Coffee, clean_title, fill_from_text, html_to_text, product_image, shopify_products, shopify_variants

log = logging.getLogger("gct.bodhileaf")

KEY = "bodhileaf"
NAME = "Bodhi Leaf"
SHIPS_FROM = "US"
BASE = "https://www.bodhileafcoffee.com"
_GREEN_RE = re.compile(r"\(\s*green\s+coffee\s*\)|\bgreen\s+coffee\b|\bunroasted\b", re.I)


def is_green(p: dict) -> bool:
    title = p.get("title") or ""
    if _GREEN_RE.search(title):
        return True
    pt = (p.get("product_type") or "").lower()
    tags = " ".join(t for t in (p.get("tags") or []) if isinstance(t, str)).lower()
    return ("green" in pt and "coffee" in pt) or "green coffee" in tags or "unroasted" in tags


def parse_product(p: dict) -> Coffee | None:
    if not is_green(p):
        return None
    title = clean_title(p.get("title", ""))
    title = re.sub(r"\s*\(\s*green\s+coffee\s*\)\s*", " ", title, flags=re.I).strip(" -–")
    body = html_to_text(p.get("body_html"))
    c = Coffee(
        id=f"{KEY}:{p['id']}",
        supplier=NAME,
        supplier_key=KEY,
        title=title,
        url=f"{BASE}/products/{p.get('handle')}",
        image=product_image(p),
        updated_at=p.get("updated_at"),
        tags=[t for t in (p.get("tags") or []) if isinstance(t, str)][:12],
        variants=shopify_variants(p),
    )
    fill_from_text(c, body_text=body)
    return c.finalize()


def scrape() -> list[Coffee]:
    products = shopify_products(BASE)
    coffees = [c for c in (parse_product(p) for p in products) if c]
    log.info("bodhileaf: %d products, %d green coffees", len(products), len(coffees))
    return coffees
