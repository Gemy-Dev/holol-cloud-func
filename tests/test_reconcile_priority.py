"""Tests for US1: priority-drift update (R1/R2/R3) in reconcile_client_tasks."""
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
    """Invoke reconcile_client_tasks and return the parsed JSON response."""
    with patch("modules.tasks.firestore") as mock_fs:
        mock_fs.SERVER_TIMESTAMP = "SERVER_TIMESTAMP"
        mock_fs.ArrayUnion = lambda x: x
        response = reconcile_client_tasks({"client": client_data}, db)
    if isinstance(response, tuple):
        resp_obj, status = response
    else:
        resp_obj, status = response, 200
    return resp_obj.get_json(), status


class TestR2PriorityDrift:
    """R2: not-completed task whose doctor's priority changed is updated."""

    def test_single_task_updated_when_priority_changes(self, db):
        doctor = make_influencer_doctor("Dr. X", priority="A")
        client = make_client(doctors=[doctor])
        plan = make_plan()
        product = make_product()
        task = make_task("t1", priority="C", doctor_name="Dr. X")

        seed_db(db, clients=[client], plans=[plan], products=[product], tasks=[task])

        result, status = _call(db, client)

        assert status == 200
        assert result["success"] is True
        assert result["tasksUpdated"] == 1
        assert result["tasksDeleted"] == 0

        updated = db.collection("tasks").document("t1").get().to_dict()
        assert updated["priority"] == "A"
        assert updated["updatedAt"] == "SERVER_TIMESTAMP"

    def test_multiple_tasks_for_same_doctor_all_updated(self, db):
        doctor = make_influencer_doctor("Dr. X", priority="B")
        client = make_client(doctors=[doctor])
        plan = make_plan()
        product = make_product()
        task1 = make_task("t1", priority="C", doctor_name="Dr. X", marketing_task="visit")
        task2 = make_task("t2", priority="C", doctor_name="Dr. X", marketing_task="demo")

        seed_db(db, clients=[client], plans=[plan], products=[product], tasks=[task1, task2])

        result, _ = _call(db, client)

        assert result["tasksUpdated"] == 2
        for tid in ["t1", "t2"]:
            assert db.collection("tasks").document(tid).get().to_dict()["priority"] == "B"

    def test_different_doctors_tasks_not_affected(self, db):
        """Only Dr. X's priority changes; Dr. Y's task stays at C."""
        doctor_x = make_influencer_doctor("Dr. X", priority="A")
        doctor_y = make_influencer_doctor("Dr. Y", priority="C")
        client = make_client(doctors=[doctor_x, doctor_y])
        plan = make_plan()
        product = make_product()
        task_x = make_task("tx", priority="C", doctor_name="Dr. X")
        task_y = make_task("ty", priority="C", doctor_name="Dr. Y")

        seed_db(db, clients=[client], plans=[plan], products=[product], tasks=[task_x, task_y])

        result, _ = _call(db, client)

        # Dr. X updated, Dr. Y unchanged (priority already matches)
        assert result["tasksUpdated"] == 1
        assert db.collection("tasks").document("tx").get().to_dict()["priority"] == "A"
        assert db.collection("tasks").document("ty").get().to_dict()["priority"] == "C"


class TestR3NoPriorityDrift:
    """R3: not-completed task already at correct priority is a no-op."""

    def test_no_update_when_priority_equals(self, db):
        doctor = make_influencer_doctor("Dr. X", priority="B")
        client = make_client(doctors=[doctor])
        plan = make_plan()
        product = make_product()
        task = make_task("t1", priority="B", doctor_name="Dr. X")

        seed_db(db, clients=[client], plans=[plan], products=[product], tasks=[task])

        result, _ = _call(db, client)

        assert result["tasksUpdated"] == 0

    def test_no_op_save_produces_zero_task_writes(self, db):
        """INV-4: unchanged client → zero writes."""
        doctor = make_influencer_doctor("Dr. X", priority="B")
        client = make_client(doctors=[doctor])
        plan = make_plan(clients_ids=["client1"])  # already in plan
        product = make_product()
        task = make_task("t1", priority="B", doctor_name="Dr. X")

        seed_db(db, clients=[client], plans=[plan], products=[product], tasks=[task])

        result, _ = _call(db, client)

        assert result["tasksUpdated"] == 0
        assert result["tasksDeleted"] == 0
        # task may be skipped (dedup) but zero new creates
        assert result["tasksCreated"] == 0


class TestR1CompletedImmutability:
    """R1: completed tasks are never re-priced."""

    def test_completed_task_not_updated(self, db):
        doctor = make_influencer_doctor("Dr. X", priority="A")
        client = make_client(doctors=[doctor])
        plan = make_plan()
        product = make_product()
        task = make_task("t1", priority="C", doctor_name="Dr. X", status="completed")

        seed_db(db, clients=[client], plans=[plan], products=[product], tasks=[task])

        result, _ = _call(db, client)

        assert result["tasksUpdated"] == 0
        assert result["completedSkipped"] == 1
        # Completed task still has old priority
        assert db.collection("tasks").document("t1").get().to_dict()["priority"] == "C"

    def test_arabic_completed_status_not_updated(self, db):
        doctor = make_influencer_doctor("Dr. X", priority="A")
        client = make_client(doctors=[doctor])
        plan = make_plan()
        product = make_product()
        task = make_task("t1", priority="C", doctor_name="Dr. X", status="مكتمل")

        seed_db(db, clients=[client], plans=[plan], products=[product], tasks=[task])

        result, _ = _call(db, client)

        assert result["completedSkipped"] == 1
        assert result["tasksUpdated"] == 0

    def test_mixed_completed_and_pending_only_pending_updated(self, db):
        doctor = make_influencer_doctor("Dr. X", priority="A")
        client = make_client(doctors=[doctor])
        plan = make_plan()
        product = make_product()
        completed_task = make_task("tc", priority="C", doctor_name="Dr. X",
                                   status="completed", marketing_task="visit")
        pending_task = make_task("tp", priority="C", doctor_name="Dr. X",
                                 status="pending", marketing_task="demo")

        seed_db(db, clients=[client], plans=[plan], products=[product],
                tasks=[completed_task, pending_task])

        result, _ = _call(db, client)

        assert result["completedSkipped"] == 1
        assert result["tasksUpdated"] == 1
        assert db.collection("tasks").document("tc").get().to_dict()["priority"] == "C"
        assert db.collection("tasks").document("tp").get().to_dict()["priority"] == "A"
