"""Client special requests: the authoritative request record (spec 2038).

A sales report or support visit can carry a special request. The record
``special_requests/{requestId}`` is written only here and is authoritative once
it exists (research R5): it holds the text, both slot decisions and the send
record. Clients never write it; the Firestore rules deny them.

``requestId`` is ``report_{reportId}`` or ``visit_{supportRecordId}_{visitId}``.
"""
import smtplib
import traceback
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Optional

from flask import jsonify

from modules import (
    config, email as email_module, firestore_tx, notification_log, notifications,
    special_request_pdf,
)
from modules.config import IRAQ_TIMEZONE
from modules.dates import as_iso, now_iso, to_iso

COLLECTION = "special_requests"

# Both stored spellings of a technical-support task report's type; the same
# pair firestore.rules uses in isTechnicalTaskReport().
_TECHNICAL_TYPES = ("technical_support", "دعم فني")

# The review grant each family needs: the enum name, and the Arabic label older
# accounts still carry (what the dashboard's rules accept as legacyLabel).
_FAMILY_GRANTS = {
    "sales": ("reviewDailyReport", "مراجعة تقرير يومي"),
    "technical": ("reviewTechnicalReport", "مراجعة تقرير فني"),
}

MESSAGES = {
    "invalid_payload": "بيانات الطلب غير صحيحة",
    "invalid_intent": "لا يمكن تنفيذ هذا الإجراء على الطلب الخاص",
    "not_author": "يمكن لمُنشئ التقرير فقط تعديل الطلب الخاص أو سحبه",
    "not_permitted": "ليست لديك صلاحية اتخاذ القرار في هذا الطلب",
    "source_not_found": "التقرير غير موجود",
    "source_not_synced": "لم يصل التقرير إلى الخادم بعد",
    "not_a_special_request": "هذا التقرير لا يحتوي على طلب خاص",
    "slot_taken": "تم اتخاذ القرار في هذه الخانة مسبقًا",
    "same_person": "لا يمكن لنفس الشخص اتخاذ القرار في الخانتين",
    "text_changed": "تغيّر نص الطلب منذ فتح الصفحة، يرجى إعادة التحميل",
    "withdrawn": "سحب المندوب هذا الطلب",
    "already_decided": "تم البت في هذا الطلب نهائيًا",
    "not_owner": "يمكن لصاحب القرار فقط تعديل ملاحظته",
    "not_retryable": "لا يمكن إعادة إرسال هذا الطلب الآن",
    "report_copy": "الطلب الخاص يُدار من تقرير المهمة",
}


class SpecialRequestError(Exception):
    """A rejected request, carrying the HTTP status and an error code."""

    def __init__(self, code: str, status: int, extra: Optional[dict] = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.message = MESSAGES.get(code, code)
        self.extra = extra or {}


def request_id_for_report(report_id: str) -> str:
    return f"report_{report_id}"


def request_id_for_visit(support_record_id: str, visit_id: str) -> str:
    return f"visit_{support_record_id}_{visit_id}"


def family_of(raw_report: dict) -> str:
    """``technical`` for a technical-support task report, else ``sales``.

    Recomputed from the source's current type wherever it matters, because a
    report's type can be corrected after it is filed.
    """
    return "technical" if (raw_report.get("type") or "") in _TECHNICAL_TYPES else "sales"


@dataclass(frozen=True)
class Source:
    """A report or visit as the special-request logic sees it."""

    source_type: str  # 'report' | 'visit'
    request_id: str
    family: str
    deleted: bool
    is_report_copy: bool
    rep_id: str
    rep_name: str
    client_id: Optional[str]
    client_name: str
    report_date: str
    created_at: str
    key_present: bool  # was hasSpecialRequests stored at all (old builds strip it)
    flag: bool
    text: str
    raw: dict
    report_id: Optional[str] = None
    support_record_id: Optional[str] = None
    visit_id: Optional[str] = None

    def with_expected(self, flag: bool, text: Optional[str]) -> "Source":
        """This source with the author's expected flag/text substituted.

        Used when the stored keys are absent (stripped by an old build): the
        fingerprint the author's own save sent stands in for them.
        """
        text = (text or "").strip()
        return replace(self, key_present=True, flag=bool(flag) and bool(text), text=text)


def _flag_and_text(raw: dict):
    text = (raw.get("clientOrders") or "").strip()
    return "hasSpecialRequests" in raw and raw.get("hasSpecialRequests") is not None, \
        bool(raw.get("hasSpecialRequests")) and bool(text), text


def _client_name(db, raw: dict) -> str:
    other = (raw.get("otherClientName") or "").strip()
    if other:
        return other
    client_id = raw.get("clientId")
    if client_id:
        snapshot = db.collection("clients").document(client_id).get()
        if snapshot.exists:
            return (snapshot.to_dict() or {}).get("name") or ""
    return ""


def read_report_source(db, report_id: str) -> Optional[Source]:
    snapshot = db.collection("reports").document(report_id).get()
    if not snapshot.exists:
        return None
    raw = snapshot.to_dict() or {}
    key_present, flag, text = _flag_and_text(raw)
    created = as_iso(raw.get("createdAt")) or ""
    return Source(
        source_type="report", request_id=request_id_for_report(report_id),
        family=family_of(raw), deleted=raw.get("reviewState") == "deleted",
        is_report_copy=False, rep_id=raw.get("assignedToId") or "",
        rep_name=raw.get("userName") or "", client_id=raw.get("clientId"),
        client_name=_client_name(db, raw), report_date=created, created_at=created,
        key_present=key_present, flag=flag, text=text, raw=raw, report_id=report_id,
    )


def visit_entries(raw) -> list:
    """A support record's ``visitHistory`` as a list of visit maps.

    Normally a list. Some records hold a map keyed by visit id, which the apps
    read too (`SupportStoredShapes.visitEntries`); the key then stands in for a
    missing ``id``.
    """
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    if isinstance(raw, dict):
        return [{"id": str(key), **value} if "id" not in value else value
                for key, value in raw.items() if isinstance(value, dict)]
    return []


def read_visit_source(db, support_record_id: str, visit_id: str) -> Optional[Source]:
    """A visit, read from the parent's ``visitHistory``.

    Never from the ``support_visits`` subcollection: a visit filed offline never
    gets that document (research R14 / latent bug), so the array is the only
    place every visit exists. A visit is deleted when its parent record is.
    """
    snapshot = db.collection("technical_support").document(support_record_id).get()
    if not snapshot.exists:
        return None
    parent = snapshot.to_dict() or {}
    entry = next(
        (v for v in visit_entries(parent.get("visitHistory")) if v.get("id") == visit_id),
        None,
    )
    if entry is None:
        return None
    key_present, flag, text = _flag_and_text(entry)
    created = as_iso(entry.get("createdAt")) or ""
    is_copy = db.collection("reports").document(visit_id).get().exists
    return Source(
        source_type="visit", request_id=request_id_for_visit(support_record_id, visit_id),
        family="technical", deleted=parent.get("reviewState") == "deleted",
        is_report_copy=is_copy, rep_id=entry.get("technicianId") or "",
        rep_name=entry.get("technicianName") or "", client_id=parent.get("clientId"),
        client_name=parent.get("clientName") or "",
        report_date=as_iso(entry.get("visitDate")) or created, created_at=created,
        key_present=key_present, flag=flag, text=text, raw=entry,
        support_record_id=support_record_id, visit_id=visit_id,
    )


def type_label(source: Source) -> str:
    """The Arabic type shown in notifications and the PDF (research R12)."""
    return special_request_pdf.type_label_for(source.source_type, source.family)


def caller_may_review(user: Optional[dict], family: str) -> bool:
    """Whether ``user`` is active and holds the review grant for ``family``."""
    if not user or user.get("isActive") is not True:
        return False
    granted = user.get("permissions") or []
    return any(grant in granted for grant in _FAMILY_GRANTS[family])


def is_decided(record: dict) -> bool:
    return bool(record.get("salesManagerDecision") or record.get("adminDecision"))


def _new_record(source: Source) -> dict:
    now = now_iso()
    record = {
        "sourceType": source.source_type,
        "family": source.family,
        "clientId": source.client_id,
        "clientName": source.client_name,
        "representativeId": source.rep_id,
        "representativeName": source.rep_name,
        "reportDate": source.report_date,
        "sourceCreatedAt": source.created_at,
        "requestText": source.text,
        "savedAt": now,
        "salesManagerDecision": None,
        "adminDecision": None,
        "state": "awaiting",
        "send": None,
        "createdAt": now,
        "updatedAt": now,
    }
    if source.source_type == "report":
        record["reportId"] = source.report_id
    else:
        record["supportRecordId"] = source.support_record_id
        record["visitId"] = source.visit_id
    return record


def refresh_request(transaction, db, source: Source, intent: str, caller_uid: str) -> dict:
    """Apply the data-model §3 refresh table inside ``transaction``.

    ``source`` must already be the source *after* the fingerprint step, so the
    flag key is never absent here. Only ``set``/``withdraw`` change the text or
    state, and only while no slot has decided; ``none`` never does. The family
    and client fields of an undecided record are refreshed on any intent,
    because a report's type and client can be corrected.

    Returns ``{"request": dict | None, "changed": bool}``.
    Raises SpecialRequestError for ``invalid_intent`` / ``not_author``.
    """
    ref = db.collection(COLLECTION).document(source.request_id)
    snapshot = ref.get(transaction=transaction)
    record = snapshot.to_dict() if snapshot.exists else None

    if record is not None and is_decided(record):
        return {"request": record, "changed": False}

    if intent == "set":
        if not source.flag:
            raise SpecialRequestError("invalid_intent", 400)
    elif intent == "withdraw":
        if caller_uid != source.rep_id:
            raise SpecialRequestError("not_author", 403)
        if not (source.key_present and not source.flag):
            raise SpecialRequestError("invalid_intent", 400)

    if record is None:
        if intent != "set":
            return {"request": None, "changed": False}
        record = _new_record(source)
        transaction.set(ref, record)
        return {"request": record, "changed": True}

    updates: dict[str, Any] = {}
    if intent == "set":
        if record.get("requestText") != source.text:
            updates["requestText"] = source.text
        if record.get("state") != "awaiting":
            updates["state"] = "awaiting"
    elif intent == "withdraw" and record.get("state") == "awaiting":
        updates["state"] = "withdrawn"
    for key, value in (("family", source.family), ("clientId", source.client_id),
                       ("clientName", source.client_name)):
        if record.get(key) != value:
            updates[key] = value
    if not updates:
        return {"request": record, "changed": False}
    now = now_iso()
    if "requestText" in updates or "state" in updates:
        updates["savedAt"] = now
    updates["updatedAt"] = now
    transaction.update(ref, updates)
    return {"request": {**record, **updates}, "changed": True}


def error_response(error: "SpecialRequestError"):
    """The JSON error body and status for a rejected request.

    ``message`` is Arabic and user-presentable, so both clients show it as is.
    """
    return jsonify({
        "success": False,
        "error": error.code,
        "message": error.message,
        **error.extra,
    }), error.status


# ---------------------------------------------------------------------------
# Decisions (US2)
# ---------------------------------------------------------------------------

_SLOTS = {
    "salesManager": ("salesManagerDecision", "مدير المبيعات"),
    "admin": ("adminDecision", "مسؤول النظام"),
}
_OTHER_SLOT = {"salesManager": "admin", "admin": "salesManager"}
_DECISIONS = ("approved", "rejected")
_MAX_NOTE = 1000


def _clean_note(raw) -> Optional[str]:
    """The trimmed note, or None when blank. Raises invalid_payload when the
    note is not text or is over the length limit (V6)."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise SpecialRequestError("invalid_payload", 400)
    note = raw.strip()
    if len(note) > _MAX_NOTE:
        raise SpecialRequestError("invalid_payload", 400)
    return note or None


def _derive_state(record: dict) -> str:
    decisions = [record.get("salesManagerDecision"), record.get("adminDecision")]
    if any(d and d.get("decision") == "rejected" for d in decisions):
        return "rejected"
    if all(d and d.get("decision") == "approved" for d in decisions):
        return "approved"
    return "awaiting"


def _read_source_for(db, data) -> Optional[Source]:
    source_type = data.get("sourceType")
    if source_type == "report":
        report_id = data.get("reportId")
        if not isinstance(report_id, str) or not report_id:
            raise SpecialRequestError("invalid_payload", 400)
        return read_report_source(db, report_id)
    if source_type == "visit":
        support_id, visit_id = data.get("supportRecordId"), data.get("visitId")
        if not (isinstance(support_id, str) and support_id and isinstance(visit_id, str) and visit_id):
            raise SpecialRequestError("invalid_payload", 400)
        return read_visit_source(db, support_id, visit_id)
    raise SpecialRequestError("invalid_payload", 400)


def _notification_route(record: dict) -> dict:
    """The data payload that opens the source when a notice is tapped."""
    if record.get("sourceType") == "report":
        return {"action": "open_daily_report", "reportId": record.get("reportId")}
    return {"action": "open_support_record", "supportRecordId": record.get("supportRecordId")}


def _notify_rejection(db, record: dict, slot: str, note: Optional[str], actor_id: str,
                      source: str = "system") -> None:
    """Tell the representative their request was rejected. Never raises."""
    try:
        _, slot_label = _SLOTS[slot]
        lines = [
            f"العميل: {record.get('clientName') or '-'}",
            f"المندوب: {record.get('representativeName') or '-'}",
            f"رفض: {slot_label}",
        ]
        if note:
            lines.append(f"الملاحظة: {note}")
        notifications.send_to_user(
            db,
            user_id=record.get("representativeId") or "",
            title="تم رفض الطلب الخاص",
            body="\n".join(lines),
            message_data=_notification_route(record),
            kind="special_request",
            source=source,
            actor_id=actor_id,
        )
    except Exception as error:  # a failed notice never fails the decision
        print(f"Could not notify the representative of a rejection: {error}")


def handle_decide_special_request(decoded_token, data, db):
    """Record one slot's decision on a special request.

    One transaction: validations V1-V6 (data-model §3), the decision, and the
    derived state. Writes only ``special_requests``: a decision never touches
    the report or the support record (FR-011).
    """
    try:
        slot, decision = data.get("slot"), data.get("decision")
        expected_text = data.get("expectedText")
        if slot not in _SLOTS or decision not in _DECISIONS or not isinstance(expected_text, str):
            raise SpecialRequestError("invalid_payload", 400)
        note = _clean_note(data.get("note"))
        caller_uid = decoded_token.get("uid")

        source = _read_source_for(db, data)
        if source is None or source.deleted:
            raise SpecialRequestError("source_not_found", 404)

        user_snapshot = db.collection("users").document(caller_uid).get()
        user = user_snapshot.to_dict() if user_snapshot.exists else None
        if not caller_may_review(user, source.family):
            raise SpecialRequestError("not_permitted", 403)

        field, _ = _SLOTS[slot]
        other_field, _ = _SLOTS[_OTHER_SLOT[slot]]

        def _decide(transaction):
            ref = db.collection(COLLECTION).document(source.request_id)
            snapshot = ref.get(transaction=transaction)
            created = not snapshot.exists
            if created:
                if not source.flag:
                    raise SpecialRequestError("not_a_special_request", 404)
                record = _new_record(source)
            else:
                record = snapshot.to_dict()
            # A report's type can be corrected, so the family follows the source.
            record["family"] = source.family

            state = record.get("state")
            if state == "withdrawn":
                raise SpecialRequestError("withdrawn", 409)
            if state in ("approved", "rejected"):
                raise SpecialRequestError("already_decided", 409)
            if (expected_text or "").strip() != (record.get("requestText") or "").strip():
                raise SpecialRequestError("text_changed", 409)
            if record.get(field):
                raise SpecialRequestError("slot_taken", 409)
            other = record.get(other_field)
            if other and other.get("deciderId") == caller_uid:
                raise SpecialRequestError("same_person", 409)

            now = now_iso()
            record[field] = {
                "decision": decision,
                "deciderId": caller_uid,
                "deciderName": (user or {}).get("name") or "",
                "decidedAt": now,
                "note": note,
            }
            record["state"] = _derive_state(record)
            record["updatedAt"] = now
            changes = {
                "family": record["family"], field: record[field],
                "state": record["state"], "updatedAt": now,
            }
            if record["state"] == "approved":
                # The second approval, and only it, claims the send, in this
                # same transaction: two reviewers approving at once cannot both
                # win, so the email goes out once.
                record["send"] = _new_send_claim(attempts=1)
                changes["send"] = record["send"]
            if created:
                transaction.set(ref, record)
            else:
                transaction.update(ref, changes)
            return record

        record = firestore_tx.run_transaction(db, _decide)
        # Approved only ever happens in the transaction that took the claim.
        won_claim = record["state"] == "approved"

        if record["state"] == "rejected" and record[field]["decision"] == "rejected":
            _notify_rejection(db, record, slot, note, caller_uid,
                              notification_log.request_source(data))

        body = {"success": True, "request": record}
        if won_claim:
            # A failed send never fails the decision (FR-029): run_send records
            # its own failure and returns it.
            final = run_send(db, source.request_id, record["send"]["attemptId"], caller_uid)
            if final is not None:
                body["request"] = {**record, "send": final}
                body["send"] = _send_summary(final)
        return jsonify(body), 200
    except SpecialRequestError as error:
        return error_response(error)
    except Exception as error:
        print(f"Error in decideSpecialRequest: {error}")
        print(traceback.format_exc())
        return jsonify({"success": False, "error": "internal",
                        "message": "تعذّر تنفيذ العملية"}), 500


def handle_edit_special_request_note(decoded_token, data, db):
    """Reword the note of a decision, for the person who recorded it.

    Only the note moves; who decided, when, and what stay as recorded.
    """
    try:
        request_id, slot = data.get("requestId"), data.get("slot")
        if slot not in _SLOTS or not isinstance(request_id, str) or not request_id:
            raise SpecialRequestError("invalid_payload", 400)
        note = _clean_note(data.get("note"))
        caller_uid = decoded_token.get("uid")
        field, _ = _SLOTS[slot]

        snapshot = db.collection(COLLECTION).document(request_id).get()
        if not snapshot.exists:
            raise SpecialRequestError("not_a_special_request", 404)
        family = (snapshot.to_dict() or {}).get("family") or "sales"

        user_snapshot = db.collection("users").document(caller_uid).get()
        user = user_snapshot.to_dict() if user_snapshot.exists else None

        def _edit(transaction):
            ref = db.collection(COLLECTION).document(request_id)
            current = ref.get(transaction=transaction)
            if not current.exists:
                raise SpecialRequestError("not_a_special_request", 404)
            record = current.to_dict()
            decision = record.get(field)
            if not decision or decision.get("deciderId") != caller_uid:
                raise SpecialRequestError("not_owner", 403)
            decision = {**decision, "note": note}
            now = now_iso()
            transaction.update(ref, {field: decision, "updatedAt": now})
            return {**record, field: decision, "updatedAt": now}

        # Ownership before the grant: a stranger learns nothing about grants.
        ownership = snapshot.to_dict().get(field)
        if not ownership or ownership.get("deciderId") != caller_uid:
            raise SpecialRequestError("not_owner", 403)
        if not caller_may_review(user, family):
            raise SpecialRequestError("not_permitted", 403)

        record = firestore_tx.run_transaction(db, _edit)
        return jsonify({"success": True, "request": record}), 200
    except SpecialRequestError as error:
        return error_response(error)
    except Exception as error:
        print(f"Error in editSpecialRequestNote: {error}")
        print(traceback.format_exc())
        return jsonify({"success": False, "error": "internal",
                        "message": "تعذّر تنفيذ العملية"}), 500



# ---------------------------------------------------------------------------
# Sending the approved request to operations (US3)
# ---------------------------------------------------------------------------

_FAILURE_LABELS = {
    "no_recipients": "لا يوجد مستلمون",
    "too_large": "حجم الملف أكبر من المسموح",
    "smtp": "تعذّر الإرسال عبر البريد",
    "source_missing": "التقرير محذوف",
    "internal": "خطأ داخلي",
}

# What a new attempt starts without: the last attempt's verdict. What it keeps
# is `deliveredTo`, so nobody is emailed twice.
_VERDICT_KEYS = ("sentAt", "failureReason", "failedRecipients", "recipientCount",
                 "invalidRecipients")


def _new_send_claim(attempts: int, previous: Optional[dict] = None) -> dict:
    claim = {key: value for key, value in (previous or {}).items() if key not in _VERDICT_KEYS}
    claim.update({
        "state": "sending",
        "attemptId": uuid.uuid4().hex,
        "claimedAt": now_iso(),
        "attempts": attempts,
    })
    return claim


def _send_summary(send: dict) -> dict:
    summary = {"state": send.get("state")}
    if send.get("recipientCount") is not None:
        summary["recipientCount"] = send["recipientCount"]
    if send.get("failureReason"):
        summary["failureReason"] = send["failureReason"]
    return summary


def stale_before_iso() -> str:
    """Claims made at or before this instant are stalled (a dead invocation)."""
    cutoff = datetime.now(IRAQ_TIMEZONE) - timedelta(minutes=config.SPECIAL_REQUEST_STALE_CLAIM_MINUTES)
    return to_iso(cutoff)


def _recipients(db):
    """``(valid, invalid)`` active recipients of special requests.

    Invalid addresses are returned rather than dropped, so the admin notice can
    name them. Duplicates (ignoring case) are collapsed.
    """
    valid, invalid, seen = [], [], set()
    query = (
        db.collection("email_recipients")
        .where("isActive", "==", True)
        .where("permissions", "array_contains", config.SPECIAL_REQUEST_PERMISSION)
    )
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        address = data.get("email")
        address = address.strip() if isinstance(address, str) else ""
        if not address:
            continue
        if not email_module._validate_email(address):
            invalid.append(address)
            continue
        if address.lower() in seen:
            continue
        seen.add(address.lower())
        valid.append({"email": address, "name": data.get("name") or ""})
    return valid, invalid


def _read_source_of(db, record: dict) -> Optional[Source]:
    if record.get("sourceType") == "report":
        return read_report_source(db, record.get("reportId") or "")
    return read_visit_source(db, record.get("supportRecordId") or "", record.get("visitId") or "")


def _pdf_source(db, source: Source) -> dict:
    """The document the PDF prints: the report, or the visit with its parent's
    sign-offs and resolution (they live on the support record, keyed by visit)."""
    if source.source_type == "report":
        return source.raw
    parent = db.collection("technical_support").document(source.support_record_id).get()
    parent_data = (parent.to_dict() or {}) if parent.exists else {}
    reviews = parent_data.get("reviews") or {}
    return {
        **source.raw,
        "reviews": {
            "salesManager": reviews.get(f"{source.visit_id}__salesManager"),
            "admin": reviews.get(f"{source.visit_id}__admin"),
        },
        "resolution": (parent_data.get("resolutions") or {}).get(source.visit_id),
    }


def _file_name(record: dict) -> str:
    client = "".join(ch for ch in (record.get("clientName") or "") if ch not in '\\/:*?"<>|\r\n').strip()
    return f"طلب خاص - {client or 'عميل'} - {special_request_pdf.filename_date(record)}.pdf"


def _subject(record: dict) -> str:
    day = (as_iso(record.get("reportDate")) or "")[:10].replace("-", "/")
    return "طلب خاص من العميل - {} - {} - {}".format(
        record.get("clientName") or "-", record.get("representativeName") or "-", day)


def _body(record: dict) -> str:
    day = (as_iso(record.get("reportDate")) or "")[:10].replace("-", "/")
    return "\n".join([
        "تم اعتماد طلب خاص من العميل:",
        f"العميل: {record.get('clientName') or '-'}",
        f"المندوب: {record.get('representativeName') or '-'}",
        f"تاريخ التقرير: {day}",
        "",
        "مرفق ملف الطلب والتقرير الكامل",
    ])


def _notify_admins(db, record: dict, send: dict, actor_id: str) -> None:
    """Tell the admins a send did not fully go out. Never raises."""
    try:
        lines = [
            f"العميل: {record.get('clientName') or '-'}",
            f"المندوب: {record.get('representativeName') or '-'}",
        ]
        if send.get("state") in ("failed", "partial"):
            lines.append(f"السبب: {_FAILURE_LABELS.get(send.get('failureReason'), _FAILURE_LABELS['internal'])}")
        if send.get("failedRecipients"):
            lines.append("تعذّر الإرسال إلى: " + "، ".join(send["failedRecipients"]))
        if send.get("invalidRecipients"):
            lines.append("عناوين غير صالحة: " + "، ".join(send["invalidRecipients"]))
        notifications.send_to_roles(
            db,
            roles=["admin"],
            title="تعذّر إرسال طلب خاص إلى العمليات",
            body="\n".join(lines),
            message_data=_notification_route(record),
            kind="special_request",
            source="system",
            actor_id=actor_id,
        )
    except Exception as error:  # a failed notice never fails the send
        print(f"Could not notify the admins of a failed send: {error}")


def _finish(db, request_id: str, attempt_id: str, *, delivered_now=(), failed_now=(),
            invalid=(), reason: Optional[str] = None, all_delivered: bool = False):
    """Write an attempt's outcome, only if it is still the current attempt.

    Returns ``(record, send)`` as written, or None when a newer attempt (or a
    deleted record) means this invocation must write nothing.
    """
    def _write(transaction):
        ref = db.collection(COLLECTION).document(request_id)
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists:
            return None
        record = snapshot.to_dict()
        send = record.get("send") or {}
        if send.get("attemptId") != attempt_id:
            return None
        delivered_to = list(dict.fromkeys([*(send.get("deliveredTo") or []), *delivered_now]))
        now = now_iso()
        done = {
            **{k: v for k, v in send.items() if k not in _VERDICT_KEYS},
            "deliveredTo": delivered_to,
            "failedRecipients": list(failed_now),
            "invalidRecipients": list(invalid),
        }
        if all_delivered:
            done.update(state="sent", sentAt=now, recipientCount=len(delivered_to))
        else:
            done["state"] = "partial" if delivered_to else "failed"
            done["failureReason"] = reason or "smtp"
            if delivered_to:
                done["recipientCount"] = len(delivered_to)
        transaction.update(ref, {"send": done, "updatedAt": now})
        return {**record, "send": done, "updatedAt": now}, done

    return firestore_tx.run_transaction(db, _write)


def _conclude(db, request_id: str, attempt_id: str, actor_id: str, **outcome) -> Optional[dict]:
    written = _finish(db, request_id, attempt_id, **outcome)
    if written is None:
        return None
    record, send = written
    if send["state"] in ("failed", "partial") or send.get("invalidRecipients"):
        _notify_admins(db, record, send, actor_id)
    return send


def run_send(db, request_id: str, attempt_id: str, actor_id: str = "") -> Optional[dict]:
    """Email the approved request's PDF to the current recipients.

    Run only by the invocation that holds ``attempt_id``; every write back is
    guarded on it, so a stale invocation cannot overwrite a newer attempt.
    Never raises: a failure is recorded as the request's send state and the
    caller's decision stands (FR-029). Returns the final ``send`` record, or
    None when this attempt was superseded.
    """
    # What this attempt has already emailed. If anything after the SMTP step
    # fails (writing the outcome, for one), the fallback still records them in
    # `deliveredTo`, so a retry never emails them twice.
    progress = {"delivered": []}
    try:
        return _run_send(db, request_id, attempt_id, actor_id, progress)
    except Exception as error:
        print(f"Error sending special request {request_id}: {error}")
        print(traceback.format_exc())
        try:
            return _conclude(db, request_id, attempt_id, actor_id, reason="internal",
                             delivered_now=progress["delivered"])
        except Exception as second:
            print(f"Could not record the failed send of {request_id}: {second}")
            return None


def _run_send(db, request_id: str, attempt_id: str, actor_id: str,
              progress: dict) -> Optional[dict]:
    snapshot = db.collection(COLLECTION).document(request_id).get()
    if not snapshot.exists:
        return None
    record = snapshot.to_dict()
    send = record.get("send") or {}
    if send.get("attemptId") != attempt_id or send.get("state") != "sending":
        return None

    def conclude(**outcome):
        return _conclude(db, request_id, attempt_id, actor_id, **outcome)

    source = _read_source_of(db, record)
    if source is None or source.deleted:
        return conclude(reason="source_missing")

    valid, invalid = _recipients(db)
    if not valid:
        return conclude(reason="no_recipients", invalid=invalid)

    delivered_before = {address.lower() for address in send.get("deliveredTo") or []}
    targets = [r for r in valid if r["email"].lower() not in delivered_before]
    if not targets:  # everyone currently valid already has it
        return conclude(all_delivered=True, invalid=invalid)
    target_addresses = [r["email"] for r in targets]

    try:
        attachments = special_request_pdf.fetch_attachments(source.raw)
        pdf_bytes = special_request_pdf.build_within_limit(record, _pdf_source(db, source), attachments)
    except special_request_pdf.PdfTooLarge:
        return conclude(reason="too_large", invalid=invalid, failed_now=target_addresses)

    try:
        result = email_module.send_pdf_email(
            targets, _subject(record), _body(record), pdf_bytes, _file_name(record))
    except (smtplib.SMTPException, OSError) as error:
        print(f"SMTP failed for {request_id}: {error}")
        return conclude(reason="smtp", invalid=invalid, failed_now=target_addresses)

    delivered_now = list(result.get("sent") or [])
    progress["delivered"] = delivered_now
    delivered_all = delivered_before | {address.lower() for address in delivered_now}
    return conclude(
        delivered_now=delivered_now,
        failed_now=[a for a in target_addresses if a not in delivered_now],
        invalid=invalid,
        reason="smtp",
        all_delivered=all(r["email"].lower() in delivered_all for r in valid),
    )


def handle_retry_special_request_send(decoded_token, data, db):
    """Re-send an approved request whose email did not fully go out.

    The claim succeeds only for ``failed``, ``partial`` or a stalled
    ``sending`` (a dead invocation). Every attempt targets the current valid
    recipients minus those already delivered to.
    """
    try:
        request_id = data.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            raise SpecialRequestError("invalid_payload", 400)
        caller_uid = decoded_token.get("uid")

        snapshot = db.collection(COLLECTION).document(request_id).get()
        if not snapshot.exists:
            raise SpecialRequestError("not_a_special_request", 404)
        family = (snapshot.to_dict() or {}).get("family") or "sales"

        user_snapshot = db.collection("users").document(caller_uid).get()
        user = user_snapshot.to_dict() if user_snapshot.exists else None
        if not caller_may_review(user, family):
            raise SpecialRequestError("not_permitted", 403)

        def _claim(transaction):
            ref = db.collection(COLLECTION).document(request_id)
            current = ref.get(transaction=transaction)
            if not current.exists:
                raise SpecialRequestError("not_a_special_request", 404)
            record = current.to_dict()
            send = record.get("send") or {}
            stalled = (send.get("state") == "sending"
                       and (as_iso(send.get("claimedAt")) or "") <= stale_before_iso())
            if record.get("state") != "approved" or not (
                    send.get("state") in ("failed", "partial") or stalled):
                raise SpecialRequestError(
                    "not_retryable", 409, {"send": {"state": send.get("state")}})
            claim = _new_send_claim((send.get("attempts") or 1) + 1, send)
            transaction.update(ref, {"send": claim, "updatedAt": now_iso()})
            return record, claim

        record, claim = firestore_tx.run_transaction(db, _claim)
        final = run_send(db, request_id, claim["attemptId"], caller_uid)
        return jsonify({"success": True, "send": _send_summary(final or claim)}), 200
    except SpecialRequestError as error:
        return error_response(error)
    except Exception as error:
        print(f"Error in retrySpecialRequestSend: {error}")
        print(traceback.format_exc())
        return jsonify({"success": False, "error": "internal",
                        "message": "تعذّر تنفيذ العملية"}), 500
