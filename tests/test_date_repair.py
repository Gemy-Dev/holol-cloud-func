"""The nightly date repair rewrites every stored date into the R1 shape."""
from datetime import datetime, timezone

from modules import date_repair as dr


def test_contract_values_are_left_alone():
    assert dr.repaired("2026-09-04T10:30:00.000") == "2026-09-04T10:30:00.000"
    assert dr.repaired("2026-09-04T10:30:00.006843") == "2026-09-04T10:30:00.006843"
    assert dr.repaired(None) is None
    assert dr.repaired("") == ""


def test_every_legacy_form_is_rewritten_in_iraq_time():
    expected = "2026-09-04T10:30:00.000"
    for legacy in (
        "2026-09-04T07:30:00.000Z",
        "2026-09-04T07:30:00+00:00",
        "2026-09-04 10:30:00.000",
        "2026-09-04T10:30:00",
        "Fri, 04 Sep 2026 07:30:00 GMT",
        datetime(2026, 9, 4, 7, 30, tzinfo=timezone.utc),
    ):
        assert dr.repaired(legacy) == expected, legacy


def test_a_bare_day_becomes_its_midnight():
    assert dr.repaired("2026-09-04") == "2026-09-04T00:00:00.000"
    assert dr.repaired("04-09-2026") == "2026-09-04T00:00:00.000"


def test_an_unreadable_value_is_reported_not_guessed():
    assert dr.repaired("tomorrow") is None
    assert dr.repaired(12) is None


def test_only_date_fields_are_touched():
    changes, unreadable = dr.changes_for("tasks", {
        "targetDate": "2026-09-04 10:30:00.000",
        "createdAt": "2026-09-04T10:30:00.000",
        "deletedAt": "nonsense",
        "name": "2026-09-04 10:30:00.000",
        "status": "pending",
    })
    assert changes == {"targetDate": "2026-09-04T10:30:00.000"}
    assert unreadable == ["deletedAt"]


def test_the_report_timestamp_is_exempt():
    stamp = datetime(2026, 9, 4, tzinfo=timezone.utc)
    changes, _ = dr.changes_for("reports", {"serverCreatedAt": stamp, "createdAt": stamp})
    assert list(changes) == ["createdAt"]


def _writer(db, refuse=()):
    written = []

    def write(_db, snapshot, changes):
        if snapshot.id in refuse:
            return False
        written.append((snapshot.id, changes))
        snapshot.reference.update(changes)
        return True
    return write, written


def test_a_run_repairs_and_reports(db):
    db._put("tasks", "t1", {"targetDate": "2026-09-04T07:30:00.000Z"})
    db._put("tasks", "t2", {"targetDate": "2026-09-04T10:30:00.000"})
    db._put("clients", "c1", {"createdAt": "Fri, 04 Sep 2026 07:30:00 GMT"})
    write, written = _writer(db)

    report = dr.repair_stored_dates(db, write=write)

    assert report["repaired"] == 2
    assert report["byCollection"]["tasks"] == 1
    assert db._get_all("tasks")["t1"]["targetDate"] == "2026-09-04T10:30:00.000"
    assert db._get_all("clients")["c1"]["createdAt"] == "2026-09-04T10:30:00.000"
    assert [doc_id for doc_id, _ in written] == ["t1", "c1"]


def test_a_document_changed_meanwhile_waits_for_the_next_night(db):
    db._put("tasks", "t1", {"targetDate": "2026-09-04T07:30:00.000Z"})
    write, _ = _writer(db, refuse={"t1"})

    report = dr.repair_stored_dates(db, write=write)

    assert report["repaired"] == 0 and report["skippedChanged"] == 1
    assert db._get_all("tasks")["t1"]["targetDate"] == "2026-09-04T07:30:00.000Z"


def test_a_dry_run_writes_nothing(db):
    db._put("tasks", "t1", {"targetDate": "2026-09-04T07:30:00.000Z"})
    write, written = _writer(db)

    report = dr.repair_stored_dates(db, dry_run=True, write=write)

    assert report["repaired"] == 1 and written == []
    assert db._get_all("tasks")["t1"]["targetDate"] == "2026-09-04T07:30:00.000Z"
