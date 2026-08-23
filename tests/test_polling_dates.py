from datetime import datetime
from zoneinfo import ZoneInfo

from app.polling import schedule_service_dates


def test_polling_uses_current_and_previous_service_dates_only():
    eastern = ZoneInfo("America/New_York")
    dates = schedule_service_dates(datetime(2026, 8, 23, 23, 55, tzinfo=eastern))
    assert dates == {"2026-08-22", "2026-08-23"}
    assert "2026-08-24" not in dates

