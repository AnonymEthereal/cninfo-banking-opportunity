from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import exchange_calendars
from .models import DateRange


def default_date_range(today: date | None = None) -> DateRange:
    today = today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    end = today - timedelta(days=1)
    calendar = exchange_calendars.get_calendar('XSHG')
    start = calendar.date_to_session(end.isoformat(), direction='previous').date()
    return DateRange(start_date=start, end_date=end)
