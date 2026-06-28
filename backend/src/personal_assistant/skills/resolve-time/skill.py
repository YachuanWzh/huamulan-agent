from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool


@tool
def resolve_current_time(timezone: str = "Asia/Shanghai") -> str:
    """Return the current ISO-8601 date/time in the requested IANA timezone."""
    now = datetime.now(ZoneInfo(timezone))
    return now.isoformat()


TOOLS = [resolve_current_time]
