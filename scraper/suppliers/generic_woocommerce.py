"""Config-driven adapter for WooCommerce stores via the public Store API
(/wp-json/wc/store/v1/products). Prices come back in minor units (cents).

Burman Coffee Traders sells green coffee by the pound: each product is a simple product priced
per 1 lb, with attributes (Origin, Processing Method, Altitude, Arrival Date, Lot #) and
categories that carry the process and the country.
"""
from __future__ import annotations

import html as htmllib
import logging
import re
import types
from collections import Counter

from common import (Coffee, Variant, clean_title, detect_country, detect_process, extract_specs, fill_from_text,
                    get, html_to_text)

log = logging.getLogger("gct.woo")

STORES: list[dict] = [
    {"key": "burman", "name": "Burman Coffee Traders", "base": "https://burmancoffee.com", "ships_from": "US",
     "enabled": True, "include_categories": ["Green Coffee Beans"], "exclude_categories": ["Product Archive"],
     "unit_lb": 1.0},
    {"key": "smokinbeans", "name": "Smokin' Beans", "base": "https://smokinbeans.com", "ships_from": "US",
     "enabled": False, "reason": "2 lb minimum, no 1 lb size", "include_categories": ["Greens: Single Origin"]},
]

PROCESS_CATEGORIES = [("Semi-Washed (Honey)", "Honey"), ("Honey", "Honey"), ("Natural", "Natural"), ("Washed", "Washed"),
                      ("Wet Hulled", "Wet-hulled"), ("Anaerobic", "Anaerobic")]


def woo_products(base: str, *, per_page: int = 100, max_pages: int = 40) -> list[dict]:
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        r = get(f"{base.rstrip('/')}/wp-json/wc/store/v1/products?per_page={per_page}&page={page}")
        items = r.json()
        if not items:
            break
        out.extend(items)
        total_pages = r.headers.get("X-WP-TotalPages")
        if total_pages and page >= int(total_pages):
            break
        if len(items) < per_page:
            break
    return out


def _make(store: dict) -> types.ModuleType:
    mod = types.ModuleType(f"gct.woo.{store['key']}")
    mod.KEY = store["key"]
    mod.NAME = store["name"]
    mod.BASE = store["base"]
    mod.SHIPS_FROM = store.get("ships_from", "?")
    mod.ENABLED = store.get("enabled", True)
    include = set(store.get("include_categories", []))
    exclude = set(store.get("exclude_categories", []))
    unit_lb = store.get("unit_lb")

    def parse_product(it: dict) -> Coffee | None:
        cats = [c.get("name") for c in it.get("categories", []) if c.get("name")]
        if include and not (set(cats) & include):
            return None
        if set(cats) & exclude:
            return None
        prices = it.get("prices") or {}
        try:
            price = float(prices.get("price") or 0) / (10 ** int(prices.get("currency_minor_unit", 2)))
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            return None
        title = clean_title(htmllib.unescape(it.get("name") or ""))
        body = html_to_text((it.get("description") or "") + "\n" + (it.get("short_description") or ""))
        attrs: dict[str, str] = {}
        for a in it.get("attributes", []) or []:
            name = (a.get("name") or "").strip().rstrip(":").lower()
            vals = ", ".join(t.get("name") for t in a.get("terms", []) if t.get("name"))
            if name and vals:
                attrs[name] = vals
        specs = extract_specs(body)
        for k_attr, k_spec in (("origin", "region"), ("processing method", "process"), ("altitude", "altitude"),
                               ("varietal", "varietal"), ("variety", "varietal"), ("producer", "producer"), ("farm", "producer"),
                               ("cupping score", "score"), ("score", "score")):
            if attrs.get(k_attr) and not specs.get(k_spec):
                specs[k_spec] = attrs[k_attr]
        available = bool(it.get("is_in_stock", True))
        variants = []
        if unit_lb:
            variants.append(Variant(title=f"{unit_lb:g} lb", price=price, grams=unit_lb * 453.592, available=available,
                                    variant_id=str(it.get("id"))).finalize())
        else:
            variants.append(Variant(title="Each", price=price, grams=None, available=available, variant_id=str(it.get("id"))).finalize())
        c = Coffee(
            id=f"{mod.KEY}:{it.get('id')}",
            supplier=mod.NAME,
            supplier_key=mod.KEY,
            title=title,
            url=it.get("permalink") or mod.BASE,
            image=(it.get("images") or [{}])[0].get("thumbnail") or (it.get("images") or [{}])[0].get("src"),
            tags=[c for c in cats if c not in include][:10],
            variants=variants,
        )
        # Burman titles: "Nicaraguan Jinotega SHG EP – Finca El Pastoral – Washed Processed"
        parts = [x.strip() for x in re.split(r"\s+[–—-]\s+", title) if x.strip()]
        if len(parts) >= 2:
            for part in parts[1:]:
                if re.search(r"finca|farm|hacienda|estate|coop|cooperative|mill|station|familia|family|producer", part, re.I) and not c.producer:
                    c.producer = part
        for cat, proc in PROCESS_CATEGORIES:
            if cat in cats:
                c.process = proc
                break
        if not c.process:
            c.process = detect_process(parts[-1] if len(parts) > 1 else "", title)
        c.country = detect_country(title, " ".join(cats))
        fill_from_text(c, body_text=body, specs=specs)
        if attrs.get("arrival date"):
            c.tags = [f"Arrived {attrs['arrival date']}"] + c.tags
        return c.finalize()

    def scrape() -> list[Coffee]:
        items = woo_products(mod.BASE)
        cats = Counter(c.get("name") for it in items for c in it.get("categories", []))
        log.info("%s: %d products; top categories: %s", mod.KEY, len(items), dict(cats.most_common(10)))
        coffees = [c for c in (parse_product(it) for it in items) if c]
        log.info("%s: %d green coffees (%d in stock)", mod.KEY, len(coffees), sum(1 for c in coffees if c.in_stock))
        return coffees

    mod.parse_product = parse_product
    mod.scrape = scrape
    return mod


def modules() -> list[types.ModuleType]:
    return [_make(s) for s in STORES if s.get("enabled", True)]
