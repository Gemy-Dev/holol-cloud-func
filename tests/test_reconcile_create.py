"""Tests for US3: create missing tasks (R5) via reconcile_client_tasks."""
import pytest
from unittest.mock import patch

from tests.conftest import (
    FakeFirestore,
    make_client,
    make_influencer_doctor,
    make_plan,
    make_product,
    make_task,
    seed_db,
)
from modules.tasks import reconcile_client_tasks


def _call(db, client_data):
    with patch("modules.tasks.firestore") as mock_fs:
        mock_fs.SERVER_TIMESTAMP = "SERVER_TIMESTAMP"
        mock_fs.ArrayUnion = lambda x: x
        response = reconcile_client_tasks({"client": client_data}, db)
    if isinstance(response, tuple):
        resp_obj, status = response
    else:
        resp_obj, status = response, 200
    return resp_obj.get_json(), status


class TestR5MissingTasksCreated:
    """R5: missing tasks for current influencer doctors are created."""

    def test_new_client_tasks_created_for_influencer_doctor(self, db):
        doctor = make_influencer_doctor("Dr. P", priority="A")
        client = make_client(doctors=[doctor])
        plan = make_plan()
        product = make_product()

        seed_db(db, clients=[client], plans=[plan], products=[product])

        result, status = _call(db, client)

        assert status == 200
        assert result["success"] is True
        assert result["tasksCreated"] == 1

        # Verify the task was created with the doctor's priority
        all_tasks = dict(db._store.get("tasks", {}))
        assert len(all_tasks) == 1
        created = list(all_tasks.values())[0]
        assert created["doctorName"] == "Dr. P"
        assert created["priority"] == "A"
        assert created["clientId"] == "client1"

    def test_two_influencer_doctors_two_tasks_created(self, db):
        doctor_p = make_influencer_doctor("Dr. P", priority="A")
        doctor_q = make_influencer_doctor("Dr. Q", priority="B")
        client = make_client(doctors=[doctor_p, doctor_q])
        plan = make_plan()
        product = make_product()

        seed_db(db, clients=[client], plans=[plan], products=[product])

        result, _ = _call(db, client)

        assert result["tasksCreated"] == 2
        all_tasks = list(db._store.get("tasks", {}).values())
        priorities = {t["doctorName"]: t["priority"] for t in all_tasks}
        assert priorities.get("Dr. P") == "A"
        assert priorities.get("Dr. Q") == "B"

    def test_tasks_created_per_marketing_activity(self, db):
        doctor = make_influencer_doctor("Dr. P", priority="B")
        client = make_client(doctors=[doctor])
        plan = make_plan()
        product = make_product(marketing_tasks=["visit", "demo", "sample"])

        seed_db(db, clients=[client], plans=[plan], products=[product])

        result, _ = _call(db, client)

        assert result["tasksCreated"] == 3

    def test_no_tasks_created_for_non_matching_plan(self, db):
        """Client city doesn't match any plan."""
        doctor = make_influencer_doctor("Dr. P", priority="A")
        client = make_client(city="مكة", doctors=[doctor])
        plan = make_plan(cities=["الرياض"])
        product = make_product()

        seed_db(db, clients=[client], plans=[plan], products=[product])

        result, _ = _call(db, client)

        assert result["tasksCreated"] == 0
        assert result["matchingPlans"] == 0


class TestIdempotency:
    """FR-008/INV-3: repeated saves create no duplicate tasks."""

    def test_second_save_creates_no_duplicates(self, db):
        doctor = make_influencer_doctor("Dr. P", priority="A")
        client = make_client(doctors=[doctor])
        plan = make_plan()
        product = make_product()

        seed_db(db, clients=[client], plans=[plan], products=[product])

        # First save
        _call(db, client)
        tasks_after_first = len(db._store.get("tasks", {}))

        # Second save with same client
        result, _ = _call(db, client)

        tasks_after_second = len(db._store.get("tasks", {}))
        assert tasks_after_second == tasks_after_first, (
            "Second save should not create new tasks"
        )
        assert result["tasksCreated"] == 0
        assert result["tasksSkipped"] >= 1

    def test_unchanged_client_zero_writes(self, db):
        """INV-4: save with unchanged influencer-doctor set → 0 creates, 0 updates, 0 deletes."""
        doctor = make_influencer_doctor("Dr. P", priority="A")
        client = make_client(doctors=[doctor])
        plan = make_plan()
        product = make_product()
        # Pre-existing task already at correct priority
        existing_task = make_task(
            "t1", doctor_name="Dr. P", priority="A", status="pending",
            plan_id="plan1", product_id="prod1", marketing_task="visit",
        )

        seed_db(db, clients=[client], plans=[plan], products=[product],
                tasks=[existing_task])

        result, _ = _call(db, client)

        assert result["tasksCreated"] == 0
        assert result["tasksUpdated"] == 0
        assert result["tasksDeleted"] == 0

    def test_new_influencer_doctor_added_gets_tasks(self, db):
        """Adding a new influencer doctor to an existing client creates its tasks."""
        doctor_old = make_influencer_doctor("Dr. P", priority="A")
        doctor_new = make_influencer_doctor("Dr. Q", priority="C")
        client = make_client(doctors=[doctor_old, doctor_new])
        plan = make_plan()
        product = make_product()
        # Dr. P already has a task
        existing_task = make_task(
            "t1", doctor_name="Dr. P", priority="A", status="pending",
        )

        seed_db(db, clients=[client], plans=[plan], products=[product],
                tasks=[existing_task])

        result, _ = _call(db, client)

        # Dr. Q gets a new task; Dr. P task is deduped
        assert result["tasksCreated"] == 1
        all_tasks = {t["doctorName"] for t in db._store.get("tasks", {}).values()}
        assert "Dr. Q" in all_tasks


class TestExpiredPlansSkipped:
    """Tasks are only created for non-expired plans."""

    def test_expired_plan_no_tasks_created(self, db):
        from datetime import datetime, timezone

        doctor = make_influencer_doctor("Dr. P", priority="A")
        client = make_client(doctors=[doctor])
        expired_plan = make_plan()
        # Override endDate to the past
        expired_plan["endDate"] = datetime(2020, 1, 1, tzinfo=timezone.utc)
        product = make_product()

        seed_db(db, clients=[client], plans=[expired_plan], products=[product])

        result, _ = _call(db, client)

        assert result["tasksCreated"] == 0
        assert result["matchingPlans"] == 0
