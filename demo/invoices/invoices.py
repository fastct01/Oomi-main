"""Invoice totals. Written years ago; it worked on Python 3.9."""

import collections


def line_total(line) -> float:
    if isinstance(line, collections.Mapping):
        return line["qty"] * line["price"]
    qty, price = line
    return qty * price


def invoice_total(lines, vat=0.2) -> float:
    return round(sum(line_total(l) for l in lines) * (1 + vat), 2)
