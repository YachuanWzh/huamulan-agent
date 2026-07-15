#!/usr/bin/env python3
"""Date/time resolution script — core calculation logic.

Can be used standalone:
    python resolve_date.py offset 1          # tomorrow
    python resolve_date.py offset -2         # day before yesterday
    python resolve_date.py weekday Tuesday 1 # next Tuesday
    python resolve_date.py now               # current time

Or imported as a module by skill.py.
"""

import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WEEKDAY_MAP: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3,
    "星期五": 4, "星期六": 5, "星期日": 6, "星期天": 6,
    "周一": 0, "周二": 1, "周三": 2, "周四": 3,
    "周五": 4, "周六": 5, "周日": 6, "周天": 6,
}

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]

# ── Lunar calendar data ──────────────────────────────────────────
# Format: (cny_month, cny_day, month_lengths, leap_month_index)
# cny_month/cny_day: Gregorian date of Chinese New Year
# month_lengths: list of lunar month lengths (29 or 30 days);
#   13 entries when leap month exists; otherwise 12.
# leap_month_index: 1-based index of the leap month (0 = no leap month).
# Data derived from Hong Kong Observatory lunar calendar.
LUNAR_DATA: dict[int, tuple[int, int, list[int], int]] = {
    2024: (2, 10, [30, 29, 30, 29, 30, 29, 30, 30, 29, 30, 29, 30], 0),
    2025: (1, 29, [30, 29, 30, 29, 30, 29, 29, 30, 29, 30, 29, 30, 29], 6),
    2026: (2, 17, [30, 30, 29, 29, 30, 29, 30, 30, 29, 30, 29, 29], 0),
    2027: (2, 6,  [30, 30, 29, 30, 29, 30, 29, 29, 30, 29, 30, 29], 0),
}

LUNAR_MONTH_NAMES = [
    "", "正月", "二月", "三月", "四月", "五月", "六月",
    "七月", "八月", "九月", "十月", "冬月", "腊月",
]

LUNAR_DAY_NAMES = [
    "", "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
]


def now(timezone: str = "Asia/Shanghai") -> datetime:
    """Current datetime in the given timezone."""
    return datetime.now(ZoneInfo(timezone))


def calc_date_by_offset(day_offset: int, timezone: str = "Asia/Shanghai") -> dict[str, str]:
    """Calculate date by day offset from today."""
    today = now(timezone).replace(hour=0, minute=0, second=0, microsecond=0)
    target = today + timedelta(days=day_offset)
    return {
        "date": target.strftime("%Y-%m-%d"),
        "weekday": WEEKDAY_NAMES[target.weekday()],
        "day_offset": day_offset,
        "description": _offset_desc(day_offset),
    }


def calc_date_by_weekday(weekday: str, week_offset: int,
                         timezone: str = "Asia/Shanghai") -> dict[str, str]:
    """Calculate date by target weekday and week offset."""
    key = weekday.lower() if weekday.isascii() else weekday
    target_wd = WEEKDAY_MAP.get(key)
    if target_wd is None:
        raise ValueError(
            f"Unknown weekday: {weekday!r}. Accepted: "
            f"{', '.join(sorted(set(WEEKDAY_MAP.keys())))}"
        )

    today = now(timezone).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today - timedelta(days=today.weekday())
    target = week_start + timedelta(weeks=week_offset, days=target_wd)

    return {
        "date": target.strftime("%Y-%m-%d"),
        "weekday": WEEKDAY_NAMES[target.weekday()],
        "week_offset": week_offset,
        "description": _week_desc(weekday, week_offset),
    }


def current_time(timezone: str = "Asia/Shanghai") -> str:
    """Return the current ISO-8601 date/time string."""
    return now(timezone).isoformat()


# ── Lunar calendar conversion ────────────────────────────────────

def calc_lunar_to_solar(
    lunar_month: int,
    lunar_day: int,
    year: int | None = None,
    timezone: str = "Asia/Shanghai",
) -> dict[str, str]:
    """Convert a lunar calendar date (month, day) to Gregorian date.

    If year is not provided, infers the appropriate lunar year from the
    current date.  Supported years: 2024-2027.
    """
    # Validate month early, before year inference
    if not (1 <= lunar_month <= 12):
        raise ValueError(
            f"lunar month must be 1-12, got {lunar_month}"
        )

    current = now(timezone)
    if year is None:
        year = _infer_lunar_year(current, lunar_month, lunar_day)

    if year not in LUNAR_DATA:
        raise ValueError(
            f"Lunar calendar data not available for year {year}. "
            f"Supported years: {sorted(LUNAR_DATA.keys())}"
        )

    cny_month, cny_day, month_lengths, leap_idx = LUNAR_DATA[year]

    physical_idx = _resolve_lunar_month_index(lunar_month, month_lengths, leap_idx)
    max_day = month_lengths[physical_idx]
    if not (1 <= lunar_day <= max_day):
        raise ValueError(
            f"lunar day must be 1-{max_day} for month {lunar_month}"
            f" in year {year}, got {lunar_day}"
        )

    offset = sum(month_lengths[:physical_idx]) + (lunar_day - 1)
    cny = datetime(year, cny_month, cny_day)
    target = cny + timedelta(days=offset)

    month_name = LUNAR_MONTH_NAMES[lunar_month]
    day_name = LUNAR_DAY_NAMES[lunar_day] if lunar_day <= 30 else f"{lunar_day}日"
    is_leap = lunar_month == leap_idx and leap_idx > 0
    lunar_desc = f"闰{month_name}{day_name}" if is_leap else f"{month_name}{day_name}"

    return {
        "date": target.strftime("%Y-%m-%d"),
        "weekday": WEEKDAY_NAMES[target.weekday()],
        "lunar_month": lunar_month,
        "lunar_day": lunar_day,
        "lunar_year": year,
        "is_leap_month": is_leap,
        "lunar_description": lunar_desc,
        "description": (
            f"{lunar_desc} in {year} is "
            f"{target.strftime('%Y-%m-%d')} ({WEEKDAY_NAMES[target.weekday()]})"
        ),
    }


def calc_solar_to_lunar(
    date_str: str,
    timezone: str = "Asia/Shanghai",
) -> dict[str, str]:
    """Convert a Gregorian date (YYYY-MM-DD) to lunar calendar date.

    Returns the lunar month, day, and year, plus weekday and description.
    Supported years: 2024–2027 (derived from lunar data range).
    """
    try:
        parts = date_str.split("-")
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])
    except (ValueError, IndexError):
        raise ValueError(f"Invalid date format: {date_str!r}. Expected YYYY-MM-DD.")

    target = datetime(year, month, day)

    # Find which lunar year covers this Gregorian date
    lunar_year = _find_lunar_year_for_solar(target)
    if lunar_year is None:
        raise ValueError(
            f"No lunar calendar data for {date_str}. Supported years: "
            f"{min(LUNAR_DATA)}–{max(LUNAR_DATA)}."
        )

    cny_month, cny_day, month_lengths, leap_idx = LUNAR_DATA[lunar_year]
    cny = datetime(lunar_year, cny_month, cny_day)
    offset = (target - cny).days

    if offset < 0:
        raise ValueError(
            f"{date_str} is before Chinese New Year {lunar_year} "
            f"({cny.strftime('%Y-%m-%d')})"
        )

    # Walk through months to find which lunar month and day
    remaining = offset
    lunar_month = 1
    found = False
    is_leap = False

    for i, month_len in enumerate(month_lengths):
        if remaining < month_len:
            lunar_day = remaining + 1
            found = True
            # Convert physical index back to logical lunar month
            if leap_idx > 0:
                if i == leap_idx:
                    # This is the leap month entry itself
                    is_leap = True
                    lunar_month = leap_idx
                elif i > leap_idx:
                    lunar_month = i  # skip leap: physical i → logical i
                else:
                    lunar_month = i + 1
            else:
                lunar_month = i + 1
            break
        remaining -= month_len

    if not found:
        raise ValueError(
            f"Date {date_str} falls outside lunar year {lunar_year} range"
        )

    month_name = LUNAR_MONTH_NAMES[lunar_month]
    day_name = LUNAR_DAY_NAMES[lunar_day] if lunar_day <= 30 else f"{lunar_day}日"
    lunar_desc = f"闰{month_name}{day_name}" if is_leap else f"{month_name}{day_name}"

    return {
        "date": date_str,
        "weekday": WEEKDAY_NAMES[target.weekday()],
        "lunar_month": lunar_month,
        "lunar_day": lunar_day,
        "lunar_year": lunar_year,
        "is_leap_month": is_leap,
        "lunar_description": lunar_desc,
        "description": (
            f"{date_str} ({WEEKDAY_NAMES[target.weekday()]}) is "
            f"{lunar_desc} in lunar year {lunar_year}"
        ),
    }


def _find_lunar_year_for_solar(target: datetime) -> int | None:
    """Find which lunar year (Gregorian year) a given Gregorian date belongs to."""
    for year in sorted(LUNAR_DATA):
        cny_m, cny_d, month_lengths, leap_idx = LUNAR_DATA[year]
        cny = datetime(year, cny_m, cny_d)
        # Calculate the last day of this lunar year
        total_days = sum(month_lengths)
        end_of_lunar_year = cny + timedelta(days=total_days - 1)
        if cny <= target <= end_of_lunar_year:
            return year
    return None


def _infer_lunar_year(
    current: datetime,
    lunar_month: int,
    lunar_day: int,
) -> int:
    """Return the Gregorian year to use for a lunar date query.

    Defaults to the current Gregorian year.  When we are early in the year
    (before CNY) and the user asks about a late lunar month (e.g. 腊月),
    the date belongs to the *previous* Gregorian year's lunar calendar.
    """
    year = current.year
    if year not in LUNAR_DATA:
        supported = [y for y in LUNAR_DATA if y <= year]
        return max(supported) if supported else min(LUNAR_DATA)

    cny_month, cny_day, month_lengths, leap_idx = LUNAR_DATA[year]
    cny = datetime(year, cny_month, cny_day)
    current_naive = current.replace(tzinfo=None)

    # If we are before this year's CNY, late-month queries (>=10) may
    # actually belong to the previous year's lunar calendar.
    if current_naive < cny and lunar_month >= 10 and year - 1 in LUNAR_DATA:
        return year - 1

    return year


def _resolve_lunar_month_index(
    month: int,
    month_lengths: list[int],
    leap_idx: int,
) -> int:
    """Convert a 1-based logical lunar month to the index in month_lengths.

    When there's a leap month, month_lengths has 13 entries, and months
    after the leap get their index bumped by 1.
    """
    if leap_idx == 0:
        return month - 1
    if month <= leap_idx:
        return month - 1
    return month  # skip over the leap month entry


# ── Helpers ────────────────────────────────────────────────────

def _offset_desc(offset: int) -> str:
    return {0: "today", 1: "tomorrow", -1: "yesterday",
            2: "day after tomorrow", -2: "day before yesterday"
            }.get(offset, f"{offset:+d} days")


def _week_desc(weekday: str, week_offset: int) -> str:
    return {0: f"this week's {weekday}", 1: f"next {weekday}",
            2: f"the {weekday} after next", -1: f"last {weekday}"
            }.get(week_offset, f"{weekday} (week_offset={week_offset})")


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: resolve_date.py [offset N | weekday NAME OFFSET | lunar M D [YEAR] | solar YYYY-MM-DD | now]",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "now":
        tz = sys.argv[2] if len(sys.argv) > 2 else "Asia/Shanghai"
        print(current_time(tz))
    elif cmd == "offset":
        offset = int(sys.argv[2])
        tz = sys.argv[3] if len(sys.argv) > 3 else "Asia/Shanghai"
        print(json.dumps(calc_date_by_offset(offset, tz), ensure_ascii=False, indent=2))
    elif cmd == "weekday":
        wd = sys.argv[2]
        wo = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        tz = sys.argv[4] if len(sys.argv) > 4 else "Asia/Shanghai"
        print(json.dumps(calc_date_by_weekday(wd, wo, tz), ensure_ascii=False, indent=2))
    elif cmd == "lunar":
        lunar_month = int(sys.argv[2])
        lunar_day = int(sys.argv[3])
        yr = int(sys.argv[4]) if len(sys.argv) > 4 else None
        tz = sys.argv[5] if len(sys.argv) > 5 else "Asia/Shanghai"
        result = calc_lunar_to_solar(lunar_month, lunar_day, yr, tz)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif cmd == "solar":
        date_str = sys.argv[2]
        tz = sys.argv[3] if len(sys.argv) > 3 else "Asia/Shanghai"
        result = calc_solar_to_lunar(date_str, tz)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
