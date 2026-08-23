from datetime import UTC, datetime, timedelta

from app.predictions.engine import format_minutes, merge_bus, merge_rail, parse_wmata_time


def profile(**changes):
    base = {"arrival_count": 3, "minute_format": "m", "near_format": "DUE",
            "scheduled_format": "superscript", "stale_format": "?", "stale_threshold": 120}
    return {**base, **changes}


def entry(**changes):
    return {"route": "S2", "direction": "0", "destination": "Federal Triangle", **changes}


def test_minute_formats():
    assert format_minutes(7, profile()) == "7m"
    assert format_minutes(7, profile(minute_format="min")) == "7 min"
    assert format_minutes(7, profile(minute_format="minutes")) == "7 minutes"


def test_near_formats_and_actual_seconds():
    assert format_minutes(.5, profile()) == "DUE"
    assert format_minutes(.7, profile(near_format="actual")) == "42s"
    assert format_minutes(.2, profile(near_format="NOW")) == "NOW"


def test_scheduled_and_stale_markers():
    assert format_minutes(12, profile(), "scheduled") == "12mˢ"
    assert format_minutes(12, profile(scheduled_format="(s)", minute_format="min"), "scheduled") == "12 min (s)"
    assert format_minutes(8, profile(stale_format="﹖"), "stale") == "8m﹖"


def test_fresh_live_beats_matching_schedule():
    now = datetime(2026, 8, 23, 16, tzinfo=UTC)
    predictions = [{"RouteID":"S2","DirectionNum":"0","DirectionText":"Federal Triangle","Minutes":3,"TripID":"t1","VehicleID":"v1"}]
    positions = [{"RouteID":"S2","TripID":"t1","VehicleID":"v1","DateTime":now.isoformat()}]
    schedules = [{"RouteID":"S2","DirectionNum":"0","TripHeadsign":"Federal Triangle","ScheduleTime":(now+timedelta(minutes=4)).isoformat(),"TripID":"t1"}]
    result = merge_bus(entry(), profile(), predictions, schedules, positions, now=now)
    assert [(x["state"], x["trip_id"]) for x in result] == [("live", "t1")]


def test_stale_gps_classification():
    now = datetime(2026, 8, 23, 16, tzinfo=UTC)
    predictions = [{"RouteID":"S2","DirectionNum":"0","DirectionText":"Federal Triangle","Minutes":8,"TripID":"t1","VehicleID":"v1"}]
    positions = [{"TripID":"t1","VehicleID":"v1","DateTime":(now-timedelta(seconds=121)).isoformat()}]
    result = merge_bus(entry(), profile(), predictions, [], positions, now=now)
    assert result[0]["state"] == "stale" and result[0]["display"] == "8m?"


def test_schedule_fallback_and_past_removal():
    now = datetime(2026, 8, 23, 16, tzinfo=UTC)
    schedules = [
        {"RouteID":"S2","DirectionNum":"0","TripHeadsign":"Federal Triangle","ScheduleTime":(now-timedelta(minutes=3)).isoformat(),"TripID":"old"},
        {"RouteID":"S2","DirectionNum":"0","TripHeadsign":"Federal Triangle","ScheduleTime":(now+timedelta(minutes=12)).isoformat(),"TripID":"next"},
    ]
    result = merge_bus(entry(), profile(), [], schedules, [], now=now)
    assert [x["trip_id"] for x in result] == ["next"]
    assert result[0]["state"] == "scheduled"


def test_route_direction_and_headsign_filters():
    predictions = [
        {"RouteID":"S2","DirectionNum":"0","DirectionText":"Federal Triangle","Minutes":2,"TripID":"yes"},
        {"RouteID":"S9","DirectionNum":"0","DirectionText":"Federal Triangle","Minutes":3,"TripID":"route"},
        {"RouteID":"S2","DirectionNum":"1","DirectionText":"Federal Triangle","Minutes":4,"TripID":"dir"},
        {"RouteID":"S2","DirectionNum":"0","DirectionText":"Silver Spring","Minutes":5,"TripID":"head"},
    ]
    assert [x["trip_id"] for x in merge_bus(entry(), profile(), predictions, [], [])] == ["yes"]


def test_passed_trip_removed():
    predictions = [{"RouteID":"S2","DirectionNum":"0","DirectionText":"Federal Triangle","Minutes":0,"TripID":"gone"}]
    assert merge_bus(entry(), profile(), predictions, [], [], passed_trip_ids={"gone"}) == []


def test_duplicate_vehicle_is_deduplicated():
    predictions = [
        {"RouteID":"S2","DirectionNum":"0","DirectionText":"Federal Triangle","Minutes":4,"VehicleID":"v1"},
        {"RouteID":"S2","DirectionNum":"0","DirectionText":"Federal Triangle","Minutes":5,"VehicleID":"v1"},
    ]
    assert len(merge_bus(entry(), profile(), predictions, [], [])) == 1


def test_timezone_and_midnight_parsing():
    parsed = parse_wmata_time("2026-11-01T01:30:00-04:00")
    assert parsed.tzinfo == UTC and parsed.hour == 5


def test_rail_status_and_filtering():
    trains = [{"Line":"RD","Group":"1","DestinationName":"Glenmont","Min":"ARR"},
              {"Line":"BL","Group":"1","DestinationName":"Franconia","Min":"2"}]
    result = merge_rail({"route":"RD","direction":"1","destination":"Glenmont"}, profile(), trains)
    assert len(result) == 1 and result[0]["display"] == "DUE"


def test_rail_cache_can_be_stale():
    trains = [{"Line":"RD","Group":"1","DestinationName":"Glenmont","Min":"8"}]
    result = merge_rail({"route":"RD","direction":"1","destination":"Glenmont"}, profile(), trains, cache_age=121)
    assert result[0]["state"] == "stale" and result[0]["display"] == "8m?"
