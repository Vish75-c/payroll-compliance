"""Pay periods (calendar months) and Indian financial years (April to March)."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True, order=True)
class Period:
    year: int
    month: int

    @classmethod
    def parse(cls, value: "str | Period") -> "Period":
        if isinstance(value, Period):
            return value
        match = _PERIOD_RE.match(str(value).strip())
        if not match:
            raise ValueError(f"period must look like 2025-07, got {value!r}")
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"invalid month in period {value!r}")
        return cls(year, month)

    @classmethod
    def of(cls, day: date) -> "Period":
        return cls(day.year, day.month)

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def start(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def end(self) -> date:
        return date(self.year, self.month, self.days)

    @property
    def days(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]

    @property
    def label(self) -> str:
        return f"{calendar.month_name[self.month]} {self.year}"

    @property
    def short_label(self) -> str:
        return f"{calendar.month_abbr[self.month]} {self.year}"

    def next(self) -> "Period":
        return Period(self.year + (self.month == 12), 1 if self.month == 12 else self.month + 1)

    def prev(self) -> "Period":
        return Period(self.year - (self.month == 1), 12 if self.month == 1 else self.month - 1)

    def add(self, months: int) -> "Period":
        index = self.year * 12 + (self.month - 1) + months
        return Period(index // 12, index % 12 + 1)

    def months_until(self, other: "Period") -> int:
        return (other.year * 12 + other.month) - (self.year * 12 + self.month)

    # Financial year helpers -------------------------------------------------
    @property
    def fy_start_year(self) -> int:
        return self.year if self.month >= 4 else self.year - 1

    @property
    def fy_label(self) -> str:
        start = self.fy_start_year
        return f"FY {start}-{str(start + 1)[-2:]}"

    @property
    def fy_index(self) -> int:
        """April = 1 ... March = 12."""
        return (self.month - 4) % 12 + 1

    @property
    def fy_first(self) -> "Period":
        return Period(self.fy_start_year, 4)

    @property
    def fy_last(self) -> "Period":
        return Period(self.fy_start_year + 1, 3)

    def same_fy(self, other: "Period") -> bool:
        return self.fy_start_year == other.fy_start_year

    @property
    def esi_contribution_start(self) -> "Period":
        """ESI contribution periods begin in April and October."""
        return self.fy_first if self.fy_index <= 6 else self.fy_first.add(6)


def parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def daterange(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def overlap_days(start: date, end: date, period: Period) -> int:
    lo = max(start, period.start)
    hi = min(end, period.end)
    return (hi - lo).days + 1 if hi >= lo else 0
