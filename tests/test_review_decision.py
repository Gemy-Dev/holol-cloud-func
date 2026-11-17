"""REV-01: accepting or rejecting a record told nobody.

The record's owner is now told, by notifyReviewDecision. Only the record is
taken from the caller: the decision, owner and reason are read from Firestore.
"""
import json
from unittest.mock import patch

from modules import business_notifications as bn


def _call(db, uid, collection, record_id):
    with patch.object(bn.n, "send_to_user") as send:
        send.return_value = {"successCount": 1, "failureCount": 0, "unreachable": []}
        response = bn.handle_review_decision({"uid": uid},
                                             {"collection": collection, "recordId": record_id}, db)
    body, status = response
    return json.loads(body.get_data()), status, send


def _seed(db):
    db._put("users", "mgr", {"role": "salesManager", "isActive": True})
    db._put("users", "rep", {"role": "salesRepresentative", "isActive": True})


def test_an_approved_client_tells_its_creator(db, app_context):
    _seed(db)
    db._put("clients", "c1", {"name": "مستشفى الأمل", "state": "approved", "creatorId": "rep"})

    body, status, send = _call(db, "mgr", "clients", "c1")

    assert status == 200 and body["decision"] == "approved"
    kwargs = send.call_args.kwargs
    assert kwargs["user_id"] == "rep"
    assert kwargs["title"] == "تم قبول العميل"
    assert "مستشفى الأمل" in kwargs["body"]
    assert kwargs["message_data"] == {"action": "open_client", "clientId": "c1",
                                      "event": "review_decision"}
    assert kwargs["notification_id"] == "review_decision-clients-c1-approved-rep"


def test_a_rejected_activity_carries_its_reason(db, app_context):
    _seed(db)
    db._put("tasks", "t1", {"name": "زيارة", "reviewState": "deleted",
                            "cancelReason": "مكرر", "assignedToId": "rep"})

    body, _, send = _call(db, "mgr", "tasks", "t1")

    assert body["decision"] == "rejected"
    assert send.call_args.kwargs["title"] == "تم رفض النشاط"
    assert "السبب: مكرر" in send.call_args.kwargs["body"]


def test_a_removal_is_not_announced_as_a_rejection(db, app_context):
    _seed(db)
    db._put("tasks", "t1", {"reviewState": "deleted", "deletedAt": "2026-11-01",
                            "assignedToId": "rep"})

    body, _, send = _call(db, "mgr", "tasks", "t1")

    assert body["skipped"] == "not_decided"
    send.assert_not_called()


def test_a_pending_record_is_not_announced(db, app_context):
    _seed(db)
    db._put("clients", "c1", {"state": "pending", "creatorId": "rep"})

    body, _, send = _call(db, "mgr", "clients", "c1")

    assert body["skipped"] == "not_decided"
    send.assert_not_called()


def test_every_representative_on_an_opportunity_is_told(db, app_context):
    _seed(db)
    db._put("users", "rep2", {"role": "salesRepresentative", "isActive": True})
    db._put("main_opportunities", "o1", {"name": "فرصة", "reviewState": "approved",
                                         "salesRepresentativeIds": ["rep", "rep2"]})

    _, _, send = _call(db, "mgr", "main_opportunities", "o1")

    assert [c.kwargs["user_id"] for c in send.call_args_list] == ["rep", "rep2"]


def test_the_reviewer_is_not_told_about_their_own_record(db, app_context):
    _seed(db)
    db._put("clients", "c1", {"state": "approved", "creatorId": "mgr"})

    body, _, send = _call(db, "mgr", "clients", "c1")

    assert body["skipped"] == "no_owner"
    send.assert_not_called()


def test_only_a_reviewer_may_announce(db, app_context):
    _seed(db)
    db._put("clients", "c1", {"state": "approved", "creatorId": "rep"})

    _, status, send = _call(db, "rep", "clients", "c1")

    assert status == 403
    send.assert_not_called()


def test_an_unknown_collection_is_refused(db, app_context):
    _seed(db)
    _, status, _ = _call(db, "mgr", "users", "rep")
    assert status == 400
