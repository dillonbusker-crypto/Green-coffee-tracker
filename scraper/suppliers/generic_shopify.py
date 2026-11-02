"""Config-driven adapter for additional Shopify green coffee stores.

Add a store to STORES and it will be scraped on the next run. Each store gets a strict
"green / unroasted" keyword filter unless `all_green` is set, so an untested store cannot
flood the tracker with roasted coffee or gear. Failures are isolated per store and recorded
in meta.json.
"""
from __future__ import annotations

import logging
import re
import types

from collections import Counter

from common import (Coffee, clean_title, detect_country, detect_process, detect_varietals, fill_from_text, html_to_text,
                    product_image, shopify_products, shopify_variants)

log = logging.getLogger("gct.generic")

STORES: list[dict] = [
    # key, name, base url, all_green (skip keyword filter), enabled
    {"key": "showroom", "name": "Showroom Coffee", "base": "https://showroomcoffee.com", "all_green": True, "enabled": True,
     "product_types": ["Coffee"], "title_style": "commas"},
    {"key": "captainscoffee", "name": "The Captain's Coffee", "base": "https://thecaptainscoffee.com", "all_green": False, "enabled": True},
    {"key": "thetaridge", "name": "Theta Ridge Coffee", "base": "https://thetaridgecoffee.com", "all_green": True, "enabled": True},
    {"key": "millcity", "name": "Mill City Roasters", "base": "https://millcityroasters.com", "all_green": False, "enabled": True,
     "title_style": "dashes"},
    {"key": "coffeeshrub", "name": "Coffee Shrub", "base": "https://www.coffeeshrub.com", "all_green": False, "enabled": False},
]

GREEN_RE = re.compile(r"\bgreen\b|unroasted|\braw\s+coffee", re.I)
EXCLUDE_RE = re.compile(
    r"roaster(?!y)|grinder|gift\s*card|t-?shirt|mug\b|book|burlap|jute|sack|sampler|sample\s*pack|filter|thermometer|"
    r"scale|kettle|brewer|press|chaff|cooling\s*tray|scoop|storage|container|subscription|membership|cupping\s*(?:kit|spoon|bowl)|"
    r"spoon|apparel|hat\b|sticker|poster|accessor|equipment|part[s]?\b|replacement|class\b|course|workshop|ticket|drum|probe|"
    r"\bwhole\s*bean\b|\bground\b|\broasted\b|cold\s*brew|k-?cup|pods?\b|tea\b|cocoa|cacao|syrup|merch",
    re.I,
)


def _make(store: dict) -> types.ModuleType:
    mod = types.ModuleType(f"gct.generic.{store['key']}")
    mod.KEY = store["key"]
    mod.NAME = store["name"]
    mod.BASE = store["base"]
    all_green = store.get("all_green", False)
    product_types = {t.lower() for t in store.get("product_types", [])}

    def is_green(p: dict) -> bool:
        title = p.get("title") or ""
        if EXCLUDE_RE.search(title):
            return False
        pt = p.get("product_type") or ""
        if product_types and pt.lower() not in product_types:
            return False
        tags = " ".join(t for t in (p.get("tags") or []) if isinstance(t, str))
        if EXCLUDE_RE.search(pt) and not GREEN_RE.search(pt):
            return False
        if all_green:
            return True
        return bool(GREEN_RE.search(title) or GREEN_RE.search(pt) or GREEN_RE.search(tags))

    def parse_product(p: dict) -> Coffee | None:
        if not is_green(p):
            return None
        title = clean_title(p.get("title", ""))
        title = re.sub(r"\s*[\(\[]?\s*(?:green|unroasted)\s+coffee\s*(?:beans?)?\s*[\)\]]?\s*", " ", title, flags=re.I).strip(" -–|,")
        body = html_to_text(p.get("body_html"))
        c = Coffee(
            id=f"{mod.KEY}:{p['id']}",
            supplier=mod.NAME,
            supplier_key=mod.KEY,
            title=title,
            url=f"{mod.BASE}/products/{p.get('handle')}",
            image=product_image(p),
            updated_at=p.get("updated_at"),
            tags=[t for t in (p.get("tags") or []) if isinstance(t, str)][:12],
            variants=shopify_variants(p),
        )
        if store.get("title_style") == "dashes":
            # "Colombia - Arnulfo Leguizamo - Finca La Primavera - Sidra - Washed" / "Brazil - Serra Negra, Natural"
            parts = [x.strip() for x in re.split(r"\s+-\s+|,\s+", title) if x.strip()]
            if len(parts) >= 2:
                c.country = detect_country(parts[0])
                rest = parts[1:]
                proc_or_var = [x for x in rest if detect_process(x) or detect_varietals(x)]
                descriptive = [x for x in rest if x not in proc_or_var]
                if descriptive:
                    if re.search(r"finca|farm|hacienda|estate|mill|station|coop|cooperative|producers|women", " ".join(descriptive), re.I) or len(descriptive) >= 2:
                        c.producer = " / ".join(descriptive[:2])
                    else:
                        c.subregion = descriptive[0]
        elif store.get("title_style") == "commas":
            # Showroom: "Yohanis Dogoma, Worka Chelbesa, Natural, G1 Lot 2" -> producer, region, process, grade
            parts = [x.strip() for x in title.split(",") if x.strip()]
            if len(parts) >= 3:
                rest = [x for x in parts if not (detect_process(x) or re.fullmatch(r"(G\d|grade\s*\d|lot\s*\d+|\(?\d+\s*kg\)?|[A-Z]{2,3}\d*)(\s+lot\s*\d+)?", x, re.I))]
                if rest and not detect_country(rest[0]):
                    c.producer = rest[0]
                if len(rest) >= 2:
                    c.subregion = rest[1]
        fill_from_text(c, body_text=body)
        return c.finalize()

    def scrape() -> list[Coffee]:
        products = shopify_products(mod.BASE)
        types = Counter((p.get("product_type") or "<none>") for p in products)
        log.info("%s: product_type counts: %s", mod.KEY, dict(types.most_common(12)))
        coffees = [c for c in (parse_product(p) for p in products) if c]
        log.info("%s: %d products, %d green coffees", mod.KEY, len(products), len(coffees))
        return coffees

    mod.is_green = is_green
    mod.parse_product = parse_product
    mod.scrape = scrape
    return mod


def modules() -> list[types.ModuleType]:
    return [_make(s) for s in STORES if s.get("enabled", True)]
