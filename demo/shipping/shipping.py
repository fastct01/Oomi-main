"""Shipping quotes for a parcel or a list of parcels."""

import collections

RATE_PER_KG = 3.2


def parcels(items):
    if isinstance(items, collections.Iterable) and not isinstance(items, dict):
        return list(items)
    return [items]


def quote(items) -> float:
    return round(sum(p["kg"] * RATE_PER_KG for p in parcels(items)), 2)
