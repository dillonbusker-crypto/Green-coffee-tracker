"""Supplier adapters. Each exposes KEY, NAME and scrape() -> list[Coffee]."""
from . import sweetmarias, bodhileaf, coffeebeancorral, royalcoffee, generic_shopify

SUPPLIERS = [sweetmarias, bodhileaf, coffeebeancorral, royalcoffee] + generic_shopify.modules()
