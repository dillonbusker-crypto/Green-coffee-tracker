#!/usr/bin/env python3
"""Probe candidate stores without touching the tracker data.

    python scraper/probe.py burmancoffee.com happymugcoffee.com ...

For each host it tries Shopify's /products.json and reports: reachable?, Shopify?, product_type counts,
how many products look like green coffee, the smallest pack size seen, and a few sample titles.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import GRAMS_PER_LB, get, grams_from_text  # noqa: E402

GREEN_RE = re.compile(r"\bgreen\b|unroasted|\braw\b", re.I)


def probe(host: str) -> dict:
    base = host if host.startswith("http") else f"https://{host}"
    out: dict = {"host": host}
    try:
        r = get(f"{base}/products.json?limit=250&page=1", retries=1, timeout=20, sleep=0)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        try:
            r2 = get(base, retries=0, timeout=15, sleep=0)
            html = r2.text[:200000]
            out["homepage"] = r2.status_code
            out["platform_hint"] = ("shopify" if "cdn.shopify.com" in html else "woocommerce" if "woocommerce" in html.lower()
                                    else "bigcommerce" if "bigcommerce" in html.lower() else "squarespace" if "squarespace" in html.lower()
                                    else "wix" if "wix.com" in html.lower() else "unknown")
        except Exception as e2:  # noqa: BLE001
            out["homepage_error"] = f"{type(e2).__name__}: {str(e2)[:80]}"
        return out
    try:
        products = r.json().get("products", [])
    except ValueError:
        out["error"] = "not JSON (not Shopify)"
        return out
    out["shopify"] = True
    out["products_page1"] = len(products)
    out["product_types"] = dict(Counter(p.get("product_type") or "<none>" for p in products).most_common(10))
    greens = [p for p in products if GREEN_RE.search(" ".join([p.get("title") or "", p.get("product_type") or ""] + [t for t in p.get("tags", []) if isinstance(t, str)]))]
    out["green_keyword_matches"] = len(greens)
    sizes = []
    for p in products:
        for v in p.get("variants", []):
            g = v.get("grams") or 0
            if g < 50:
                g = grams_from_text(v.get("title") or "") or 0
            if g:
                sizes.append(round(g / GRAMS_PER_LB, 2))
    out["smallest_lb"] = min(sizes) if sizes else None
    out["size_counts"] = dict(Counter(sizes).most_common(6))
    out["samples"] = [f"{p.get('title')} [{p.get('product_type')}] " + ", ".join(f"{v.get('title')} ${v.get('price')}" for v in p.get("variants", [])[:3]) for p in (greens or products)[:6]]
    return out


if __name__ == "__main__":
    hosts = [h for arg in sys.argv[1:] for h in arg.split(",") if h.strip()]
    for h in hosts:
        res = probe(h.strip())
        print("=" * 80)
        print(json.dumps(res, indent=1, ensure_ascii=False))
