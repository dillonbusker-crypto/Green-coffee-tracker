"""Coffee Bean Corral (coffeebeancorral.com) - Shopify (untested in the handoff).

Nearly everything they sell is green coffee, so we include products unless they look
like equipment, merchandise or roasted coffee. product_type counts are logged so the
filter can be tightened once real data has been seen.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from common import Coffee, clean_title, detect_country, fill_from_text, html_to_text, product_image, shopify_products, shopify_variants

log = logging.getLogger("gct.coffeebeancorral")

KEY = "coffeebeancorral"
NAME = "Coffee Bean Corral"
BASE = "https://www.coffeebeancorral.com"

EXCLUDE_RE = re.compile(
    r"roaster|grinder|gift\s*card|t-?shirt|mug|book|burlap|jute|sack|sampler|sample\s*pack|filter|thermometer|"
    r"scale|kettle|brewer|press|chaff|cooling|scoop|storage|container|bag[s]?\s*\(|roasted|whole\s*bean|ground|"
    r"subscription|membership|cupping|spoon|apparel|hat\b|sticker|poster|accessor|equipment|part[s]?\b|replacement",
    re.I,
)
ROASTED_TYPE_RE = re.compile(r"roasted|equipment|merch|supplies|accessor|gear|hardware", re.I)
GREEN_RE = re.compile(r"green|unroasted|raw", re.I)


NON_COFFEE_TYPE_RE = re.compile(r"set|digital|roaster|brewer|accessor|merch|equipment|gift|sample", re.I)


def is_green(p: dict) -> bool:
    """Real product types seen: 'Bulk Green - Conventional', 'Bulk Green - Organic', 'Bulk Green Coffee',
    'Sets', 'Coffee Sets', 'Digital', 'Accessories', 'Roaster', 'Brewer', 'Merch'."""
    title = p.get("title") or ""
    pt = p.get("product_type") or ""
    if NON_COFFEE_TYPE_RE.search(pt) and not GREEN_RE.search(pt):
        return False
    if EXCLUDE_RE.search(title):
        return False
    if GREEN_RE.search(pt):
        return True
    tags = " ".join(t for t in (p.get("tags") or []) if isinstance(t, str))
    if GREEN_RE.search(title) or re.search(r"green\s*coffee|unroasted", tags, re.I):
        return True
    if ROASTED_TYPE_RE.search(pt):
        return False
    # No product_type at all: assume coffee if it has a weight-based variant
    variants = p.get("variants") or []
    return not pt and any((v.get("grams") or 0) >= 400 for v in variants)


def parse_product(p: dict) -> Coffee | None:
    if not is_green(p):
        return None
    title = clean_title(p.get("title", ""))
    title = re.sub(r"\s*\(?\s*green\s+coffee\s*(?:beans?)?\s*\)?\s*", " ", title, flags=re.I).strip(" -–")
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
    m = re.match(r"^([A-Za-z .'\-]+):\s*(.+?)(?:,\s*([^,]+))?$", title)
    if m:
        head = m.group(1).strip()
        if head.lower().startswith("cbc blend"):
            c.country = None
        else:
            c.country = detect_country(head)
            if m.group(3):
                c.subregion = m.group(3).strip()
    fill_from_text(c, body_text=body)
    if m and m.group(1).strip().lower().startswith("cbc blend"):
        c.country = None
        c.region = None
    return c.finalize()


def scrape() -> list[Coffee]:
    products = shopify_products(BASE)
    types = Counter((p.get("product_type") or "<none>") for p in products)
    log.info("coffeebeancorral: %d products; product_type counts: %s", len(products), dict(types.most_common(15)))
    coffees = [c for c in (parse_product(p) for p in products) if c]
    log.info("coffeebeancorral: %d green coffees", len(coffees))
    return coffees
