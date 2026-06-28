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
        print("Usage: resolve_date.py [offset N | weekday NAME OFFSET | now]", file=sys.stderr)
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
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
