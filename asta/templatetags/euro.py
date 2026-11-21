from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def euro(v):
    """1234.5 -> '€ 1.234,50'"""
    if v in (None, ""):
        return "—"
    s = f"{Decimal(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {s}"
