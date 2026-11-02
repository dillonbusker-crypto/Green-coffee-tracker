# Green Coffee Tracker

A daily snapshot of green (unroasted) coffee for sale at popular suppliers, shown on a
phone-friendly page with filters for supplier, region/country, producer, varietal,
process, cupping score and bag size, an in-stock toggle, and sorting by $/lb or score.

**Live page:** https://dillonbusker-crypto.github.io/Green-coffee-tracker/

## How it works

- `scraper/` (Python, requests + beautifulsoup4) pulls each supplier's catalog. Most
  suppliers run Shopify, which publishes everything at `/products.json`; Sweet Maria's
  product pages are fetched too because scores live only there.
- `.github/workflows/scrape.yml` runs it once a day (and on demand from the Actions tab)
  and commits `docs/data/coffees.json` and `docs/data/meta.json`. `first_seen` is kept per
  listing so the page can show a NEW badge for the first week.
- Only listings that are actually for sale are kept. A listing that sells out stays for 14
  days after it was last seen buyable (shown as "Sold out" when the in-stock toggle is off),
  then disappears. Suppliers' archive pages for past lots are never included.
- Fetching is polite but impatient: a blocked request (any 4xx) is never retried, anything
  else gets at most two retries, and a supplier that keeps failing is skipped for that run
  with the failure recorded in `meta.json` (yesterday's rows for it are kept, marked `stale`).
- `docs/index.html` is a single vanilla-JS page served by GitHub Pages. Filters are saved
  in localStorage, and a PWA manifest lets it go on the home screen.

## One-time setup

1. **GitHub Pages:** Settings → Pages → Source "Deploy from a branch", branch `main`, folder `/docs`.
2. **Run the first scrape:** Actions → "Scrape green coffee" → Run workflow. The page shows
   "no data yet" until the first run has committed data.
3. **Optional AI fill-in:** add a repository secret `ANTHROPIC_API_KEY`. When present, the
   scraper asks Claude Haiku (`claude-haiku-5-5`) to fill in missing producer / varietal /
   region / process from the description. Results are cached in `scraper/cache/enrich.json`
   by product id + `updated_at`, so each listing is only ever sent once.
   `GCT_ENRICH_MAX` (default 400) caps the number of listings sent per run.

## Suppliers

Two rules decide who is included, and `scraper/scrape.py` enforces both on every run:
the supplier ships from the US or Canada (`SHIPS_FROM` on each adapter), and it sells a
1 lb or smaller size (a supplier whose listings have no variant of 1 lb or less is dropped
and the reason recorded in `meta.json`).

| key | supplier | ships from | notes |
|---|---|---|---|
| `sweetmarias` | Sweet Maria's | Oakland, CA | `product_type == "Green Coffee"`; country from `Category_…` tags; score/varietal/process scraped from the product page and cached |
| `bodhileaf` | Bodhi Leaf | Orange, CA | titles contain "(Green Coffee)"; specs parsed from description prose; no scores |
| `coffeebeancorral` | Coffee Bean Corral | Mississippi | green product types only; "Country: Name, Region" titles |
| `captainscoffee` | The Captain's Coffee | Olympia, WA | Shopify, "green" keyword filter |
| `thetaridge` | Theta Ridge Coffee | Indiana | Shopify, everything is green coffee |
| `prodigal` | Prodigal Coffee | US | Shopify, "green" keyword filter (mostly roasted coffee, a few greens) |
| `hacea` | Hacea Coffee Source | San Diego, CA | Shopify, product type "Coffee"; 500 g / 2 kg / 20 kg |
| `happymug` | Happy Mug | Pennsylvania | Shopify; green coffees are typed by origin and sold by the pound (single default variant) |
| `greenbeanery` | Green Beanery | Toronto, ON | Shopify, product type "Unroasted Beans"; 0.5/1/2/5 lb; priced in CAD, converted to USD at a fixed rate in `generic_shopify.py` |

Kept but disabled because they only sell bulk: Royal Coffee (full bags, 22/50 lb boxes),
Mill City Roasters (10 lb minimum), Showroom Coffee (2 lb minimum), Coffee Shrub.
Disabled but ready if wanted (commodity-grade organic lots): Dean's Beans, Home Roast Coffee.
Not Shopify, so they would need their own adapter: Burman Coffee Traders and Smokin' Beans
(WooCommerce), Len's Coffee (BigCommerce), West Coast Coffee Traders (Squarespace).

To check a new store before adding it, run the workflow with the `probe` input set to its
host name; the job summary reports the platform, product types, sizes and sample titles.

Add a Shopify store by appending to `STORES` in `generic_shopify.py`. Anything else gets its
own module in `scraper/suppliers/` exposing `KEY`, `NAME` and `scrape()`.

## Local development

```bash
pip install -r scraper/requirements.txt
python scraper/tests/test_parsers.py          # offline parser tests on fixture catalogs
python scraper/tests/mock_run.py              # full pipeline on fixtures -> docs/data/*.json
python scraper/scrape.py --only bodhileaf     # real scrape of one supplier
python scraper/scrape.py --no-pages           # skip Sweet Maria's per-product page fetches
cd docs && python -m http.server 8000         # open http://localhost:8000
```

## Data shape

Each entry in `coffees.json` has `id`, `supplier`, `title`, `url`, `image`, `country`,
`region` (continent group), `subregion`, `producer`, `varietals[]`, `process`, `decaf`,
`score`, `altitude`, `notes`, `description`, `variants[]` (`title`, `price`, `grams`, `lb`,
`price_per_lb`, `available`, `size_class`), `in_stock`, `min_price_per_lb`, `min_price`,
`size_classes[]`, `first_seen`, `wholesale`, `enriched`.

Size classes: `sample` (<1 lb), `home` (1–5 lb), `box` (6–59 lb, e.g. 10/20/22/50 lb boxes), `bag` (60+ lb full bags).
