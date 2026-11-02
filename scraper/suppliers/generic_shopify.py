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

# Rules for inclusion: ships from the US or Canada, and sells 1 lb (or smaller) sizes. Stores that only sell
# bulk stay here disabled so their parsing is kept, and scrape.py double-checks both rules at run time.
STORES: list[dict] = [
    # key, name, base url, ships_from, all_green (skip keyword filter), enabled
    {"key": "captainscoffee", "name": "The Captain's Coffee", "base": "https://thecaptainscoffee.com", "ships_from": "US",
     "all_green": False, "enabled": True},
    {"key": "thetaridge", "name": "Theta Ridge Coffee", "base": "https://thetaridgecoffee.com", "ships_from": "US",
     "all_green": True, "enabled": True},
    {"key": "prodigal", "name": "Prodigal Coffee", "base": "https://getprodigal.com", "ships_from": "US",
     "all_green": False, "enabled": True},
    {"key": "hacea", "name": "Hacea Coffee Source", "base": "https://haceacoffee.com", "ships_from": "US",
     "all_green": True, "enabled": True, "product_types": ["Coffee"]},
    {"key": "happymug", "name": "Happy Mug", "base": "https://happymugcoffee.com", "ships_from": "US",
     "all_green": True, "enabled": True, "only_default_variant": True, "default_grams": 453.6,
     "product_type_regex": r"^(africa|asia|central america|south america|indonesia|colombia|ethiopia|guatemala|brazil|kenya|"
                           r"costa rica|honduras|el salvador|nicaragua|mexico|peru|panama|burundi|rwanda|tanzania|uganda|"
                           r"sumatra|india|papua|yemen|bolivia|ecuador|decaf.*|green coffee)$"},
    {"key": "greenbeanery", "name": "Green Beanery", "base": "https://greenbeanery.ca", "ships_from": "CA",
     "all_green": True, "enabled": True, "product_types": ["Unroasted Beans"], "currency": "CAD",
     "title_strip": r"^unroasted\s*[-–:]\s*"},
    {"key": "deansbeans", "name": "Dean's Beans", "base": "https://deansbeans.com", "ships_from": "US",
     "all_green": False, "enabled": False, "reason": "organic/fair-trade commodity lots rather than microlots; enable if wanted"},
    {"key": "homeroastcoffee", "name": "Home Roast Coffee", "base": "https://homeroastcoffee.com", "ships_from": "US",
     "all_green": True, "enabled": False, "product_types": ["Coffee Bean"], "reason": "commodity lots; enable if wanted"},
    {"key": "showroom", "name": "Showroom Coffee", "base": "https://showroomcoffee.com", "ships_from": "US",
     "all_green": True, "enabled": False, "reason": "2 lb minimum, no 1 lb size",
     "product_types": ["Coffee"], "title_style": "commas"},
    {"key": "millcity", "name": "Mill City Roasters", "base": "https://millcityroasters.com", "ships_from": "US",
     "all_green": False, "enabled": False, "reason": "10 lb minimum, no 1 lb size", "title_style": "dashes"},
    {"key": "coffeeshrub", "name": "Coffee Shrub", "base": "https://www.coffeeshrub.com", "ships_from": "US",
     "all_green": False, "enabled": False, "reason": "Sweet Maria's wholesale arm, bulk only"},
]

GREEN_RE = re.compile(r"\bgreen\b|unroasted|\braw\s+coffee", re.I)
# Approximate conversion for stores that price in another currency; prices on the page are shown in USD.
CURRENCY_TO_USD = {"USD": 1.0, "CAD": 0.72}
EXCLUDE_RE = re.compile(
    r"roaster(?!y)|grinder|gift\s*card|t-?shirt|mug\b|book|burlap|jute|sack|sampler|sample\s*pack|filter|thermometer|"
    r"scale|kettle|brewer|press|chaff|cooling\s*tray|scoop|storage|container|subscription|membership|cupping\s*(?:kit|spoon|bowl)|"
    r"spoon|apparel|hat\b|sticker|poster|accessor|equipment|part[s]?\b|replacement|class\b|course|workshop|ticket|drum|probe|"
    r"\bwhole\s*bean\b|\bground\b|\broasted\b|cold\s*brew|k-?cup|pods?\b|tea\b|cocoa|cacao|syrup|merch|^evaluation$|consult|shipping|deposit",
    re.I,
)


def _make(store: dict) -> types.ModuleType:
    mod = types.ModuleType(f"gct.generic.{store['key']}")
    mod.KEY = store["key"]
    mod.NAME = store["name"]
    mod.BASE = store["base"]
    mod.SHIPS_FROM = store.get("ships_from", "?")
    all_green = store.get("all_green", False)
    product_types = {t.lower() for t in store.get("product_types", [])}
    type_re = re.compile(store["product_type_regex"], re.I) if store.get("product_type_regex") else None
    title_strip = re.compile(store["title_strip"], re.I) if store.get("title_strip") else None
    currency = store.get("currency", "USD")
    rate = CURRENCY_TO_USD.get(currency, 1.0)

    def is_green(p: dict) -> bool:
        title = p.get("title") or ""
        if EXCLUDE_RE.search(title):
            return False
        pt = p.get("product_type") or ""
        if product_types and pt.lower() not in product_types:
            return False
        if type_re and not type_re.match(pt.strip()):
            return False
        if store.get("only_default_variant"):
            vts = [(v.get("title") or "").strip().lower() for v in p.get("variants", [])]
            if not vts or any(t not in ("default title", "default", "") for t in vts):
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
        if title_strip:
            title = title_strip.sub("", title).strip(" -–|,")
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
            variants=shopify_variants(p, default_grams=store.get("default_grams")),
            currency=currency,
        )
        if rate != 1.0:
            for v in c.variants:
                v.price = round(v.price * rate, 2)
                v.finalize()
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
