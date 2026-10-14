import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
STATE_FILE = BASE / "products.json"

STORES = [
    ("Little Things UAE", "https://littlethingsme.com"),
    ("Geekay", "https://www.geekay.com/search?q=pokemon"),
    ("Otaku Middle East", "https://otakume.com/collections/pokemon"),
    ("Pokemon Center", "https://www.pokemoncenter.com/search/pokemon"),
    ("Target", "https://www.target.com/s?searchTerm=pokemon+trading+cards"),
    ("Walmart", "https://www.walmart.com/search?q=pokemon+trading+cards"),
    ("Best Buy", "https://www.bestbuy.com/site/searchpage.jsp?st=pokemon+trading+cards"),
    ("Sam's Club", "https://www.samsclub.com/search?searchTerm=pokemon"),
    ("Virgin Megastore UAE", "https://www.virginmegastore.ae/en/search?q=pokemon"),
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PersonalProductMonitor/1.0)"
}


def read_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"products": {}, "events": [], "last_checked": None}


def find_products(data, results):
    if isinstance(data, list):
        for item in data:
            find_products(item, results)
    elif isinstance(data, dict):
        kind = data.get("@type", "")
        if "Product" in (kind if isinstance(kind, list) else [kind]):
            results.append(data)
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in data:
                find_products(data[key], results)


def scan_store(name, url):
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    found = []

    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.string or tag.get_text())
            find_products(data, found)
        except (json.JSONDecodeError, TypeError):
            continue

    products = {}

    for item in found:
        title = item.get("name")
        if not title:
            continue

        offers = item.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if not isinstance(offers, dict):
            offers = {}

        price = offers.get("price")
        try:
            price = float(str(price).replace(",", "")) if price else None
        except ValueError:
            price = None

        product_url = item.get("url") or url
        product_url = urljoin(url, product_url)
        availability = str(offers.get("availability", "")).lower()

        if "outofstock" in availability or "soldout" in availability:
            stock = "out_of_stock"
        elif "instock" in availability or "limitedavailability" in availability:
            stock = "in_stock"
        else:
            stock = "unknown"

        text = (title + " " + str(item.get("description", ""))).lower()
        preorder = bool(re.search(r"\bpre[\s-]?order\b", text))

        key = name + "|" + product_url
        products[key] = {
            "store": name,
            "title": title,
            "url": product_url,
            "price": price,
            "currency": offers.get("priceCurrency"),
            "stock": stock,
            "preorder": preorder,
        }

    return products


def main():
    state = read_state()
    previous = state.get("products", {})
    current = {}
    events = []
    baseline = not bool(previous)

    for name, url in STORES:
        try:
            products = scan_store(name, url)
            print(f"{name}: found {len(products)} structured products")

            for key, product in products.items():
                current[key] = product
                old = previous.get(key)

                if old is None:
                    if not baseline:
                        events.append({
                            "type": "NEW PRODUCT",
                            **product
                        })
                    if product["preorder"] and not baseline:
                        events.append({
                            "type": "PREORDER",
                            **product
                        })
                    continue

                if old.get("stock") == "out_of_stock" and product["stock"] == "in_stock":
                    events.append({"type": "RESTOCK", **product})

                if product["preorder"] and not old.get("preorder", False):
                    events.append({"type": "PREORDER", **product})

                old_price = old.get("price")
                new_price = product.get("price")

                if old_price and new_price and new_price < old_price:
                    drop = (old_price - new_price) / old_price * 100
                    if drop >= 5:
                        events.append({
                            "type": "PRICE DROP",
                            "drop_percent": round(drop, 1),
                            **product
                        })

        except Exception as error:
            print(f"{name}: scan failed: {error}")

        time.sleep(2)

    # Preserve previously known products if a store fails to load.
    for key, old in previous.items():
        current.setdefault(key, old)

    state = {
        "products": current,
        "events": (events + state.get("events", []))[:100],
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "baseline_complete": True,
    }

    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Saved {len(current)} products and {len(events)} events.")


if __name__ == "__main__":
    main()
