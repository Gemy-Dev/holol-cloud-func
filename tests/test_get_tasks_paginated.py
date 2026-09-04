"""Paging and filtering behaviour of get_tasks_paginated.

The handler already loads a plan's whole task set into memory on every call, so
slicing 20 tasks off it made the app pay one full scan per page. The app's plan
screen filters client-side (multi-select panel, options built from the data), and
a filter that matched nothing on the loaded page left the user stuck: no
scrollable meant no way to ask for the next page. Callers can now send
`pageSize <= 0` to take the plan in one response, capped so an unexpectedly large
plan still paginates.

Also covered: targetDateFilter used to be dropped as soon as any other filter was
set, which silently broke the "tasks still needing a date" entry point.
"""
from datetime import datetime, timezone

import pytest

from conftest import make_task, seed_db
from modules import tasks as tasks_module
from modules.tasks import get_tasks_paginated


def _run(db, **payload):
    """Call the handler and return its decoded body."""
    payload.setdefault("planId", "plan1")
    payload.setdefault("taskType", "planned")
    response = get_tasks_paginated(payload, db)
    body = response[0].get_json() if isinstance(response, tuple) else response.get_json()
    return body


def _ids(body):
    return [t["id"] for t in body["tasks"]]


def _seed_tasks(db, count, **overrides):
    """Seed `count` planned tasks named t00, t01, … on plan1."""
    tasks = []
    for i in range(count):
        task = make_task(f"t{i:02d}")
        task.update(overrides)
        tasks.append(task)
    seed_db(db, tasks=tasks)
    return tasks


def test_default_page_size_returns_one_page(db):
    """Without an explicit pageSize the caller still gets the usual 20."""
    _seed_tasks(db, 25)

    body = _run(db)

    assert len(body["tasks"]) == 20
    assert body["hasMore"] is True
    assert body["total"] == 25


def test_page_size_zero_returns_the_whole_plan(db):
    """pageSize <= 0 means "the whole plan", so a client-side filter sees it all."""
    _seed_tasks(db, 25)

    body = _run(db, pageSize=0)

    assert len(body["tasks"]) == 25
    assert body["hasMore"] is False
    assert body["total"] == 25


def test_fetch_all_is_capped_and_keeps_paginating(db, monkeypatch):
    """A plan larger than the cap is truncated, not dropped: the cursor still works."""
    monkeypatch.setattr(tasks_module, "MAX_TASKS_PER_RESPONSE", 10)
    _seed_tasks(db, 25)

    first = _run(db, pageSize=0)

    assert len(first["tasks"]) == 10
    assert first["hasMore"] is True
    assert first["total"] == 25

    second = _run(db, pageSize=0, lastDocument=first["lastDocument"])

    assert len(second["tasks"]) == 10
    # The cursor resumes where the previous response stopped, no overlap.
    assert set(_ids(first)).isdisjoint(_ids(second))


def test_explicit_page_size_is_clamped_to_the_cap(db, monkeypatch):
    """An oversized pageSize cannot be used to bypass the response ceiling."""
    monkeypatch.setattr(tasks_module, "MAX_TASKS_PER_RESPONSE", 10)
    _seed_tasks(db, 25)

    body = _run(db, pageSize=10_000)

    assert len(body["tasks"]) == 10
    assert body["hasMore"] is True


def test_non_numeric_page_size_falls_back_to_the_default(db):
    _seed_tasks(db, 25)

    body = _run(db, pageSize="lots")

    assert len(body["tasks"]) == 20


def test_target_date_filter_applies_alongside_another_filter(db):
    """Regression: targetDateFilter used to be ignored once any filter was set.

    The plan screen opens with `withoutDate` from the "needs a date" card, so
    dropping it there showed dated tasks the screen exists to exclude.
    """
    dated = make_task("dated", client_id="client1")
    dated["targetDate"] = "2026-08-20T00:00:00"
    dateless = make_task("dateless", client_id="client1")
    dateless["targetDate"] = None
    other_client = make_task("other", client_id="client2")
    other_client["targetDate"] = None
    seed_db(db, tasks=[dated, dateless, other_client])

    body = _run(
        db,
        pageSize=0,
        targetDateFilter="withoutDate",
        filterClientId="client1",
    )

    assert _ids(body) == ["dateless"]
    assert body["total"] == 1


def test_target_date_filter_with_date_applies_alongside_another_filter(db):
    dated = make_task("dated", client_id="client1")
    dated["targetDate"] = "2026-08-20T00:00:00"
    dateless = make_task("dateless", client_id="client1")
    dateless["targetDate"] = None
    seed_db(db, tasks=[dated, dateless])

    body = _run(db, pageSize=0, targetDateFilter="withDate", filterClientId="client1")

    assert _ids(body) == ["dated"]


def test_soft_deleted_tasks_are_never_returned(db):
    kept = make_task("kept")
    removed = make_task("removed", review_state="deleted")
    seed_db(db, tasks=[kept, removed])

    body = _run(db, pageSize=0)

    assert _ids(body) == ["kept"]
    assert body["total"] == 1


def test_tasks_of_other_plans_are_not_returned(db):
    mine = make_task("mine", plan_id="plan1")
    theirs = make_task("theirs", plan_id="plan2")
    seed_db(db, tasks=[mine, theirs])

    body = _run(db, pageSize=0)

    assert _ids(body) == ["mine"]


def test_mixed_timezone_created_at_still_sorts(db):
    """Regression: the sort used to die on a plan holding both date shapes.

    A task this function creates carries a Firestore timestamp (tz-aware UTC);
    a task the app creates carries Dart's `toIso8601String()` of a local time,
    which has no offset. Comparing the two raises TypeError, and the 500 that
    followed reached the user as an empty plan — every filter, every page.
    """
    aware = make_task("aware")
    aware["createdAt"] = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    naive = make_task("naive")
    naive["createdAt"] = "2026-08-12T09:00:00.000"
    undated = make_task("undated")
    undated["createdAt"] = None
    seed_db(db, tasks=[aware, naive, undated])

    body = _run(db, pageSize=0)

    # Newest first, and the one with no usable date sinks to the bottom.
    assert _ids(body) == ["naive", "aware", "undated"]


def test_naive_created_at_is_read_as_iraq_local_time(db):
    """The app writes local time, so a naive value is +03:00, not UTC.

    Read as UTC, a task the app created at 01:00 Iraq time would outrank one
    the function stamped at 23:00 UTC the evening before, which is later.
    """
    server_side = make_task("server")
    server_side["createdAt"] = datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)
    app_side = make_task("app")
    app_side["createdAt"] = "2026-08-12T01:00:00.000"  # 22:00 UTC on the 11th
    seed_db(db, tasks=[server_side, app_side])

    body = _run(db, pageSize=0)

    assert _ids(body) == ["server", "app"]


def test_plan_id_is_required(db):
    response = get_tasks_paginated({"pageSize": 0}, db)

    assert response[1] == 400
    assert response[0].get_json()["success"] is False
