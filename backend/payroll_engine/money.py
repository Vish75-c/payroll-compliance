"""Money helpers. Every amount in the engine is a Decimal; floats never enter a calculation."""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")

ROUNDING_METHODS = {
    "nearest": ROUND_HALF_UP,  # 0.5 goes up: 975.5 -> 976
    "nearest_even": ROUND_HALF_EVEN,  # banker's rounding
    "up": ROUND_CEILING,  # always up: 121.01 -> 122 (ESI)
    "down": ROUND_FLOOR,  # always down
}


def d(value) -> Decimal:
    """Convert a value from JSON/DB into a Decimal. Floats are converted via str() to avoid binary noise."""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("booleans are not amounts")
    if isinstance(value, float):
        value = repr(value)
    try:
        return Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc


def round_amount(value: Decimal, method: str = "nearest", unit="1") -> Decimal:
    """Round to a multiple of ``unit`` (1 = whole rupees, 10 = nearest ten, 0.01 = paise)."""
    if method not in ROUNDING_METHODS:
        raise ValueError(f"unknown rounding method {method!r}")
    unit = d(unit)
    if unit <= 0:
        raise ValueError("rounding unit must be positive")
    quotient = (value / unit).quantize(ONE, rounding=ROUNDING_METHODS[method])
    result = quotient * unit
    # Format whole-rupee multiples without an exponent. Fractional multiples keep their precision.
    if unit == unit.to_integral_value():
        return result.quantize(ONE)
    return result


def to_str(value: Decimal) -> str:
    """Canonical string for storage/hashing: no exponent, no trailing zeros beyond what is needed."""
    if value == value.to_integral_value():
        return str(value.quantize(ONE))
    text = format(value.normalize(), "f")
    return text


def pct(rate: Decimal) -> str:
    """0.12 -> '12%', 0.0075 -> '0.75%'."""
    return f"{to_str(rate * HUNDRED)}%"


def inr(value, decimals: int | None = None) -> str:
    """Format a Decimal with Indian digit grouping: 120000 -> '₹1,20,000'."""
    value = d(value)
    negative = value < 0
    value = abs(value)
    if decimals is None:
        decimals = 0 if value == value.to_integral_value() else 2
    quantized = value.quantize(Decimal(1).scaleb(-decimals)) if decimals else value.quantize(ONE)
    text = format(quantized, "f")
    whole, _, frac = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    result = f"₹{whole}" + (f".{frac}" if frac else "")
    return f"-{result}" if negative else result


def num(value, decimals: int = 2) -> str:
    """Plain number for formulas, e.g. 580.645161 -> '580.65'."""
    value = d(value)
    if value == value.to_integral_value():
        return inr(value)[1:] if value >= 0 else "-" + inr(-value)[1:]
    q = value.quantize(Decimal(1).scaleb(-decimals))
    return format(q, "f")
