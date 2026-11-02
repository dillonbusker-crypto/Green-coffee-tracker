"""Offline parser tests on fixture catalogs. Run: python -m pytest scraper/tests  (or python scraper/tests/test_parsers.py)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from common import detect_country, detect_process, detect_varietals, grams_from_text, size_class, extract_specs, html_to_text  # noqa: E402
from suppliers import sweetmarias, bodhileaf, coffeebeancorral, royalcoffee  # noqa: E402


def load(name):
    return json.loads((HERE / "fixtures" / name).read_text())["products"]


def test_helpers():
    assert detect_country("Kenya Nyeri Gatomboya AA") == "Kenya"
    assert detect_country("Finca El Paraiso, Colombian beauty") == "Colombia"
    assert detect_country("A nice coffee") is None
    assert detect_process("Wet Process") == "Washed"
    assert detect_process("Thermal shock anaerobic") == "Anaerobic"
    assert detect_process("Black honey") == "Honey"
    assert detect_process("Sun dried on raised beds") == "Natural"
    assert detect_varietals("SL-28, SL-34, Ruiru 11") == ["SL28", "SL34", "Ruiru 11"]
    assert detect_varietals("Pink Bourbon") == ["Pink Bourbon"]
    assert grams_from_text("5 LB") == 2267.96 or abs(grams_from_text("5 LB") - 2267.96) < 0.1
    assert abs(grams_from_text("69 kg") - 69000) < 0.1
    assert abs(grams_from_text("12 oz") - 340.19) < 0.1
    assert size_class(0.5) == "sample" and size_class(1) == "home" and size_class(5) == "home"
    assert size_class(22) == "box" and size_class(50) == "box" and size_class(132) == "bag"
    specs = extract_specs(html_to_text("<p><strong>Region:</strong> Huila<br><strong>Producer:</strong> Diego</p>"))
    assert specs == {"region": "Huila", "producer": "Diego"}


def test_sweetmarias():
    products = load("sweetmarias.json")
    page = (HERE / "fixtures" / "sweetmarias_page.html").read_text()
    attrs = sweetmarias.parse_product_page(page)
    assert attrs["score"] == 88.5 and attrs["varietal"].startswith("SL-28") and attrs["region"] == "Nyeri"
    coffees = [c for c in (sweetmarias.parse_product(p, page if p["id"] == 8001 else None) for p in products) if c]
    assert [c.title for c in coffees] == ["Kenya Nyeri Gatomboya AA", "Guatemala Huehuetenango Finca Rosma", "Decaf Ethiopia Guji SWP"]
    k = coffees[0]
    assert k.country == "Kenya" and k.region == "Africa" and k.score == 88.5 and k.wholesale
    assert k.varietals == ["SL28", "SL34", "Ruiru 11"] and k.process == "Washed" and k.subregion == "Nyeri"
    assert k.in_stock and k.min_price_per_lb == 9.3 and k.size_classes == ["home", "box"]
    assert [v.price_per_lb for v in k.variants] == [9.95, 9.3, 8.6]
    g = coffees[1]
    assert g.country == "Guatemala" and g.region == "Central America" and not g.in_stock and g.score is None
    d = coffees[2]
    assert d.decaf and d.country == "Ethiopia"


def test_bodhileaf():
    coffees = [c for c in (bodhileaf.parse_product(p) for p in load("bodhileaf.json")) if c]
    assert len(coffees) == 2
    c = coffees[0]
    assert c.title == "Colombia Huila Finca El Paraiso Pink Bourbon"
    assert c.country == "Colombia" and c.subregion == "Huila" and c.producer == "Diego Bermudez"
    assert c.varietals == ["Pink Bourbon"] and c.process == "Anaerobic" and c.altitude == "1900 masl"
    assert c.notes == "lychee, rose, bubblegum" and c.size_classes == ["home", "box"]
    e = coffees[1]
    assert e.country == "Ethiopia" and e.process == "Natural" and "Heirloom" in e.varietals
    assert e.variants[0].lb == 1.0 and e.variants[0].price_per_lb == 9.5  # grams inferred from the variant title


def test_coffeebeancorral():
    coffees = [c for c in (coffeebeancorral.parse_product(p) for p in load("coffeebeancorral.json")) if c]
    assert [c.title for c in coffees] == ["Brazil Cerrado Natural", "Sumatra Mandheling Grade 1"]
    b = coffees[0]
    assert b.country == "Brazil" and b.producer == "Fazenda Santa Ines" and b.process == "Natural" and b.score == 84.0
    assert set(b.varietals) == {"Yellow Catuai", "Mundo Novo"}
    assert b.size_classes == ["home", "box", "bag"]
    s = coffees[1]
    assert s.country == "Sumatra" and s.region == "Asia-Pacific" and s.process == "Wet-hulled"


def test_royalcoffee():
    coffees = [c for c in (royalcoffee.parse_product(p) for p in load("royalcoffee.json")) if c]
    assert len(coffees) == 2
    col = coffees[0]
    assert col.country == "Colombia" and col.score == 85.5 and col.process == "Washed" and col.wholesale
    v = col.variants[0]
    assert v.price_per_lb == 4.15 and abs(v.price - 4.15 * 70000 / 453.592) < 0.05 and v.size_class == "bag"
    cj = coffees[1]
    assert cj.score == 87.25 and cj.variants[0].size_class == "box" and abs(cj.variants[0].price_per_lb - 7.18) < 0.01
    assert cj.varietals == ["74110", "74112"] and cj.tags[0] == "Crown Jewel" and cj.title == "Ethiopia Guji Shakiso Natural"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
