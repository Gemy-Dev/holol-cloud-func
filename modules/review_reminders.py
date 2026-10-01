"""``reportSaved`` and the daily review summary (spec 2038).

``reportSaved`` is called after every save of a report or visit from a current
app or dashboard build. It refreshes the special-request record and, for a new
report or visit, announces it to the reviewers. The daily summary lives here
too (added with its user story).
"""
import traceback
from datetime import datetime

from flask import jsonify

from modules import config, firestore_tx, notification_log, notifications
from modules import special_requests as sr
from modules.config import IRAQ_TIMEZONE
from modules.dates import as_iso

_EVENTS = ("created", "updated")
_INTENTS = ("set", "withdraw", "none")


def _bad_payload():
    return sr.error_response(sr.SpecialRequestError("invalid_payload", 400))


def _read_source(db, data):
    """The source the payload names, or None when it does not exist.

    Raises SpecialRequestError(invalid_payload) for a malformed reference.
    """
    source_type = data.get("sourceType")
    if source_type == "report":
        report_id = data.get("reportId")
        if not isinstance(report_id, str) or not report_id:
            raise sr.SpecialRequestError("invalid_payload", 400)
        return sr.read_report_source(db, report_id)
    if source_type == "visit":
        support_id, visit_id = data.get("supportRecordId"), data.get("visitId")
        if not (isinstance(support_id, str) and support_id and isinstance(visit_id, str) and visit_id):
            raise sr.SpecialRequestError("invalid_payload", 400)
        return sr.read_visit_source(db, support_id, visit_id)
    raise sr.SpecialRequestError("invalid_payload", 400)


def _resolve_fingerprint(source, expected_flag, expected_text, caller_uid):
    """Check the save's fingerprint against the server's copy of the source.

    Returns the source to refresh from. While the server's copy differs from
    what the save wrote (an offline visit whose sync has not landed) the answer
    is a retryable ``source_not_synced``. When the stored keys are absent
    (stripped by an old app build, research R15) the author's own expected
    values stand in for them; anyone else gets ``source_not_synced``.
    """
    text = (expected_text or "").strip()
    if source.key_present:
        if source.flag != (bool(expected_flag) and bool(text)) or source.text != text:
            raise sr.SpecialRequestError("source_not_synced", 409)
        return source
    if caller_uid != source.rep_id:
        raise sr.SpecialRequestError("source_not_synced", 409)
    return source.with_expected(expected_flag, expected_text)


def _announce(db, source, record, caller_uid, caller_source="system"):
    """Tell the sales managers and admins a new report or visit arrived.

    One notice per source, ever: it is claimed under ``review_request-{id}``,
    so a repeated or concurrent ``created`` finds it taken and pushes nothing.
    The caller (the rep, or an admin on the dashboard) is not told about their
    own save.
    """
    lines = [
        f"المندوب: {source.rep_name or '-'}",
        f"العميل: {source.client_name or '-'}",
        f"النوع: {sr.type_label(source)}",
    ]
    if record and record.get("state") == "awaiting":
        lines.append("يتضمن طلبات خاصة من العميل")
    if source.source_type == "report":
        route = {"action": "open_daily_report", "reportId": source.report_id}
    else:
        route = {"action": "open_support_record", "supportRecordId": source.support_record_id}
    return notifications.send_to_roles(
        db,
        roles=["salesManager", "admin"],
        title="وصل تقرير جديد بحاجة إلى مراجعة",
        body="\n".join(lines),
        message_data=route,
        kind="review_request",
        source=caller_source,
        actor_id=caller_uid,
        notification_id=f"review_request-{source.request_id}",
        create_only=True,
    )


def handle_report_saved(decoded_token, data, db):
    """Refresh a special-request record after a save, and announce new reports.

    Safe to repeat: an unchanged save is a no-op, and the announcement is
    claimed once per request.
    """
    try:
        event, intent = data.get("event"), data.get("intent")
        expected_flag = data.get("expectedFlag")
        if (event not in _EVENTS or intent not in _INTENTS
                or not isinstance(expected_flag, bool) or "expectedText" not in data):
            return _bad_payload()

        caller_uid = decoded_token.get("uid")
        try:
            source = _read_source(db, data)
            if source is None:
                raise sr.SpecialRequestError("source_not_found", 404)

            # A visit copied from a task report has no request of its own; the
            # report did the refresh and the announcement already.
            if source.source_type == "visit" and source.is_report_copy:
                if intent == "none":
                    return jsonify({"success": True, "request": None, "notified": False,
                                    "reason": "report_copy"}), 200
                raise sr.SpecialRequestError("report_copy", 409)

            resolved = _resolve_fingerprint(source, expected_flag, data.get("expectedText"), caller_uid)
            outcome = firestore_tx.run_transaction(
                db, lambda tx: sr.refresh_request(tx, db, resolved, intent, caller_uid)
            )
        except sr.SpecialRequestError as error:
            return sr.error_response(error)

        record = outcome["request"]
        body = {
            "success": True,
            "request": {"state": record["state"]} if record else None,
            "notified": False,
        }
        if event == "updated":
            body["reason"] = "updated_event"  # an edit never announces (FR-024)
        elif resolved.deleted:
            body["reason"] = "deleted"
        else:
            # Not caught: a failure here is a 5xx the app retries, and the
            # create-only claim keeps the retry from announcing twice.
            result = _announce(db, resolved, record, caller_uid,
                               notification_log.request_source(data))
            if result.get("skipped"):
                body["reason"] = result["skipped"]
            else:
                body["notified"] = True
                for key in ("successCount", "failureCount", "unreachable"):
                    if key in result:
                        body[key] = result[key]
        return jsonify(body), 200
    except Exception as error:
        print(f"Error in reportSaved: {error}")
        print(traceback.format_exc())
        return jsonify({"success": False, "error": "internal",
                        "message": "تعذّر معالجة الطلب"}), 500


# ---------------------------------------------------------------------------
# The morning summary (US5)
# ---------------------------------------------------------------------------

_DIGEST_ROLES = ("salesManager", "admin")


def reports_phrase(count: int) -> str:
    """``count`` reports with Arabic number agreement."""
    if count == 1:
        return "تقرير واحد"
    if count == 2:
        return "تقريران"
    if count <= 10:
        return f"{count} تقارير"
    return f"{count} تقريرًا"


def _since_iso() -> str:
    return as_iso(config.REVIEW_REMINDER_SINCE) or config.REVIEW_REMINDER_SINCE


def _counted(created_at, since: str) -> bool:
    """Whether a report or visit is new enough to count (at or after the
    go-live cut-off). A date that cannot be read is not counted."""
    created = as_iso(created_at)
    return bool(created) and created >= since


def _pending_reviews(db) -> dict:
    """How many reports and visits still wait on each reviewer slot.

    Skips deleted reports, visits of deleted support records, visit copies of
    task reports (the report itself counts), and anything older than the
    cut-off. Returns ``{"salesManager": n, "admin": n}`` plus the sets of live
    source ids that special requests are joined against.
    """
    since = _since_iso()
    pending = {"salesManager": 0, "admin": 0}
    live_reports, live_visits = set(), set()
    report_ids = set()

    for snapshot in db.collection("reports").stream():
        report_ids.add(snapshot.id)
        raw = snapshot.to_dict() or {}
        if raw.get("reviewState") == "deleted":
            continue
        live_reports.add(snapshot.id)
        if not _counted(raw.get("createdAt"), since):
            continue
        if not raw.get("salesManagerReview"):
            pending["salesManager"] += 1
        if not raw.get("adminReview"):
            pending["admin"] += 1

    for snapshot in db.collection("technical_support").stream():
        parent = snapshot.to_dict() or {}
        if parent.get("reviewState") == "deleted":
            continue
        reviews = parent.get("reviews") or {}
        for entry in sr.visit_entries(parent.get("visitHistory")):
            if not entry.get("id"):
                continue
            visit_id = entry["id"]
            if visit_id in report_ids:
                continue  # a copy of a task report: the report counts, not the copy
            live_visits.add(sr.request_id_for_visit(snapshot.id, visit_id))
            if not _counted(entry.get("createdAt"), since):
                continue
            if not reviews.get(f"{visit_id}__salesManager"):
                pending["salesManager"] += 1
            if not reviews.get(f"{visit_id}__admin"):
                pending["admin"] += 1

    return {"pending": pending, "live_requests": {
        *(sr.request_id_for_report(i) for i in live_reports), *live_visits}}


def _request_counts(db, live_requests: set) -> list:
    """The special requests the summary counts, as small dicts.

    Only requests whose source is still live; the join is against the streams
    already read for the review counts.
    """
    counted = []
    for snapshot in db.collection(sr.COLLECTION).stream():
        if snapshot.id not in live_requests:
            continue
        record = snapshot.to_dict() or {}
        state = record.get("state")
        if state == "awaiting":
            counted.append({
                "kind": "awaiting",
                "sales_decided": bool(record.get("salesManagerDecision")),
                "admin_decided": bool(record.get("adminDecision")),
                "sales_decider": (record.get("salesManagerDecision") or {}).get("deciderId"),
                "admin_decider": (record.get("adminDecision") or {}).get("deciderId"),
            })
        elif state == "approved" and _send_unfinished(record.get("send")):
            counted.append({"kind": "unsent"})
    return counted


def _send_unfinished(send) -> bool:
    """Whether an approved request's email is failed, partial or stalled."""
    if not isinstance(send, dict):
        return False
    state = send.get("state")
    if state in ("failed", "partial"):
        return True
    if state == "sending":
        stale_at = sr.stale_before_iso()
        return (as_iso(send.get("claimedAt")) or "") <= stale_at
    return False


def _awaiting_for(counts: list, role: str, uid: str) -> int:
    """Awaiting requests whose slot for ``role`` is open and that ``uid`` may
    still decide (the same person cannot take both slots)."""
    total = 0
    for item in counts:
        if item["kind"] != "awaiting":
            continue
        if role == "salesManager":
            if not item["sales_decided"] and item["admin_decider"] != uid:
                total += 1
        elif not item["admin_decided"] and item["sales_decider"] != uid:
            total += 1
    return total


def _digest_body(pending: int, awaiting: int, unsent: int) -> str:
    lines = []
    if pending:
        lines.append(f"لديك {reports_phrase(pending)} بحاجة إلى مراجعة، يرجى زيارة الموقع لمراجعة المهمة")
    if awaiting:
        lines.append(f"{awaiting} طلبات خاصة بانتظار قرارك")
    if unsent:
        lines.append(f"{unsent} طلبات خاصة موافق عليها لم تُرسل بالكامل")
    return "\n".join(lines)


def _reviewers(db) -> list:
    """``[(uid, role_key)]`` for every active sales manager and admin."""
    found = {}
    for role in _DIGEST_ROLES:
        spellings = notifications._ROLE_SPELLINGS[role]
        for snapshot in db.collection("users").where("role", "in", spellings).stream():
            user = snapshot.to_dict() or {}
            if user.get("isActive") is True:
                found.setdefault(snapshot.id, role)
    return list(found.items())


def handle_review_reminders(db):
    """Send each reviewer their one morning summary of pending reviews.

    Unauthenticated: Cloud Scheduler calls it at 08:00 Iraq time. A reviewer
    gets a summary only when something waits on them, and never a second one the
    same day: it is claimed under ``review_digest-{date}-{uid}``. The summary
    counts many reports, so it names no client or rep.
    """
    try:
        date = datetime.now(IRAQ_TIMEZONE).strftime("%Y-%m-%d")
        reviewers = _reviewers(db)
        gathered = _pending_reviews(db)
        counts = _request_counts(db, gathered["live_requests"])
        unsent = sum(1 for item in counts if item["kind"] == "unsent")

        notified = already = unreachable = failed = 0
        for uid, role in reviewers:
            pending = gathered["pending"][role]
            awaiting = _awaiting_for(counts, role, uid)
            unsent_for = unsent if role == "admin" else 0
            if not (pending or awaiting or unsent_for):
                continue
            try:
                result = _send_digest(db, uid, date, pending, awaiting, unsent_for)
            except Exception as error:
                # One reviewer's failure must not cost everyone after them their
                # summary; the scheduler does not retry.
                print(f"Review summary for {uid} failed: {error}")
                failed += 1
                continue
            if result.get("skipped"):
                already += 1
            elif result.get("unreachable"):
                unreachable += 1
            else:
                notified += 1
        return jsonify({
            "success": True, "date": date, "reviewers": len(reviewers), "notified": notified,
            "skipped": {"already": already, "unreachable": unreachable}, "failed": failed,
        }), 200
    except Exception as error:
        print(f"Error in review_reminders: {error}")
        print(traceback.format_exc())
        return jsonify({"success": False, "error": "internal",
                        "message": "تعذّر إرسال ملخص المراجعات"}), 500


def _send_digest(db, uid, date, pending, awaiting, unsent_for):
    """One reviewer's summary, claimed once per day under its fixed id."""
    return notifications.send_to_user(
        db,
        user_id=uid,
        title="تقارير بحاجة إلى مراجعة",
        body=_digest_body(pending, awaiting, unsent_for),
        message_data={"action": "open_pending_reviews"},
        kind="review_digest",
        source="system",
        actor_id="",
        notification_id=f"review_digest-{date}-{uid}",
        create_only=True,
    )
