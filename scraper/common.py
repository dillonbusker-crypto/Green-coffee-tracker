"""Shared helpers for the green coffee scrapers."""
from __future__ import annotations

import html as htmllib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Iterable, Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("gct")

USER_AGENT = (
    "Mozilla/5.0 (compatible; GreenCoffeeTracker/1.0; "
    "+https://github.com/dillonbusker-crypto/Green-coffee-tracker)"
)
GRAMS_PER_LB = 453.592

_session: Optional[requests.Session] = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"})
        _session = s
    return _session


def get(url: str, *, retries: int = 2, timeout: int = 20, sleep: float = 0.2) -> requests.Response:
    """GET with at most `retries` retries. A 4xx (blocked / missing) is never retried: we move on."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = session().get(url, timeout=timeout)
            if 400 <= r.status_code < 500:
                raise FetchBlocked(f"{r.status_code} for {url}")
            if r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} for {url}", response=r)
            if sleep:
                time.sleep(sleep)
            return r
        except FetchBlocked:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    assert last is not None
    raise last


RAW_DIR = os.environ.get("GCT_RAW_DIR")


def _dump_raw(base: str, products: list[dict]) -> None:
    if not RAW_DIR:
        return
    try:
        os.makedirs(RAW_DIR, exist_ok=True)
        host = re.sub(r"^https?://(www\.)?", "", base).strip("/").replace("/", "_")
        with open(os.path.join(RAW_DIR, f"{host}.json"), "w", encoding="utf-8") as f:
            json.dump({"products": products}, f, ensure_ascii=False)
    except OSError as e:
        log.warning("raw dump failed for %s: %s", base, e)


class FetchBlocked(Exception):
    """A 4xx response: the host refused us or the page does not exist."""


def shopify_products(base: str, *, max_pages: int = 40) -> list[dict]:
    """Fetch the full public catalog of a Shopify store via /products.json."""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        r = get(f"{base.rstrip('/')}/products.json?limit=250&page={page}")
        data = r.json()
        products = data.get("products", [])
        if not products:
            break
        out.extend(products)
        if len(products) < 250:
            break
    _dump_raw(base, out)
    return out


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

def html_to_text(html: str | None) -> str:
    """Strip HTML, keeping line breaks for block elements so 'Label: value' lines survive."""
    if not html:
        return ""
    if "&lt;" in html and html.count("&lt;") > html.count("<"):
        html = htmllib.unescape(html)  # some stores (Royal) store the description HTML-escaped
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "dt", "dd"]):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = soup.get_text(separator=" ")
    text = htmllib.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", title or "").strip()
    return t


def truncate(text: str, n: int = 420) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= n:
        return text
    cut = text[: n - 1]
    return cut[: cut.rfind(" ")].rstrip(",;:") + "…"


# --------------------------------------------------------------------------
# Spec extraction ("Label: value" lines in descriptions)
# --------------------------------------------------------------------------

SPEC_LABELS = {
    "producer": ["producer", "producers", "farm", "farm name", "grower", "growers", "cooperative", "co-op", "coop",
                 "estate", "farmer", "farmers", "washing station", "mill", "wet mill", "station", "producer / farm", "farm / producer"],
    "region": ["region", "growing region", "area", "sub-region", "subregion", "origin region", "zone", "department", "province", "district", "municipality", "town"],
    "country": ["country", "origin", "country of origin"],
    "varietal": ["varietal", "varietals", "variety", "varieties", "cultivar", "cultivars", "botanical variety"],
    "process": ["process", "processing", "processing method", "process method", "method", "preparation", "prep"],
    "altitude": ["altitude", "elevation", "growing altitude", "elevation (masl)", "masl"],
    "score": ["score", "cupping score", "cup score", "sca score", "q score", "q-grade score", "q grade", "cup rating", "rating"],
    "notes": ["tasting notes", "cupping notes", "cup notes", "flavor notes", "flavour notes", "notes", "flavor", "flavour", "flavor profile", "cup profile", "cup", "in the cup", "taste"],
    "harvest": ["harvest", "crop", "crop year", "harvest period", "harvest year"],
    "arrival": ["arrival", "arrived", "arrival date", "landed", "eta"],
    "grade": ["grade", "screen size", "screen"],
}
_LABEL_TO_KEY = {lbl: key for key, labels in SPEC_LABELS.items() for lbl in labels}
_SPEC_RE = re.compile(r"^\s*(?:[•\-\*·]\s*)?([A-Za-z][A-Za-z /\-\(\)]{1,32}?)\s*[:：\-–]\s+(.{1,220})$")
# "Label value" with no separator, as rendered by attribute tables (Sweet Maria's). Only unambiguous labels.
_BARE_LABELS = ["cupping score", "cup score", "score", "processing method", "process method", "processing", "process",
                "varietals", "varietal", "variety", "varieties", "cultivar", "region", "arrival date", "arrival", "producer",
                "farm", "farm gate", "altitude", "elevation", "grade", "harvest"]
_LABEL_TO_KEY.update({"processing method": "process", "process method": "process", "farm gate": "_ignore"})
_BARE_SPEC_RE = re.compile(r"^\s*(" + "|".join(re.escape(l) for l in sorted(_BARE_LABELS, key=len, reverse=True))
                           + r")\s+(?!:)(\S.{0,78})$", re.I)
# "Producer Notes", "Arrival date", "Tasting notes" are headings, not values
_HEADING_TAILS = {"notes", "note", "date", "info", "information", "details", "profile", "method", "type", "name", "gate"}


_ALL_LABELS_RE = re.compile(
    r"(?<=[.;|•\-–]\s)(?=(?:" + "|".join(re.escape(l) for l in sorted(_LABEL_TO_KEY, key=len, reverse=True)) + r")\s*[:：])",
    re.I,
)


def _split_inline_specs(line: str) -> list[str]:
    """'Farm: X. Variety: Y. Process: Z.' -> ['Farm: X.', 'Variety: Y.', 'Process: Z.']"""
    if line.count(":") < 2:
        return [line]
    parts = _ALL_LABELS_RE.split(line)
    return [p.strip() for p in parts if p.strip()]


def extract_specs(text: str) -> dict[str, str]:
    """Find 'Label: value' lines. First match per key wins."""
    out: dict[str, str] = {}
    bare: dict[str, str] = {}
    lines = [piece for raw in text.splitlines() for piece in _split_inline_specs(raw)]
    for line in lines:
        m = _SPEC_RE.match(line)
        if m:
            label = m.group(1).strip().lower().rstrip(":")
            key = _LABEL_TO_KEY.get(label)
            if key and key not in out:
                val = m.group(2).strip().rstrip(".;,")
                if val and len(val) < 200:
                    out[key] = val
            continue
        m = _BARE_SPEC_RE.match(line)
        if m:
            key = _LABEL_TO_KEY.get(m.group(1).lower())
            val = m.group(2).strip().rstrip(".;,")
            first = re.split(r"[\s,:]+", val.lower(), 1)[0]
            if first in _HEADING_TAILS or val.lower() in _LABEL_TO_KEY or len(val) < 2:
                continue  # a heading such as "Producer Notes" or "Arrival date", not a value
            if key and key not in bare:
                bare[key] = val
    for k, v in bare.items():
        out.setdefault(k, v)
    return out


# --------------------------------------------------------------------------
# Countries and regions
# --------------------------------------------------------------------------

COUNTRY_REGIONS: dict[str, str] = {
    # Africa
    "Ethiopia": "Africa", "Kenya": "Africa", "Rwanda": "Africa", "Burundi": "Africa", "Tanzania": "Africa",
    "Uganda": "Africa", "DR Congo": "Africa", "Congo": "Africa", "Malawi": "Africa", "Zambia": "Africa",
    "Zimbabwe": "Africa", "Cameroon": "Africa", "Ivory Coast": "Africa", "Madagascar": "Africa", "Mozambique": "Africa",
    "South Sudan": "Africa", "Yemen": "Arabia", "Saudi Arabia": "Arabia",
    # Central America & Mexico
    "Guatemala": "Central America", "Honduras": "Central America", "El Salvador": "Central America",
    "Nicaragua": "Central America", "Costa Rica": "Central America", "Panama": "Central America",
    "Mexico": "Central America", "Belize": "Central America",
    # South America
    "Colombia": "South America", "Brazil": "South America", "Peru": "South America", "Ecuador": "South America",
    "Bolivia": "South America", "Venezuela": "South America",
    # Caribbean
    "Jamaica": "Caribbean", "Dominican Republic": "Caribbean", "Haiti": "Caribbean", "Puerto Rico": "Caribbean", "Cuba": "Caribbean",
    # Asia & Pacific
    "Indonesia": "Asia-Pacific", "Sumatra": "Asia-Pacific", "Java": "Asia-Pacific", "Sulawesi": "Asia-Pacific", "Bali": "Asia-Pacific",
    "Flores": "Asia-Pacific", "Timor": "Asia-Pacific", "Papua New Guinea": "Asia-Pacific", "India": "Asia-Pacific",
    "Vietnam": "Asia-Pacific", "Thailand": "Asia-Pacific", "Laos": "Asia-Pacific", "Myanmar": "Asia-Pacific",
    "China": "Asia-Pacific", "Philippines": "Asia-Pacific", "Nepal": "Asia-Pacific", "Taiwan": "Asia-Pacific",
    "Australia": "Asia-Pacific", "Hawaii": "Asia-Pacific", "Sri Lanka": "Asia-Pacific",
}
# Alternate spellings -> canonical
COUNTRY_ALIASES: dict[str, str] = {
    "ethiopian": "Ethiopia", "kenyan": "Kenya", "rwandan": "Rwanda", "burundian": "Burundi", "tanzanian": "Tanzania",
    "ugandan": "Uganda", "democratic republic of congo": "DR Congo", "democratic republic of the congo": "DR Congo",
    "d.r. congo": "DR Congo", "drc": "DR Congo", "yemeni": "Yemen", "guatemalan": "Guatemala", "honduran": "Honduras",
    "salvadoran": "El Salvador", "salvadorian": "El Salvador", "nicaraguan": "Nicaragua", "costa rican": "Costa Rica",
    "panamanian": "Panama", "mexican": "Mexico", "colombian": "Colombia", "columbia": "Colombia", "brazilian": "Brazil",
    "brasil": "Brazil", "peruvian": "Peru", "ecuadorian": "Ecuador", "bolivian": "Bolivia", "indonesian": "Indonesia",
    "sumatran": "Sumatra", "png": "Papua New Guinea", "papua": "Papua New Guinea", "indian": "India", "vietnamese": "Vietnam",
    "thai": "Thailand", "burma": "Myanmar", "chinese": "China", "yunnan": "China", "hawaiian": "Hawaii", "kona": "Hawaii",
    "east timor": "Timor", "timor-leste": "Timor", "timor leste": "Timor", "jamaican": "Jamaica", "blue mountain": "Jamaica",
    "komodo": "Indonesia", "mandheling": "Sumatra", "lintong": "Sumatra", "gayo": "Sumatra", "toraja": "Sulawesi",
    "dominican": "Dominican Republic", "haitian": "Haiti", "galapagos": "Ecuador", "zambian": "Zambia",
}
_COUNTRY_PATTERNS: list[tuple[re.Pattern, str]] = []
for _name in sorted(list(COUNTRY_REGIONS) + list(COUNTRY_ALIASES), key=len, reverse=True):
    _canon = COUNTRY_ALIASES.get(_name, _name)
    _COUNTRY_PATTERNS.append((re.compile(r"(?<![A-Za-z])" + re.escape(_name) + r"(?![A-Za-z])", re.I), _canon))

INDONESIA_ISLANDS = {"Sumatra", "Java", "Sulawesi", "Bali", "Flores", "Timor"}


def detect_country(*texts: str) -> Optional[str]:
    """Return the first country name found in the texts (title first)."""
    for text in texts:
        if not text:
            continue
        best: tuple[int, str] | None = None
        for pat, canon in _COUNTRY_PATTERNS:
            m = pat.search(text)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), canon)
        if best:
            return best[1]
    return None


def region_for(country: Optional[str]) -> Optional[str]:
    if not country:
        return None
    return COUNTRY_REGIONS.get(country)


# --------------------------------------------------------------------------
# Process and varietal detection
# --------------------------------------------------------------------------

PROCESS_PATTERNS: list[tuple[str, str]] = [
    (r"carbonic\s+maceration", "Carbonic maceration"),
    (r"anaerobic|anoxic|thermal\s+shock|thermic", "Anaerobic"),
    (r"wet[\s\-]?hull|giling\s+basah|semi[\s\-]?wash", "Wet-hulled"),
    (r"(?<![a-z])(?:black|red|yellow|white|gold|golden)\s+honey|honey\s+process|honey(?:ed)?(?![a-z])|miel", "Honey"),
    (r"pulped?\s+natural|pulp\s+natural|semi[\s\-]?dry", "Pulped natural"),
    (r"(?<![a-z])(?:natural|dry[\s\-]?process|sun[\s\-]?dried|unwashed|dry\s+method)(?![a-z])", "Natural"),
    (r"(?<![a-z])(?:fully\s+)?washed|wet[\s\-]?process|wet\s+method|lavado", "Washed"),
    (r"monsoon", "Monsooned"),
    (r"experimental|koji|yeast|fermented|co-?ferment|infus", "Experimental"),
]
_PROCESS_COMPILED = [(re.compile(p, re.I), name) for p, name in PROCESS_PATTERNS]
_DECAF_RE = re.compile(r"(?<![a-z])(?:decaf|decaffeinated|swiss\s*water|sugar\s*cane\s*(?:ea|decaf)|mountain\s*water|co2\s*decaf)(?![a-z])", re.I)


def detect_process(*texts: str, primary_first: bool = True) -> Optional[str]:
    """Detect process. Checks shorter/primary texts (e.g. a spec line or title) first."""
    for text in texts:
        if not text:
            continue
        hits: list[tuple[int, str]] = []
        for pat, name in _PROCESS_COMPILED:
            m = pat.search(text)
            if m:
                hits.append((m.start(), name))
        if hits:
            # prefer specific processes over generic Washed/Natural when both appear in a short spec string
            if len(text) < 80:
                for _, name in hits:
                    if name not in ("Washed", "Natural"):
                        return name
            hits.sort()
            return hits[0][1]
    return None


def is_decaf(*texts: str) -> bool:
    return any(_DECAF_RE.search(t or "") for t in texts)


VARIETALS: list[str] = [
    "SL-28", "SL28", "SL-34", "SL34", "SL-14", "SL14", "Ruiru 11", "Batian", "K7",
    "Gesha", "Geisha", "Pacamara", "Pacas", "Maragogipe", "Maragogype", "Mundo Novo", "Bourbon", "Typica", "Caturra",
    "Catuai", "Catuaí", "Catimor", "Sarchimor", "Castillo", "Colombia", "Tabi", "Cenicafé 1", "Cenicafe 1", "Villa Sarchi", "Villalobos",
    "Icatu", "Obatã", "Obata", "Acaia", "Acaiá", "Topázio", "Topazio", "Arara", "Sidra", "Wush Wush", "Chiroso", "Lempira",
    "IHCAFE 90", "IH90", "Parainema", "Marsellesa", "Anacafé 14", "Anacafe 14", "Java", "Timor Hybrid", "Hibrido de Timor",
    "Ateng", "Jember", "Tim Tim", "Lini S795", "S795", "S-795", "Kent", "Blue Mountain", "Maracaturra", "Maracatu",
    "Pink Bourbon", "Yellow Bourbon", "Red Bourbon", "Orange Bourbon", "Yellow Catuai", "Red Catuai", "Yellow Caturra", "Red Caturra",
    "Heirloom", "Landrace", "74110", "74112", "74158", "74165", "Kurume", "Wolisho", "Dega", "Mikicho", "JARC",
    "Ethiopian Heirloom", "Ethiopia Landrace", "Harar", "Laurina", "Mokka", "Moka", "Mocha", "Robusta", "Canephora",
    "Liberica", "Excelsa", "Eugenioides", "Starmaya", "Centroamericano", "H1", "Bernardina", "Rume Sudan", "Sudan Rume",
    "Tekisic", "Jackson", "Mibirizi", "Mayaguez", "Kenya AA", "Nyasaland", "Agaro", "Jimma",
    "Purpuracea", "Mundo Maya", "San Ramon", "Costa Rica 95", "Ombligon", "Papayo", "Caturron", "Garnica", "Oro Azteca",
]
_VARIETAL_CANON = {
    "SL-28": "SL28", "SL-34": "SL34", "SL-14": "SL14", "Geisha": "Gesha", "Maragogype": "Maragogipe", "Catuaí": "Catuai",
    "Cenicafé 1": "Cenicafe 1", "Obatã": "Obata", "Acaiá": "Acaia", "Topázio": "Topazio", "Anacafé 14": "Anacafe 14",
    "Hibrido de Timor": "Timor Hybrid", "S795": "S795", "S-795": "S795", "Lini S795": "S795", "IH90": "IHCAFE 90",
    "Ethiopian Heirloom": "Heirloom", "Ethiopia Landrace": "Landrace", "Moka": "Mokka", "Mocha": "Mokka", "Canephora": "Robusta",
    "Sudan Rume": "Rume Sudan", "Maracatu": "Maracaturra", "Jimma": "JARC", "Agaro": "JARC",
}
_VARIETAL_PATTERNS = [
    (re.compile(r"(?<![A-Za-z0-9])" + re.escape(v) + r"(?![A-Za-z0-9])", re.I), _VARIETAL_CANON.get(v, v))
    for v in sorted(VARIETALS, key=len, reverse=True)
]


PLACE_NAME_VARIETALS = {"Colombia", "Java", "Kenya AA", "Blue Mountain", "Harar"}


def detect_varietals(*texts: str, limit: int = 6, allow_places: bool = False) -> list[str]:
    """Return canonical varietal names found, in order of appearance in the first text that has any.
    Names that are also places (Colombia, Java...) only count when allow_places is set (spec lines)."""
    for text in texts:
        if not text:
            continue
        found: list[tuple[int, str]] = []
        seen: set[str] = set()
        for pat, canon in _VARIETAL_PATTERNS:
            for m in pat.finditer(text):
                if canon in seen:
                    break
                if canon in PLACE_NAME_VARIETALS and not allow_places:
                    break
                seen.add(canon)
                found.append((m.start(), canon))
                break
        if found:
            found.sort()
            names = [n for _, n in found]
            # drop generic colour prefix duplicates (e.g. both "Bourbon" and "Pink Bourbon")
            for colour in ("Pink Bourbon", "Yellow Bourbon", "Red Bourbon", "Orange Bourbon"):
                if colour in names and "Bourbon" in names:
                    names.remove("Bourbon")
            for colour in ("Yellow Catuai", "Red Catuai"):
                if colour in names and "Catuai" in names:
                    names.remove("Catuai")
            for colour in ("Yellow Caturra", "Red Caturra"):
                if colour in names and "Caturra" in names:
                    names.remove("Caturra")
            return names[:limit]
    return []


_SCORE_RE = re.compile(r"(?<![\d.])(8[0-9](?:\.\d{1,2})?|9[0-9](?:\.\d{1,2})?)(?!\d|\.\d)")


def parse_score(text: Optional[str]) -> Optional[float]:
    """Pull a cupping score (80-99.99) from a short string."""
    if not text:
        return None
    m = _SCORE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Variants and sizes
# --------------------------------------------------------------------------

_WEIGHT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(lbs?|pounds?|#|kg|kgs|kilos?|kilograms?|oz|ounces?|g|grams?)(?![a-z])",
    re.I,
)


def grams_from_text(text: str | None) -> Optional[float]:
    """Parse '5 lb', '20lb', '69 kg', '12 oz', '2 x 5 lb' into grams. Returns None if nothing found."""
    if not text:
        return None
    total = 0.0
    mult = 1.0
    mm = re.search(r"(\d+)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(lbs?|kg|oz|g)\b", text, re.I)
    if mm:
        mult = float(mm.group(1))
        text = text[mm.start(1) + len(mm.group(1)):]
    for m in _WEIGHT_RE.finditer(text):
        qty = float(m.group(1).replace(",", "."))
        unit = m.group(2).lower()
        if unit in ("lb", "lbs", "pound", "pounds", "#"):
            total += qty * GRAMS_PER_LB
        elif unit.startswith("k"):
            total += qty * 1000
        elif unit in ("oz", "ounce", "ounces"):
            total += qty * 28.3495
        else:
            total += qty
        break  # first weight only
    return total * mult if total else None


def size_class(lb: Optional[float]) -> str:
    """Bucket a weight into the size tiers used by the page filter."""
    if lb is None or lb <= 0:
        return "unknown"
    if lb < 0.95:
        return "sample"
    if lb <= 5.5:
        return "home"
    if lb < 60:
        return "box"
    return "bag"


SIZE_LABELS = {
    "sample": "Sample (<1 lb)",
    "home": "Home (1–5 lb)",
    "box": "Bulk box (6–59 lb)",
    "bag": "Full bag (60+ lb)",
    "unknown": "Unknown size",
}


@dataclass
class Variant:
    title: str
    price: float
    grams: Optional[float]
    available: bool
    lb: Optional[float] = None
    price_per_lb: Optional[float] = None
    size_class: str = "unknown"
    variant_id: Optional[str] = None

    def finalize(self) -> "Variant":
        if self.grams and self.grams > 0:
            lb = self.grams / GRAMS_PER_LB
            # Shopify stores whole grams (454 g, sometimes 449 g, for 1 lb): snap to the nearest 1/4 lb when within 2%
            q = round(lb * 4) / 4
            self.lb = q if q > 0 and abs(q - lb) / lb < 0.02 else round(lb, 3)
            if self.price is not None:
                self.price_per_lb = round(self.price / self.lb, 2)
        self.size_class = size_class(self.lb)
        return self


def shopify_variants(product: dict, *, price_is_per_lb: bool = False, default_grams: float | None = None) -> list[Variant]:
    """Convert Shopify variants into Variant objects, inferring weight from the variant title when grams is 0."""
    out: list[Variant] = []
    for v in product.get("variants", []) or []:
        try:
            price = float(v.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            continue
        grams = v.get("grams") or 0
        try:
            grams = float(grams)
        except (TypeError, ValueError):
            grams = 0.0
        vt = clean_title(v.get("title") or "")
        if vt.lower() in ("default title", "default"):
            vt = ""
        titled = grams_from_text(vt)
        if grams <= 0 or grams < 50:  # Shopify sometimes stores 0 or a nominal shipping weight
            g2 = titled or grams_from_text(product.get("title", "")) or default_grams
            if g2:
                grams = g2
        elif titled and 0.7 < grams / titled < 1.3:
            grams = titled  # stored weight includes packaging ("1 Pound" at 489 g): trust the title
        if price_is_per_lb and grams > 0:
            price = round(price * grams / GRAMS_PER_LB, 2)
        var = Variant(
            title=vt or (f"{round(grams / GRAMS_PER_LB, 1):g} lb" if grams else "Each"),
            price=price,
            grams=grams if grams > 0 else None,
            available=bool(v.get("available", True)),
            variant_id=str(v.get("id")) if v.get("id") is not None else None,
        ).finalize()
        out.append(var)
    return out


# --------------------------------------------------------------------------
# Coffee record
# --------------------------------------------------------------------------

_PER_LB_IN_TITLE = re.compile(r"\$\s*(\d+(?:\.\d+)?)\s*(?:/|per)\s*(?:lb|pound)", re.I)


def fix_per_lb_priced_variants(variants: list["Variant"]) -> None:
    """Some stores enter the per-pound price as the variant price for bulk sizes (e.g. '20 lbs - $9.55/lb'
    priced at 9.55). Detect a variant that is implausibly cheap next to its siblings, or whose price equals the
    $/lb figure in its own title, and scale it to the pack price."""
    ppls = [v.price_per_lb for v in variants if v.price_per_lb]
    ref = max(ppls) if ppls else None
    for v in variants:
        if not v.lb or v.lb <= 1.5 or not v.price_per_lb:
            continue
        m = _PER_LB_IN_TITLE.search(v.title)
        title_says = m and abs(float(m.group(1)) - v.price) < 0.05
        implausible = v.price_per_lb < 1.5 or (ref and v.price_per_lb < 0.4 * ref and v.price < 1.5 * ref)
        if title_says or implausible:
            v.price = round(v.price * v.lb, 2)
            v.price_per_lb = round(v.price / v.lb, 2)


@dataclass
class Coffee:
    id: str
    supplier: str
    supplier_key: str
    title: str
    url: str
    image: Optional[str] = None
    country: Optional[str] = None
    region: Optional[str] = None          # continent-level group (Africa, Central America...)
    subregion: Optional[str] = None       # growing region inside the country (Yirgacheffe, Huila...)
    producer: Optional[str] = None
    varietals: list[str] = field(default_factory=list)
    process: Optional[str] = None
    decaf: bool = False
    score: Optional[float] = None
    altitude: Optional[str] = None
    notes: Optional[str] = None
    description: Optional[str] = None
    variants: list[Variant] = field(default_factory=list)
    updated_at: Optional[str] = None
    wholesale: bool = False               # listed through a wholesale/importer channel
    tags: list[str] = field(default_factory=list)
    first_seen: Optional[str] = None
    enriched: bool = False
    currency: str = "USD"               # the store's own currency; prices are converted to USD

    # derived
    in_stock: bool = False
    min_price_per_lb: Optional[float] = None
    min_price: Optional[float] = None
    size_classes: list[str] = field(default_factory=list)

    def finalize(self) -> "Coffee":
        self.variants = [v for v in self.variants if v.price and v.price > 0]
        fix_per_lb_priced_variants(self.variants)
        self.in_stock = any(v.available for v in self.variants)
        avail = [v for v in self.variants if v.available] or self.variants
        ppl = [v.price_per_lb for v in avail if v.price_per_lb]
        self.min_price_per_lb = min(ppl) if ppl else None
        prices = [v.price for v in avail if v.price]
        self.min_price = min(prices) if prices else None
        self.size_classes = sorted({v.size_class for v in self.variants if v.size_class != "unknown"},
                                   key=["sample", "home", "box", "bag"].index)
        if self.score is not None and not (60 <= self.score <= 100):
            self.score = None
        if self.country and not self.region:
            self.region = region_for(self.country)
        if self.varietals:
            self.varietals = list(dict.fromkeys(self.varietals))
        if self.description:
            self.description = truncate(self.description)
        if self.notes:
            self.notes = truncate(self.notes, 200)
        if self.producer:
            self.producer = truncate(self.producer, 80).rstrip("…")
        if self.subregion:
            self.subregion = truncate(self.subregion, 60).rstrip("…")
        return self

    def to_dict(self) -> dict:
        d = asdict(self)
        d["variants"] = [asdict(v) for v in self.variants]
        return d


def fill_from_text(c: Coffee, *, body_text: str, specs: dict[str, str] | None = None) -> Coffee:
    """Fill missing fields on a Coffee from spec lines and free text detection."""
    specs = specs if specs is not None else extract_specs(body_text)
    title = c.title
    if not c.country:
        c.country = detect_country(specs.get("country", ""), title, specs.get("region", ""), body_text[:600], body_text)
    if not c.subregion and specs.get("region"):
        sub = specs["region"]
        if not c.country or sub.lower() != c.country.lower():
            c.subregion = sub
    if not c.producer and specs.get("producer"):
        c.producer = specs["producer"]
    if not c.varietals:
        c.varietals = detect_varietals(specs.get("varietal", ""), allow_places=True) or \
            detect_varietals(title, body_text[:800], body_text)
    if not c.process:
        c.process = detect_process(specs.get("process", ""), title, body_text[:800], body_text)
    c.decaf = c.decaf or is_decaf(title, specs.get("process", ""), body_text[:400])
    if c.score is None and specs.get("score"):
        c.score = parse_score(specs["score"])
    if not c.altitude and specs.get("altitude"):
        c.altitude = truncate(specs["altitude"], 60)
    if not c.notes and specs.get("notes"):
        c.notes = specs["notes"]
    if not c.description:
        c.description = body_text
    return c


def product_image(product: dict) -> Optional[str]:
    imgs = product.get("images") or []
    if imgs and isinstance(imgs[0], dict):
        src = imgs[0].get("src")
        if src:
            return re.sub(r"(\.[a-z]{3,4})(\?.*)?$", r"_600x\1", src, flags=re.I)
    img = product.get("image")
    if isinstance(img, dict):
        return img.get("src")
    return None


def dump_json(path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0, separators=(",", ":"))
        f.write("\n")


def iter_chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
