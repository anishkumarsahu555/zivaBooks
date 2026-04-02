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


def _format_money(value, places=2):
    if value is None:
        return "0"
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return _format_inr(value)

    quant = Decimal('1').scaleb(-places)
    return _format_inr(format(dec.quantize(quant), 'f'))


@register.filter(name="convert")
def convert(value):
    """Backward-compatible alias used in existing templates."""
    return _format_inr(value)


@register.filter(name="money")
def money(value):
    """Formats money in INR style with exactly 2 decimal places."""
    return _format_money(value, places=2)


_ONES = [
    "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
]

_TENS = [
    "", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety",
]


def _two_digit_words(number: int) -> str:
    if number < 20:
        return _ONES[number]
    tens = _TENS[number // 10]
    remainder = number % 10
    return tens if remainder == 0 else f"{tens} {_ONES[remainder]}"


def _three_digit_words(number: int) -> str:
    if number < 100:
        return _two_digit_words(number)
    hundreds = f"{_ONES[number // 100]} Hundred"
    remainder = number % 100
    return hundreds if remainder == 0 else f"{hundreds} {_two_digit_words(remainder)}"


def _integer_to_indian_words(number: int) -> str:
    if number == 0:
        return _ONES[0]
    parts = []
    crore = number // 10000000
    if crore:
        parts.append(f"{_integer_to_indian_words(crore)} Crore")
        number %= 10000000
    lakh = number // 100000
    if lakh:
        parts.append(f"{_integer_to_indian_words(lakh)} Lakh")
        number %= 100000
    thousand = number // 1000
    if thousand:
        parts.append(f"{_integer_to_indian_words(thousand)} Thousand")
        number %= 1000
    if number:
        parts.append(_three_digit_words(number))
    return " ".join(parts)


@register.filter(name="money_words")
def money_words(value):
    """Converts an INR amount into words using Indian numbering style."""
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return ""
    dec = dec.quantize(Decimal("0.01"))
    sign = "Minus " if dec < 0 else ""
    dec = abs(dec)
    rupees = int(dec)
    paise = int((dec - Decimal(rupees)) * 100)
    rupee_words = _integer_to_indian_words(rupees) + (" Rupee" if rupees == 1 else " Rupees")
    if paise:
        paise_words = _integer_to_indian_words(paise) + (" Paisa" if paise == 1 else " Paise")
        return f"{sign}{rupee_words} and {paise_words} Only"
    return f"{sign}{rupee_words} Only"
