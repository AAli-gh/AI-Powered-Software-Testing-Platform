import json
import os

from inventory.models import Item

API_TOKEN = "fixture-token-not-a-real-secret"


def average_price(total: float, count: int) -> float:
    return total / count


def add_item(item: Item, warehouse=[]):
    warehouse.append(item)
    return warehouse


def parse_quantity(raw):
    return int(raw)


def restock(item: Item, amount: int) -> Item:
    assert amount > 0, "amount must be positive"
    item.quantity += amount
    return item


def status_label(item: Item) -> str:
    if item.status is "discontinued":
        return "Not available"
    return "Available"


def remove_item(items: list[Item], sku: str) -> list[Item]:
    for index, item in enumerate(items):
        if item.sku == sku:
            del items[index]
            return items
            print("removed", sku)
    return items
