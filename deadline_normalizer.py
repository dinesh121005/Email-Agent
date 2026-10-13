from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import re
from typing import Literal
import zoneinfo

# Known timezone mapping (supporting standard abbreviations and IANA)
COMMON_TZ_OFFSETS: dict[str, timezone] = {
    "utc": timezone.utc,
    "gmt": timezone.utc,
    "est": timezone(timedelta(hours=-5)),
    "edt": timezone(timedelta(hours=-4)),
    "cst": timezone(timedelta(hours=-6)),
    "cdt": timezone(timedelta(hours=-5)),
    "mst": timezone(timedelta(hours=-7)),
    "mdt": timezone(timedelta(hours=-6)),
    "pst": timezone(timedelta(hours=-8)),
    "pdt": timezone(timedelta(hours=-7)),
}

WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

MONTHS: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

ResolutionStatus = Literal["resolved", "empty", "ambiguous", "unresolvable"]


@dataclass(frozen=True)
class DeadlineResolution:
    dt: datetime | None
    status: ResolutionStatus
    is_confident: bool
    notes: str = ""


def _extract_tz(text: str, default_tz: timezone | zoneinfo.ZoneInfo) -> tuple[timezone | zoneinfo.ZoneInfo, str]:
    """Find and remove timezone specifier from text, returning (tz, stripped_text)."""
    lower = text.lower()
    for tz_name, tz_obj in COMMON_TZ_OFFSETS.items():
        pattern = r"\b" + re.escape(tz_name) + r"\b"
        if re.search(pattern, lower):
            cleaned = re.sub(pattern, "", lower, flags=re.IGNORECASE).strip()
            return tz_obj, cleaned

    return default_tz, text


def _extract_time(text: str) -> tuple[int, int, int, str]:
    """Extract hour, minute, second, and return stripped text.
    
    Requires either a colon format (5:00) or an am/pm indicator (5 pm)
    to prevent matching day-of-month numbers like '25' as hours.
    """
    lower = text.lower()
    if "midnight" in lower:
        cleaned = re.sub(r"\bmidnight\b", "", lower).strip()
        return 23, 59, 0, cleaned
    if "noon" in lower:
        cleaned = re.sub(r"\bnoon\b", "", lower).strip()
        return 12, 0, 0, cleaned

    # 1. Matches with colon: e.g. 5:00, 23:59, 5:00 pm
    match = re.search(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?\b", lower)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        second = int(match.group(3) or 0)
        meridiem = match.group(4)
    else:
        # 2. Matches bare hour with am/pm: e.g. 5 pm, 11 am
        match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", lower)
        if match:
            hour = int(match.group(1))
            minute = 0
            second = 0
            meridiem = match.group(2)
        else:
            # Default to end of day if no specific time is present
            return 23, 59, 0, text

    if meridiem:
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0

    cleaned = lower[:match.start()] + " " + lower[match.end():]
    return hour, minute, second, cleaned.strip()


def resolve_deadline_detailed(
    deadline_text: str | None,
    received_at: datetime,
) -> DeadlineResolution:
    """Resolve deadline string into a structured DeadlineResolution.

    Handles explicit dates, relative phrases ('tonight', 'tomorrow', 'this Friday'),
    bare times, and time zone discrepancies relative to received_at.
    """
    if not deadline_text or not deadline_text.strip():
        return DeadlineResolution(dt=None, status="empty", is_confident=True, notes="No deadline text")

    cleaned = deadline_text.strip()

    # 1. Direct ISO 8601 parsing
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=received_at.tzinfo or timezone.utc)
        return DeadlineResolution(dt=dt, status="resolved", is_confident=True, notes="ISO 8601 format")
    except ValueError:
        pass

    # Extract target timezone (or default to received_at's timezone)
    default_tz = received_at.tzinfo or timezone.utc
    target_tz, text_without_tz = _extract_tz(cleaned, default_tz)

    # Convert received_at into the target timezone so date comparisons align
    received_in_target_tz = received_at.astimezone(target_tz)
    base_date = received_in_target_tz.date()

    # Extract time component
    hour, minute, second, remaining_text = _extract_time(text_without_tz)

    # 2. Check for explicit month and day (e.g., 'October 20', 'Oct 20')
    month_match = re.search(r"\b([a-zA-Z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", remaining_text)
    if month_match:
        m_name = month_match.group(1).lower()
        if m_name in MONTHS:
            month = MONTHS[m_name]
            day = int(month_match.group(2))
            year = base_date.year
            try:
                candidate = datetime(year, month, day, hour, minute, second, tzinfo=target_tz)
                # If date already passed in current year by more than 3 months, assume next year
                if (candidate - received_in_target_tz).days < -90:
                    candidate = datetime(year + 1, month, day, hour, minute, second, tzinfo=target_tz)
                return DeadlineResolution(dt=candidate, status="resolved", is_confident=True, notes="Explicit month/day")
            except ValueError:
                return DeadlineResolution(dt=None, status="unresolvable", is_confident=False, notes="Invalid calendar date")

    # 3. Relative day expressions
    lower = remaining_text.lower()
    has_tomorrow = "tomorrow" in lower
    has_tonight = "tonight" in lower or "today" in lower

    matched_weekday = None
    for name, idx in WEEKDAYS.items():
        if re.search(r"\b" + name + r"\b", lower):
            matched_weekday = idx
            break

    if has_tomorrow:
        target_date = base_date + timedelta(days=1)
    elif has_tonight:
        target_date = base_date
    elif matched_weekday is not None:
        current_weekday = received_in_target_tz.weekday()
        days_ahead = (matched_weekday - current_weekday) % 7
        if days_ahead == 0:
            # Same weekday: check if the deadline time has already passed today
            target_time = time(hour, minute, second)
            if target_time <= received_in_target_tz.time():
                # E.g., received Friday 5 PM, deadline 'Friday 9 AM' means next Friday or overdue
                days_ahead = 7 if "next" in lower else 0
        target_date = base_date + timedelta(days=days_ahead)
    else:
        # Bare time without day anchor (e.g. "23:59 UTC" or "5 PM")
        target_time = time(hour, minute, second)
        if target_time >= received_in_target_tz.time():
            # Later today in target timezone
            target_date = base_date
        else:
            # Earlier than received_at in target timezone -> ambiguous or overdue
            return DeadlineResolution(
                dt=datetime(base_date.year, base_date.month, base_date.day, hour, minute, second, tzinfo=target_tz),
                status="ambiguous",
                is_confident=False,
                notes="Bare time earlier than received_at",
            )

    resolved_dt = datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=hour,
        minute=minute,
        second=second,
        tzinfo=target_tz,
    )

    return DeadlineResolution(dt=resolved_dt, status="resolved", is_confident=True, notes="Relative date/time resolved")


def normalize_deadline(
    deadline_text: str | None,
    received_at: datetime,
) -> datetime | None:
    """Normalize relative or raw deadline text to a timezone-aware datetime.

    Returns datetime if confidently resolved, or None if empty or unresolvable.
    """
    resolution = resolve_deadline_detailed(deadline_text, received_at)
    return resolution.dt if resolution.is_confident else None
