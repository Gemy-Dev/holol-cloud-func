"""Day-boundary behaviour of get_tasks_by_date_range.

Two bugs lived here, and they pulled in opposite directions:

  * the window returned one day too many, because days_count was added in full
    and the result then extended to end-of-day;
  * a tz-aware targetDate had its offset discarded rather than converted, so a
    task written at Iraq-local midnight (21:00 UTC the previous day) was read
    as belonging to the previous day.

Both are visible in the app: the task screen's date strip is built from the
distinct targetDates of whatever comes back, so an extra day becomes an extra
chip and a shifted task moves to the wrong chip.
"""
from datetime import datetime, timedelta, timezone

import pytest

from modules.config import IRAQ_TIMEZONE
from modules.tasks import get_tasks_by_date_range

TOKEN = {"uid": "user-1"}


def _task(target_date, task_id="t1"):
    return {"targetDate": target_date, "reviewState": "approved", "title": task_id}


def _run(db, date_str, days):
    """Call the handler and return the ids of the tasks it kept."""
    response = get_tasks_by_date_range({"date": date_str, "days": days}, TOKEN, db)
    body = response.get_json() if hasattr(response, "get_json") else response[0].get_json()
    assert body["success"] is True, body
    return [t["title"] for t in body["data"]], body["dateRange"]


class _FakeStream:
    def __init__(self, docs):
        self._docs = docs

    def stream(self):
        return iter(self._docs)

    def where(self, *args, **kwargs):
        return self


class _FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _FakeDb:
    def __init__(self, tasks):
        self._docs = [_FakeDoc(t["title"], t) for t in tasks]

    def collection(self, name):
        assert name == "tasks"
        return _FakeStream(self._docs)


def test_window_covers_exactly_days_count_days():
    """A 3-day window starting on the 10th must end on the 12th, not the 13th."""
    db = _FakeDb([
        _task("2026-08-09", "before"),
        _task("2026-08-10", "day1"),
        _task("2026-08-12", "day3"),
        _task("2026-08-13", "day4_outside"),
    ])

    kept, date_range = _run(db, "2026-08-10", 3)

    assert kept == ["day1", "day3"]
    assert date_range["end"].startswith("2026-08-12")


def test_single_day_window_is_just_that_day():
    db = _FakeDb([_task("2026-08-10", "in"), _task("2026-08-11", "out")])

    kept, _ = _run(db, "2026-08-10", 1)

    assert kept == ["in"]


def test_local_midnight_task_stays_on_its_own_day():
    """The regression: stored UTC is the previous day at 21:00."""
    local_midnight = datetime(2026, 8, 12, 0, 0, tzinfo=IRAQ_TIMEZONE)
    stored = local_midnight.astimezone(timezone.utc)
    assert stored.date() == datetime(2026, 8, 11).date(), "precondition"

    db = _FakeDb([_task(stored, "midnight")])

    # A window covering only the 12th must still find it.
    kept, _ = _run(db, "2026-08-12", 1)
    assert kept == ["midnight"]


def test_local_midnight_task_is_not_pulled_into_the_previous_day():
    local_midnight = datetime(2026, 8, 12, 0, 0, tzinfo=IRAQ_TIMEZONE)
    db = _FakeDb([_task(local_midnight.astimezone(timezone.utc), "midnight")])

    kept, _ = _run(db, "2026-08-11", 1)

    assert kept == []


def test_late_evening_task_stays_on_its_own_day():
    """23:30 Iraq is 20:30 UTC the same day — must not drift forward either."""
    evening = datetime(2026, 8, 12, 23, 30, tzinfo=IRAQ_TIMEZONE)
    db = _FakeDb([_task(evening.astimezone(timezone.utc), "evening")])

    kept, _ = _run(db, "2026-08-12", 1)

    assert kept == ["evening"]


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "not-a-date"],
    ids=["null", "empty", "blank", "garbage"],
)
def test_unusable_target_dates_are_skipped(raw):
    db = _FakeDb([_task(raw, "bad"), _task("2026-08-10", "good")])

    kept, _ = _run(db, "2026-08-10", 1)

    assert kept == ["good"]


def test_iso_string_with_time_is_matched_on_its_day():
    db = _FakeDb([_task("2026-08-10T14:30:00", "iso")])

    kept, _ = _run(db, "2026-08-10", 1)

    assert kept == ["iso"]
