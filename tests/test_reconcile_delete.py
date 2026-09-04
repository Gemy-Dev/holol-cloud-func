"""Tests for US2: soft-delete removed-doctor tasks (R4 + R0) in reconcile_client_tasks."""
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
from tests.conftest import is_contract_iso


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


class TestR4RemovedDoctorSoftDeleted:
    """R4: tasks for a doctor no longer in the desired map are soft-deleted."""

    def test_removed_doctor_not_completed_task_soft_deleted(self, db):
        # Client now has NO influencer doctors — Dr. Y was removed
        client = make_client(doctors=[])
        plan = make_plan()
        product = make_product()
        task = make_task("t1", doctor_name="Dr. Y", priority="B", status="pending")

        seed_db(db, clients=[client], plans=[plan], products=[product], tasks=[task])

        result, status = _call(db, client)

        assert status == 200
        assert result["tasksDeleted"] == 1
        assert result["tasksUpdated"] == 0

        updated = db.collection("tasks").document("t1").get().to_dict()
        assert updated["reviewState"] == "deleted"
        # Contract rule R1: an ISO-8601 string, never a Timestamp — the
        # field app and the dashboard order this collection on it.
        assert is_contract_iso(updated["updatedAt"])

    def test_removed_doctor_completed_task_retained(self, db):
        """R1 + R4: completed task for a removed doctor is kept."""
        client = make_client(doctors=[])
        plan = make_plan()
        product = make_product()
        completed_task = make_task("tc", doctor_name="Dr. Y", status="completed",
                                   review_state="approved")

        seed_db(db, clients=[client], plans=[plan], products=[product],
                tasks=[completed_task])

        result, _ = _call(db, client)

        assert result["tasksDeleted"] == 0
        assert result["completedSkipped"] == 1
        updated = db.collection("tasks").document("tc").get().to_dict()
        assert updated["reviewState"] == "approved"

    def test_removed_doctor_arabic_completed_retained(self, db):
        """Arabic status 'مكتمل' also counts as completed."""
        client = make_client(doctors=[])
        task = make_task("tc", doctor_name="Dr. Y", status="مكتمل")

        seed_db(db, clients=[client], tasks=[task])

        result, _ = _call(db, client)

        assert result["tasksDeleted"] == 0
        assert result["completedSkipped"] == 1

    def test_partial_removal_only_removed_doctor_deleted(self, db):
        """Removing Dr. Y keeps Dr. X's task untouched."""
        doctor_x = make_influencer_doctor("Dr. X", priority="A")
        client = make_client(doctors=[doctor_x])
        plan = make_plan()
        product = make_product()
        task_x = make_task("tx", doctor_name="Dr. X", priority="A")
        task_y = make_task("ty", doctor_name="Dr. Y", priority="B")

        seed_db(db, clients=[client], plans=[plan], products=[product],
                tasks=[task_x, task_y])

        result, _ = _call(db, client)

        assert result["tasksDeleted"] == 1
        assert db.collection("tasks").document("tx").get().to_dict()["reviewState"] == "approved"
        assert db.collection("tasks").document("ty").get().to_dict()["reviewState"] == "deleted"

    def test_mixed_completed_and_pending_for_removed_doctor(self, db):
        """Only the pending task for the removed doctor is deleted."""
        client = make_client(doctors=[])
        plan = make_plan()
        product = make_product()
        pending_task = make_task("tp", doctor_name="Dr. Y", status="pending",
                                 marketing_task="visit")
        completed_task = make_task("tc", doctor_name="Dr. Y", status="completed",
                                   marketing_task="demo")

        seed_db(db, clients=[client], plans=[plan], products=[product],
                tasks=[pending_task, completed_task])

        result, _ = _call(db, client)

        assert result["tasksDeleted"] == 1
        assert result["completedSkipped"] == 1
        assert db.collection("tasks").document("tp").get().to_dict()["reviewState"] == "deleted"
        assert db.collection("tasks").document("tc").get().to_dict()["reviewState"] == "approved"


class TestR0EmptyDoctorNameUntouched:
    """R0: tasks with empty doctorName are never modified."""

    def test_empty_doctor_name_task_not_deleted(self, db):
        client = make_client(doctors=[])
        plan = make_plan()
        product = make_product()
        # Task with empty doctorName (created for a client with no influencer doctors)
        task = make_task("t1", doctor_name="", priority="C")

        seed_db(db, clients=[client], plans=[plan], products=[product], tasks=[task])

        result, _ = _call(db, client)

        assert result["tasksDeleted"] == 0
        unchanged = db.collection("tasks").document("t1").get().to_dict()
        assert unchanged["reviewState"] == "approved"

    def test_empty_doctor_name_task_not_updated(self, db):
        """Even when the client has influencer doctors, empty-name tasks stay."""
        doctor = make_influencer_doctor("Dr. X", priority="A")
        client = make_client(doctors=[doctor])
        plan = make_plan()
        product = make_product()
        empty_task = make_task("te", doctor_name="", priority="C")

        seed_db(db, clients=[client], plans=[plan], products=[product], tasks=[empty_task])

        result, _ = _call(db, client)

        assert result["tasksUpdated"] == 0
        assert result["tasksDeleted"] == 0


class TestIsInfluencerFalse:
    """A doctor with isInfluencer=False is excluded from the desired map → treated as removed."""

    def test_toggled_off_influencer_tasks_deleted(self, db):
        # Doctor exists but isInfluencer is now False
        non_influencer_doctor = {
            "name": "Dr. Z",
            "phone": "050",
            "email": "z@test.com",
            "isInfluencer": False,
            "priority": "A",
        }
        client = make_client(doctors=[non_influencer_doctor])
        plan = make_plan()
        product = make_product()
        task = make_task("t1", doctor_name="Dr. Z", priority="A", status="pending")

        seed_db(db, clients=[client], plans=[plan], products=[product], tasks=[task])

        result, _ = _call(db, client)

        assert result["tasksDeleted"] == 1
        assert db.collection("tasks").document("t1").get().to_dict()["reviewState"] == "deleted"
