from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _format_inr(value):
    """
    Format a number using Indian digit grouping.

    Example:
      1234567.89 -> 12,34,567.89
    """
    if value is None:
        return "0"

    # Keep decimals if present in input. Accept Decimal/float/int/str.
    try:
        # Normalize numeric types while preserving the textual decimal part when possible.
        if isinstance(value, Decimal):
            s = format(value, "f")
        else:
            s = str(value).strip()
    except Exception:
        return str(value)

    if not s:
        return "0"

    sign = ""
    if s[0] == "-":
        sign = "-"
        s = s[1:]

    whole, dot, frac = s.partition(".")
    # If whole part isn't numeric, just return as-is.
    if not whole.isdigit():
        try:
            Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return str(value)

    if len(whole) <= 3:
        grouped = whole
    else:
        last3 = whole[-3:]
        rest = whole[:-3]
        parts = []
        while rest:
            parts.append(rest[-2:])
            rest = rest[:-2]
        grouped = ",".join(reversed(parts)) + "," + last3

    if dot:
        return f"{sign}{grouped}.{frac}"
    return f"{sign}{grouped}"


@register.filter(name="convert")
def convert(value):
    """Backward-compatible alias used in existing templates."""
    return _format_inr(value)

