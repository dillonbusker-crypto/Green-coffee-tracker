"""Sweet Maria's (sweetmarias.com) - Shopify.

Green coffee has product_type == "Green Coffee". Country comes from tags like
"Category_Sweet Marias/Green Coffee/Africa/Kenya". Tags with "Category_Coffee Shrub/..."
mark lots also sold through Coffee Shrub (wholesale). Score / varietal / process /
arrival are not in body_html, so we fetch each product page and look for them there.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import (Coffee, clean_title, detect_country, extract_specs, fill_from_text, get, html_to_text,
                    parse_score, product_image, shopify_products, shopify_variants, region_for)

log = logging.getLogger("gct.sweetmarias")

KEY = "sweetmarias"
NAME = "Sweet Maria's"
BASE = "https://www.sweetmarias.com"
FETCH_PAGES = True  # set False in tests

_CAT_RE = re.compile(r"^Category_Sweet Marias/Green Coffee/([^/]+)(?:/([^/]+))?", re.I)
_SHRUB_RE = re.compile(r"^Category_Coffee Shrub", re.I)
_REGION_MAP = {
    "africa": "Africa", "central america": "Central America", "south america": "South America",
    "indonesia": "Asia-Pacific", "asia": "Asia-Pacific", "asia/pacific": "Asia-Pacific", "pacific": "Asia-Pacific",
    "arabia": "Arabia", "yemen": "Arabia", "caribbean": "Caribbean", "islands": "Asia-Pacific",
}

# Patterns tried against the product page (text first, then raw html)
_SCORE_TEXT_RES = [
    re.compile(r"(?:cupping\s+)?score\s*[:\-–]?\s*(8\d(?:\.\d{1,2})?|9\d(?:\.\d{1,2})?)(?![\d.])", re.I),
    re.compile(r"(8\d(?:\.\d{1,2})?|9\d(?:\.\d{1,2})?)\s*(?:pts?|points)(?![a-z])", re.I),
]
_SCORE_HTML_RES = [
    re.compile(r'"(?:cupping_score|cup_score|score|cuppingscore|sm_score)"\s*:\s*"?(8\d(?:\.\d{1,2})?|9\d(?:\.\d{1,2})?)"?', re.I),
    re.compile(r'(?:score|rating)[^<>]{0,40}</[^>]+>\s*(?:<[^>]+>\s*)*(8\d(?:\.\d{1,2})?|9\d(?:\.\d{1,2})?)(?![\d.])', re.I),
    re.compile(r'(?:class|data-\w+)="[^"]*score[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*(8\d(?:\.\d{1,2})?|9\d(?:\.\d{1,2})?)(?![\d.])', re.I),
]


def parse_product_page(html: str) -> dict:
    """Pull score / varietal / process / arrival / region from a product page. Best effort."""
    out: dict = {}
    text = html_to_text(html)
    for pat in _SCORE_TEXT_RES:
        m = pat.search(text)
        if m:
            out["score"] = float(m.group(1))
            break
    if "score" not in out:
        for pat in _SCORE_HTML_RES:
            m = pat.search(html)
            if m:
                out["score"] = float(m.group(1))
                break
    specs = extract_specs(text)
    for k in ("varietal", "process", "region", "arrival", "altitude", "producer", "grade", "notes"):
        if specs.get(k):
            out[k] = specs[k]
    # Sweet Maria's often renders attributes as "Process Wet Process" (label and value on neighbouring lines)
    lines = text.splitlines()
    for i, ln in enumerate(lines[:-1]):
        key = ln.strip().lower().rstrip(":")
        if key in ("process", "processing", "processing method") and "process" not in out and len(lines[i + 1]) < 60:
            out["process"] = lines[i + 1].strip()
        elif key in ("varietal", "variety", "cultivar") and "varietal" not in out and len(lines[i + 1]) < 80:
            out["varietal"] = lines[i + 1].strip()
        elif key in ("producer", "farm", "producer name") and "producer" not in out and len(lines[i + 1]) < 80:
            out["producer"] = lines[i + 1].strip()
        elif key in ("region",) and "region" not in out and len(lines[i + 1]) < 60:
            out["region"] = lines[i + 1].strip()
        elif key in ("arrival", "arrival date") and "arrival" not in out and len(lines[i + 1]) < 40:
            out["arrival"] = lines[i + 1].strip()
        elif key in ("score", "cupping score") and "score" not in out:
            s = parse_score(lines[i + 1])
            if s:
                out["score"] = s
    return out


def parse_product(p: dict, page_html: str | None = None, page_attrs: dict | None = None) -> Coffee | None:
    if (p.get("product_type") or "").strip().lower() != "green coffee":
        return None
    tags = [t for t in (p.get("tags") or []) if isinstance(t, str)]
    region = country = None
    wholesale = False
    for t in tags:
        m = _CAT_RE.match(t)
        if m:
            region = region or _REGION_MAP.get(m.group(1).strip().lower(), m.group(1).strip())
            if m.group(2):
                country = country or m.group(2).strip()
        if _SHRUB_RE.match(t):
            wholesale = True
    title = clean_title(p.get("title", ""))
    body = html_to_text(p.get("body_html"))
    if country:
        country = detect_country(country) or country
    c = Coffee(
        id=f"{KEY}:{p['id']}",
        supplier=NAME,
        supplier_key=KEY,
        title=title,
        url=f"{BASE}/products/{p.get('handle')}",
        image=product_image(p),
        country=country,
        region=region if region in ("Africa", "Central America", "South America", "Asia-Pacific", "Arabia", "Caribbean") else None,
        updated_at=p.get("updated_at"),
        wholesale=wholesale,
        tags=[t for t in tags if not t.lower().startswith("category_")][:12],
        variants=shopify_variants(p),
    )
    page = page_attrs if page_attrs is not None else (parse_product_page(page_html) if page_html else {})
    if page.get("score"):
        c.score = page["score"]
    specs = extract_specs(body)
    for k in ("varietal", "process", "region", "producer", "altitude", "notes"):
        if page.get(k) and not specs.get(k):
            specs[k] = page[k]
    fill_from_text(c, body_text=body, specs=specs)
    if not c.region:
        c.region = region_for(c.country)
    return c.finalize()


PAGE_CACHE = Path(__file__).resolve().parent.parent / "cache" / "sweetmarias_pages.json"
PARSER_VERSION = "v2"  # bump when parse_product_page changes so cached attributes are refreshed


def cache_key(p: dict) -> str:
    return f"{p['id']}|{p.get('updated_at') or ''}|{PARSER_VERSION}"
WORKERS = 4
MAX_CONSECUTIVE_FAILURES = 3


def _load_cache() -> dict:
    try:
        return json.loads(PAGE_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        PAGE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PAGE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    except OSError as e:
        log.warning("could not write page cache: %s", e)


def fetch_page_attrs(greens: list[dict]) -> dict[str, dict]:
    """Return {product id: attrs} using the cache for unchanged products and fetching the rest in parallel."""
    cache = _load_cache()
    attrs: dict[str, dict] = {}
    todo: list[dict] = []
    for p in greens:
        key = cache_key(p)
        hit = cache.get(key)
        if hit is not None:
            attrs[str(p["id"])] = hit
        else:
            todo.append(p)
    log.info("sweetmarias: %d product pages cached, %d to fetch", len(attrs), len(todo))
    if todo and FETCH_PAGES:
        failures = 0
        stop = False

        def work(p: dict):
            if stop:
                return p, None
            try:
                html = get(f"{BASE}/products/{p.get('handle')}", sleep=0).text
                return p, parse_product_page(html)
            except Exception as e:  # noqa: BLE001
                log.warning("sweetmarias page fetch failed for %s: %s", p.get("handle"), e)
                return p, None

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for p, got in ex.map(work, todo):
                if got is None:
                    failures += 1
                    if failures >= MAX_CONSECUTIVE_FAILURES and not stop:
                        stop = True
                        log.error("sweetmarias: %d page fetches failed in a row, moving on without the rest", failures)
                    continue
                failures = 0
                attrs[str(p["id"])] = got
                cache[cache_key(p)] = got
    live = {cache_key(p) for p in greens}
    _save_cache({k: v for k, v in cache.items() if k in live})
    return attrs


def scrape() -> list[Coffee]:
    products = shopify_products(BASE)
    greens = [p for p in products if (p.get("product_type") or "").strip().lower() == "green coffee"]
    log.info("sweetmarias: %d products, %d green", len(products), len(greens))
    attrs = fetch_page_attrs(greens)
    coffees: list[Coffee] = []
    for p in greens:
        c = parse_product(p, page_attrs=attrs.get(str(p["id"])))
        if c:
            coffees.append(c)
    scored = sum(1 for c in coffees if c.score)
    log.info("sweetmarias: %d coffees, %d with score", len(coffees), scored)
    return coffees
