"""Supplier adapters. Each exposes KEY, NAME and scrape() -> list[Coffee]."""
from . import sweetmarias, bodhileaf, coffeebeancorral, royalcoffee, generic_shopify

ALL_SUPPLIERS = [sweetmarias, bodhileaf, coffeebeancorral, royalcoffee] + generic_shopify.modules()
SUPPLIERS = [m for m in ALL_SUPPLIERS if getattr(m, "ENABLED", True)]
