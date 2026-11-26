"""Turn text read from a screen or a spreadsheet cell into typed values. Deterministic; no AI."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from payroll_engine.periods import Period
from payroll_engine.states import state_code

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MULTIPLIERS = {"k": Decimal(1000), "thousand": Decimal(1000), "l": Decimal(100000), "lac": Decimal(100000),
                "lakh": Decimal(100000), "lakhs": Decimal(100000), "lacs": Decimal(100000),
                "cr": Decimal(10000000), "crore": Decimal(10000000), "crores": Decimal(10000000)}


class ValueError_(ValueError):
    pass


def is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in ("", "-", "—", "–", "N/A", "n/a", "NA", "None", "null", "Not set", "not set"))


def parse_amount(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError_(f"not an amount: {value!r}")
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")") or text.startswith("-")
    text = text.strip("()").lstrip("-").strip()
    text = re.sub(r"(?i)^(₹|rs\.?|inr)\s*", "", text)
    text = re.sub(r"(?i)\s*(/-|/month|per month|p\.?m\.?|/mo)$", "", text)
    text = text.replace(",", "").replace("₹", "").strip()
    match = re.fullmatch(r"(?i)(\d+(?:\.\d+)?)\s*([a-z]+)?", text)
    if not match:
        raise ValueError_(f"not an amount: {value!r}")
    number = Decimal(match.group(1))
    suffix = (match.group(2) or "").lower()
    if suffix:
        if suffix not in _MULTIPLIERS:
            raise ValueError_(f"not an amount: {value!r}")
        number *= _MULTIPLIERS[suffix]
    return -number if negative else number


def parse_number(value) -> Decimal:
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    text = str(value).strip().lower()
    text = re.sub(r"\s*(days?|d)$", "", text).strip()
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise ValueError_(f"not a number: {value!r}") from None


def parse_date(value, order: str = "DMY") -> date:
    """Read a date. ``order`` decides ambiguous numeric dates: DMY (Indian default) or MDY."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 20000 < value < 80000:
        # Excel serial date
        from datetime import timedelta
        return date(1899, 12, 30) + timedelta(days=int(value))
    text = str(value).strip()
    text = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\s*(am|pm|AM|PM)?$", "", text)  # drop a time part
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:T.*)?", text)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", text)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        y = y + 2000 if y < 100 else y
        day, month = (a, b) if order == "DMY" else (b, a)
        return date(y, month, day)
    cleaned = text.replace(",", " ").replace(".", " ").replace("-", " ")
    parts = cleaned.split()
    if len(parts) == 3:
        p0, p1, p2 = parts
        if p1.lower() in MONTHS and p0.isdigit() and p2.isdigit():  # 1 May 2025 / 01-May-2025
            y = int(p2)
            return date(y + 2000 if y < 100 else y, MONTHS[p1.lower()], int(p0))
        if p0.lower() in MONTHS and p1.isdigit() and p2.isdigit():  # May 1 2025 / Jul. 14, 2025
            y = int(p2)
            return date(y + 2000 if y < 100 else y, MONTHS[p0.lower()], int(p1))
    raise ValueError_(f"not a date: {value!r}")


def parse_period(value) -> Period:
    if isinstance(value, (date, datetime)):
        return Period(value.year, value.month)
    text = str(value).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
    if m:
        return Period(int(m[1]), int(m[2]))
    m = re.fullmatch(r"(\d{1,2})[/\-](\d{4})", text)
    if m:
        return Period(int(m[2]), int(m[1]))
    parts = text.replace(",", " ").replace("-", " ").replace("'", " ").split()
    if len(parts) == 2 and parts[0].lower() in MONTHS and parts[1].isdigit():
        y = int(parts[1])
        return Period(y + 2000 if y < 100 else y, MONTHS[parts[0].lower()])
    try:
        d = parse_date(value)
        return Period(d.year, d.month)
    except ValueError_:
        raise ValueError_(f"not a month: {value!r}") from None


_TRUE = {"yes", "y", "true", "1", "applicable", "member", "on", "✓", "✔"}
_FALSE = {"no", "n", "false", "0", "not applicable", "na", "off", "exempt", "✗"}


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError_(f"not yes/no: {value!r}")


def parse_gender(value) -> str:
    text = str(value).strip().lower()
    if text in ("m", "male", "man"):
        return "M"
    if text in ("f", "female", "woman"):
        return "F"
    if text:
        return "O"
    return ""


def parse_state(value) -> str:
    code = state_code(value)
    if not code:
        # "Bengaluru, Karnataka" -> try each part
        for part in re.split(r"[,/|-]", str(value)):
            code = state_code(part.strip())
            if code:
                break
    if not code:
        raise ValueError_(f"unknown state: {value!r}")
    return code


def normalize_text(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


UNPAID_KEYWORDS = ("loss of pay", "lop", "unpaid", "without pay", "lwp", "leave without pay", "no pay")
APPROVED_KEYWORDS = ("approved", "accepted", "validated", "sanctioned", "granted", "taken", "availed")
NOT_APPROVED_KEYWORDS = ("reject", "cancel", "pending", "requested", "draft", "withdrawn", "declined", "refused")


def guess_unpaid(leave_type: str) -> bool:
    text = leave_type.lower()
    return any(re.search(rf"\b{re.escape(k)}\b", text) for k in UNPAID_KEYWORDS)


def guess_approved(status: str) -> bool:
    text = status.lower()
    if any(k in text for k in NOT_APPROVED_KEYWORDS):
        return False
    return any(k in text for k in APPROVED_KEYWORDS)
