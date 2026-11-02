"""Run the whole pipeline offline against the fixture catalogs (writes docs/data/*.json)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import common  # noqa: E402

FIX = {
    "sweetmarias.com": "sweetmarias.json", "bodhileafcoffee.com": "bodhileaf.json",
    "coffeebeancorral.com": "coffeebeancorral.json", "royalcoffee.com": "royalcoffee.json",
}


def fake_products(base, **kw):
    for host, fn in FIX.items():
        if host in base:
            return json.loads((HERE / "fixtures" / fn).read_text())["products"]
    return []


class FakeResp:
    def __init__(self, text):
        self.text = text


def fake_get(url, **kw):
    if "sweetmarias.com/products/" in url:
        return FakeResp((HERE / "fixtures" / "sweetmarias_page.html").read_text())
    raise RuntimeError("offline")


common.shopify_products = fake_products
common.get = fake_get
import suppliers.sweetmarias as sm  # noqa: E402
import suppliers.bodhileaf as bl  # noqa: E402
import suppliers.coffeebeancorral as cbc  # noqa: E402
import suppliers.royalcoffee as rc  # noqa: E402
import suppliers.generic_shopify as gs  # noqa: E402
for m in (sm, bl, cbc, rc, gs):
    m.shopify_products = fake_products
sm.get = fake_get

import scrape  # noqa: E402
sys.argv = ["scrape", "--no-enrich"]
sys.exit(scrape.main())
