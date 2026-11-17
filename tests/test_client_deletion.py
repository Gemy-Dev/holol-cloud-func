"""Phase 1 (SEC-06): deleting a client keeps its fieldwork and can be undone."""
from modules.products import (
    DELETED_WITH_CLIENT_KEY,
    delete_client_and_tasks,
    purge_client,
)


def _body(response):
    if isinstance(response, tuple):
        return response[0].get_json(), response[1]
    return response.get_json(), 200


def _seed(db):
    db._put("clients", "c1", {"name": "مستشفى", "state": "approved"})
    db._put("tasks", "pending", {"clientId": "c1", "status": "pending", "reviewState": "approved"})
    db._put("tasks", "awaiting", {"clientId": "c1", "status": "pending", "reviewState": "pending"})
    db._put("tasks", "completed", {"clientId": "c1", "status": "completed", "reviewState": "approved"})
    db._put("tasks", "visited", {"clientId": "c1", "status": "pending", "visitResult": "تم"})
    db._put("tasks", "deletedBefore", {"clientId": "c1", "status": "pending", "reviewState": "deleted",
                                        "previousReviewState": "approved"})
    db._put("tasks", "otherClient", {"clientId": "c2", "status": "pending", "reviewState": "approved"})


def test_delete_soft_deletes_the_client(db):
    _seed(db)
    body, status = _body(delete_client_and_tasks({"clientId": "c1"}, {"uid": "mgr"}, db))
    client = db._get_all("clients")["c1"]
    assert status == 200 and body["success"]
    assert client["state"] == "deleted"
    assert client["previousReviewState"] == "approved"
    assert client["deletedBy"] == "mgr"
    assert client["deletedAt"]


def test_delete_retires_only_tasks_without_fieldwork(db):
    _seed(db)
    body, _ = _body(delete_client_and_tasks({"clientId": "c1"}, {"uid": "mgr"}, db))
    tasks = db._get_all("tasks")
    assert body["tasksRetired"] == 2
    for task_id, previous in (("pending", "approved"), ("awaiting", "pending")):
        assert tasks[task_id]["reviewState"] == "deleted"
        assert tasks[task_id]["previousReviewState"] == previous
        assert tasks[task_id][DELETED_WITH_CLIENT_KEY] == "c1"
    assert tasks["completed"]["reviewState"] == "approved"
    assert "reviewState" not in tasks["visited"]
    assert DELETED_WITH_CLIENT_KEY not in tasks["deletedBefore"]
    assert tasks["otherClient"]["reviewState"] == "approved"


def test_nothing_is_removed_by_a_delete(db):
    _seed(db)
    delete_client_and_tasks({"clientId": "c1"}, {"uid": "mgr"}, db)
    assert "c1" in db._get_all("clients")
    assert len(db._get_all("tasks")) == 6


def test_a_second_delete_changes_nothing(db):
    _seed(db)
    delete_client_and_tasks({"clientId": "c1"}, {"uid": "mgr"}, db)
    body, status = _body(delete_client_and_tasks({"clientId": "c1"}, {"uid": "other"}, db))
    assert status == 200 and body["tasksRetired"] == 0
    assert db._get_all("clients")["c1"]["deletedBy"] == "mgr"


def test_purge_refuses_a_client_outside_the_bin(db):
    _seed(db)
    _, status = _body(purge_client({"clientId": "c1"}, {"uid": "admin"}, db))
    assert status == 409
    assert "c1" in db._get_all("clients")


def test_purge_removes_the_client_and_its_retired_tasks_only(db):
    _seed(db)
    delete_client_and_tasks({"clientId": "c1"}, {"uid": "mgr"}, db)
    body, status = _body(purge_client({"clientId": "c1"}, {"uid": "admin"}, db))
    tasks = db._get_all("tasks")
    assert status == 200 and body["tasksDeleted"] == 2
    assert "c1" not in db._get_all("clients")
    assert set(tasks) == {"completed", "visited", "deletedBefore", "otherClient"}
