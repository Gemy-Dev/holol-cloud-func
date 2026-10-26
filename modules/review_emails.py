"""The email that goes out with a review sign-off.

A review on a sales report, a technical-support report or a support visit is
emailed to the representative who did the work and to every holder of the
other reviewer role: an administrator's review reaches the sales managers, a
sales manager's reaches the administrators — the same audience as the push.

Built from the stored record rather than the caller's text, so the dashboard
and the field app send the same email, and the note and solution in it are the
ones on record. Every send is logged in ``review_emails``, one document per
signed slot, so a repeated call for the same sign-off never emails twice.
"""
import smtplib
import traceback
from html import escape

from modules import email as mail
from modules import notifications
from modules import special_request_pdf as labels
from modules import special_requests as sr
from modules.dates import as_iso, now_iso

LOG_COLLECTION = "review_emails"

SLOTS = ("salesManager", "admin")
_ROLE_LABELS = {"salesManager": "مدير المبيعات", "admin": "مسؤول النظام"}


def _id(value):
    """``value`` when it can name a document, else None."""
    return value if isinstance(value, str) and value and "/" not in value else None


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _day(value):
    return (as_iso(value) or "")[:10]


def _minute(value):
    return (as_iso(value) or "")[:16].replace("T", " ")


def _signed_slot(reviews, actor_id, hint):
    """The slot ``actor_id`` signed, trying ``hint`` (the caller's slot) first."""
    order = (hint, *[s for s in SLOTS if s != hint]) if hint in SLOTS else SLOTS
    for slot in order:
        review = reviews.get(slot)
        if isinstance(review, dict) and review.get("reviewerId") == actor_id:
            return slot
    return None


class _Reviewed:
    """A report or visit, read for the email."""

    def __init__(self, *, key, type_label, worker_label, rep_id, rep_name, client,
                 activity_label, activity, day_label, day, reviews, problem,
                 suggested, solution, solved_by):
        self.key = key
        self.type_label = type_label
        self.worker_label = worker_label
        self.rep_id = rep_id
        self.rep_name = rep_name
        self.client = client
        self.activity_label = activity_label
        self.activity = activity
        self.day_label = day_label
        self.day = day
        self.reviews = reviews
        self.problem = problem
        self.suggested = suggested
        self.solution = solution
        self.solved_by = solved_by


def _read_report(db, report_id):
    snapshot = db.collection("reports").document(report_id).get()
    if not snapshot.exists:
        return None
    raw = snapshot.to_dict() or {}
    family = sr.family_of(raw)
    activity = raw.get("type") or ""
    has_problem = raw.get("hasProblems") is True or bool(_text(raw.get("problems")))
    return _Reviewed(
        key=f"report__{report_id}",
        type_label=labels.type_label_for("report", family),
        worker_label="الفني" if family == "technical" else "المندوب",
        rep_id=raw.get("assignedToId") or "",
        rep_name=_text(raw.get("userName")),
        client=sr._client_name(db, raw),
        activity_label="النشاط",
        activity=labels._ACTIVITY_LABELS.get(activity, activity),
        day_label="تاريخ التقرير",
        day=_day(raw.get("plannedDate") or raw.get("createdAt")),
        reviews={"salesManager": raw.get("salesManagerReview"),
                 "admin": raw.get("adminReview")},
        problem=_text(raw.get("problems")) if has_problem else "",
        suggested=_text(raw.get("suggestSolutions")),
        solution=_text(raw.get("solutionDetails")) if raw.get("solved") is True else "",
        solved_by=_text(raw.get("solvedByName")),
    )


def _latest_visit_signed_by(reviews_map, actor_id):
    """The visit whose newest sign-off is ``actor_id``'s, for callers that do not
    name the visit. Dates are contract strings, so they order as text."""
    latest = None
    for key, review in reviews_map.items():
        visit_id, _, slot = key.rpartition("__")
        if slot in SLOTS and isinstance(review, dict) and review.get("reviewerId") == actor_id:
            stamp = as_iso(review.get("reviewedAt")) or ""
            if latest is None or stamp > latest[0]:
                latest = (stamp, visit_id)
    return latest[1] if latest else None


def _read_visit(db, support_record_id, visit_id, actor_id):
    snapshot = db.collection("technical_support").document(support_record_id).get()
    if not snapshot.exists:
        return None
    parent = snapshot.to_dict() or {}
    reviews_map = parent.get("reviews") or {}
    visit_id = visit_id or _latest_visit_signed_by(reviews_map, actor_id)
    entry = next((v for v in sr.visit_entries(parent.get("visitHistory"))
                  if visit_id and v.get("id") == visit_id), None)
    if entry is None:
        return None
    report = entry.get("completionReport") or {}
    resolution = (parent.get("resolutions") or {}).get(visit_id) or {}
    has_problem = report.get("hasProblem") is True or bool(_text(report.get("problemDescription")))
    visit_type = entry.get("visitType") or ""
    return _Reviewed(
        key=f"visit__{support_record_id}__{visit_id}",
        type_label=labels.type_label_for("visit", "technical"),
        worker_label="الفني",
        rep_id=entry.get("technicianId") or parent.get("technicianId") or "",
        rep_name=_text(entry.get("technicianName") or parent.get("technicianName")),
        client=_text(parent.get("clientName")),
        activity_label="نوع الزيارة",
        activity=labels._VISIT_TYPE_LABELS.get(visit_type, visit_type),
        day_label="تاريخ الزيارة",
        day=_day(entry.get("visitDate") or entry.get("createdAt")),
        reviews={slot: reviews_map.get(f"{visit_id}__{slot}") for slot in SLOTS},
        problem=_text(report.get("problemDescription")) if has_problem else "",
        suggested=_text(report.get("suggestedSolution")),
        solution=_text(resolution.get("solutionDetails")),
        solved_by=_text(resolution.get("resolvedByName")),
    )


def _read_reviewed(db, route, actor_id):
    """The record ``route`` (the notification's routing payload) points at."""
    report_id = _id(route.get("reportId"))
    if report_id:
        return _read_report(db, report_id)
    support_record_id = _id(route.get("supportRecordId"))
    if support_record_id:
        return _read_visit(db, support_record_id, _id(route.get("visitId")), actor_id)
    return None


def _unreachable_reason(user):
    """Why ``user`` gets no email, or None when they do."""
    if user.get("isActive") is not True:
        return "inactive"
    if user.get("receiveEmailNotifications") is not True:
        return "preference_off"
    if not mail._validate_email(_text(user.get("email"))):
        return "no_email"
    return None


def _recipients(db, *, audience_role, rep_id, actor_id):
    """The representative and the holders of ``audience_role``, minus the actor.

    Returns ``(recipients, skipped)``: one ``{"userId", "email", "name"}`` per
    address, and ``{"userId", "reason"}`` for each user left out.
    """
    users = db.collection("users")
    candidates = {}
    skipped = []
    if rep_id:
        snapshot = users.document(rep_id).get()
        if snapshot.exists:
            candidates[rep_id] = snapshot.to_dict() or {}
        else:
            skipped.append({"userId": rep_id, "reason": "not_found"})
    if audience_role in notifications._ROLE_SPELLINGS:
        spellings = notifications._ROLE_SPELLINGS[audience_role]
        for user_doc in users.where("role", "in", spellings).stream():
            candidates.setdefault(user_doc.id, user_doc.to_dict() or {})

    recipients = []
    seen = set()
    for user_id, user in candidates.items():
        if user_id == actor_id:
            continue
        reason = _unreachable_reason(user)
        if reason:
            skipped.append({"userId": user_id, "reason": reason})
            continue
        address = _text(user.get("email"))
        # Two accounts sharing one mailbox still get one email.
        if address.lower() in seen:
            continue
        seen.add(address.lower())
        recipients.append({"userId": user_id, "email": address, "name": _text(user.get("name"))})
    return recipients, skipped


def _compose(item, review, actor_role):
    """The subject, plain-text body and HTML body of the email."""
    role_label = _ROLE_LABELS[actor_role]
    reviewer = _text(review.get("reviewerName")) or role_label
    signed = [f"{_ROLE_LABELS[slot]}: {'تمت' if item.reviews.get(slot) else 'بانتظار المراجعة'}"
              for slot in SLOTS]
    solution = item.solution
    if solution and item.solved_by:
        solution = f"{solution} (سجّله {item.solved_by})"
    elif not solution and item.problem:
        solution = "لم يُسجَّل حل بعد"

    rows = [
        ("نوع التقرير", item.type_label),
        (item.worker_label, item.rep_name),
        ("العميل", item.client),
        (item.activity_label, item.activity),
        (item.day_label, item.day),
        ("المراجِع", f"{reviewer} ({role_label})"),
        ("تاريخ المراجعة", _minute(review.get("reviewedAt"))),
        ("ملاحظة المراجعة", _text(review.get("note")) or "لا توجد ملاحظة"),
        ("المشكلة", item.problem),
        ("الحل المقترح", item.suggested),
        ("الحل", solution),
        ("حالة المراجعات", " | ".join(signed)),
    ]
    rows = [(label, value) for label, value in rows if value]

    intro = f"قام {reviewer} ({role_label}) بمراجعة {item.type_label}."
    outro = "يمكنك الاطلاع على التفاصيل من التطبيق أو لوحة التحكم."
    text = "\n".join([intro, "", *[f"{label}: {value}" for label, value in rows], "", outro])
    html = (
        f"<p>{escape(intro)}</p>"
        '<table style="border-collapse:collapse">'
        + "".join(
            '<tr><td style="padding:4px 0 4px 16px;color:#555;vertical-align:top;'
            f'white-space:nowrap"><b>{escape(label)}</b></td>'
            f'<td style="padding:4px 0;white-space:pre-wrap">{escape(value)}</td></tr>'
            for label, value in rows
        )
        + f"</table><p>{escape(outro)}</p>"
    )
    subject = f"مراجعة {role_label} ل{item.type_label}"
    subject += f" - {item.rep_name}" if item.rep_name else ""
    subject += f" - {item.day}" if item.day else ""
    return subject, text, html


def send_review_email(db, *, actor_id, actor_role, slot_hint, route):
    """Email a review sign-off to its audience. Never raises.

    Args:
        actor_id: the reviewer, from the verified token.
        actor_role: ``admin`` or ``salesManager`` — whoever reviewed, whichever
            slot they signed, decides that the other role is told.
        slot_hint: the slot the caller says was signed; tried first.
        route: the notification's routing payload (``reportId``, or
            ``supportRecordId`` and ``visitId``).

    Returns:
        A summary for the response: ``sent``, ``failed`` and ``skipped``
        recipients, or ``skippedReason`` / ``error`` when nothing was sent.
    """
    try:
        if actor_role not in SLOTS:
            return {"skippedReason": "not_a_reviewer"}
        item = _read_reviewed(db, route if isinstance(route, dict) else {}, actor_id)
        if item is None:
            return {"skippedReason": "record_not_found"}
        slot = _signed_slot(item.reviews, actor_id, slot_hint)
        if slot is None:
            return {"skippedReason": "not_signed"}
        review = item.reviews[slot]
        reviewed_at = as_iso(review.get("reviewedAt")) or ""

        log_ref = db.collection(LOG_COLLECTION).document(f"{item.key}__{slot}")
        prior = log_ref.get()
        if prior.exists:
            prior_data = prior.to_dict() or {}
            if prior_data.get("status") == "sent" and prior_data.get("reviewedAt") == reviewed_at:
                return {"skippedReason": "already_sent"}

        audience_role = "salesManager" if actor_role == "admin" else "admin"
        recipients, skipped = _recipients(
            db, audience_role=audience_role, rep_id=item.rep_id, actor_id=actor_id)
        for entry in skipped:
            print(f"📭 Review email not sent to {entry['userId']}: {entry['reason']}")
        if not recipients:
            return {"sent": [], "failed": [], "skipped": skipped}

        config_valid, config_error = mail._check_email_config()
        if not config_valid:
            print(f"❌ Review email not sent: {config_error}")
            return {"error": "email_not_configured", "skipped": skipped}

        subject, text, html = _compose(item, review, actor_role)
        try:
            outcome = mail.send_text_email(recipients, subject, text, html=html)
        except (smtplib.SMTPException, OSError) as error:
            # Connecting or logging in failed: nothing went out. Logged as
            # failed, so the next call for this sign-off tries again.
            print(f"❌ Review email SMTP failure: {error}")
            outcome = {"sent": [], "failed": [{"email": r["email"], "error": str(error)}
                                             for r in recipients]}

        log_ref.set({
            "status": "sent" if outcome["sent"] else "failed",
            "reviewedAt": reviewed_at,
            "slot": slot,
            "reviewerId": actor_id,
            "subject": subject,
            "sentTo": outcome["sent"],
            "failed": outcome["failed"],
            "skipped": skipped,
            "updatedAt": now_iso(),
        })
        print(f"✅ Review email sent to {len(outcome['sent'])}, "
              f"{len(outcome['failed'])} failed, {len(skipped)} skipped")
        return {"sent": outcome["sent"], "failed": outcome["failed"], "skipped": skipped}
    except Exception as error:
        print(f"❌ Review email failed: {error}")
        print(traceback.format_exc())
        return {"error": str(error)}

