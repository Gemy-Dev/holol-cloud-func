"""The stored record of every push notification this function sends.

Every notification in the system leaves through this function — the field app,
the dashboard and Cloud Scheduler all call it rather than FCM — so this is the
one place that can guarantee each send is written down, whatever started it.
The clients never write the collection; the Firestore rules deny them.

Document shape, ``notifications/{id}``:

    id            the document id, also sent to the device as ``notificationId``
    title, body   what the recipient saw
    data          the FCM data payload — the ``action`` and record ids that open
                  the record when the notification is tapped
    kind          direct | broadcast | review | daily_tasks | apk_update |
                  review_request | review_digest | special_request
    source        app | dashboard | system
    senderId      uid from the verified ID token; None for the scheduler
    senderName    the sender's name at send time, for display
    audience      "all" or "users"
    recipientIds  ["all"] for a broadcast, else the addressed uids
    createdAt     contract ISO string (R1), Iraq time
    event         what happened (``IMPORTANT_EVENTS``), or None
    important     whether the app lists it — see ``classify``
    delivery      {successCount, failureCount[, reason]} once the send returns

Read state is kept on each phone, never here. A person sees a notification
when it is addressed to them, or when it is a broadcast somebody else sent —
the rule ``is_visible_to`` spells out, and the field app applies to its list.
Only important ones are listed and counted on the badge; the rest are a push
and nothing more.
"""

from datetime import datetime

from firebase_admin import messaging
from google.api_core.exceptions import AlreadyExists

from modules.config import IRAQ_TIMEZONE
from modules.dates import now_iso, to_iso

COLLECTION = "notifications"

# The recipientIds entry that marks a broadcast. The app queries
# `recipientIds array-contains-any [uid, "all"]`, so a broadcast and a
# notification addressed to the viewer come back from one query.
ALL = "all"

# The events the app's notification page lists. Everything else — date
# changes, deletions, edits, reminders — is pushed but not listed, so the
# page holds only what someone has to act on.
IMPORTANT_EVENTS = frozenset({
    "task_completed",
    "activity_added",
    "opportunity_added",
    "review",
    "support_record_added",
    "support_visit_added",
})

# App builds released before senders named their event, told apart by the
# exact title each one sends. Matched only for the field app: the dashboard is
# always the latest build, and an admin's free-text title must not pass for
# one of these.
_LEGACY_APP_TITLES = {
    "إنجاز مهمة": "task_completed",
    "إضافة نشاط": "activity_added",
    "تمت مراجعة التقرير": "review",
    "إضافة سجل دعم فني جديد": "support_record_added",
    "🔔 Add: Visit Technical Support": "support_visit_added",
    "🔔 Add: Main Opportunity": "opportunity_added",
}


def classify(*, title, kind, data, source):
    """The event a notification announces, or None when it names none."""
    event = (data or {}).get("event")
    if event:
        return str(event)
    if kind == "review":
        return "review"
    if source == "app":
        return _LEGACY_APP_TITLES.get(title)
    return None


def is_important(notification):
    """Whether a stored notification is listed and counted.

    Records written before ``important`` was stored are classified the way
    they would be now.
    """
    if "important" in notification:
        return notification["important"] is True
    event = classify(
        title=notification.get("title"),
        kind=notification.get("kind"),
        data=notification.get("data"),
        source=notification.get("source"),
    )
    return event in IMPORTANT_EVENTS


def request_source(data):
    """Which client made the request: ``app`` or ``dashboard``.

    Builds released before this field existed send no ``source``. The field
    app's transport has always added ``userId`` to every request and the
    dashboard's never has, so that tells the two apart.
    """
    source = (data or {}).get("source")
    if source in ("app", "dashboard"):
        return source
    return "app" if (data or {}).get("userId") else "dashboard"


def sender_name(db, sender_id):
    """The sender's display name, or None. Never raises."""
    if not sender_id:
        return None
    try:
        snapshot = db.collection("users").document(sender_id).get()
        if not snapshot.exists:
            return None
        return (snapshot.to_dict() or {}).get("name")
    except Exception as error:
        print(f"⚠️ Could not read sender {sender_id}: {error}")
        return None


# Returned by ``record(create_only=True)`` when the fixed id is already taken.
# Distinct from ``None`` (the write failed): a taken id means "already sent, do
# not push again", a failed write means "push anyway" (research R16).
ALREADY_RECORDED = object()


def record(db, *, title, body, data, kind, source, sender_id, recipient_ids,
           notification_id=None, create_only=False):
    """Write the record and return its id, or None if the write failed.

    Written before the send, so a send that dies halfway is still on record.
    A failed write never blocks the push: the alert is what the caller is
    waiting on, and losing it too would compound one failure into two.

    Args:
        recipient_ids: ``[ALL]`` for a broadcast, else the addressed uids.
        notification_id: a fixed document id, for callers that dedupe on it.
        create_only: claim ``notification_id`` atomically with ``create()``;
            returns ``ALREADY_RECORDED`` when it exists. Needs a fixed id.
    """
    try:
        collection = db.collection(COLLECTION)
        ref = collection.document(notification_id) if notification_id else collection.document()
        event = classify(title=title, kind=kind, data=data, source=source)
        payload = {
            "id": ref.id,
            "title": title,
            "body": body,
            "data": dict(data or {}),
            "kind": kind,
            "source": source,
            "senderId": sender_id,
            "senderName": sender_name(db, sender_id),
            "audience": "all" if ALL in recipient_ids else "users",
            "recipientIds": list(recipient_ids),
            "createdAt": now_iso(),
            "event": event,
            "important": event in IMPORTANT_EVENTS,
        }
        if create_only:
            ref.create(payload)
        else:
            ref.set(payload)
        return ref.id
    except AlreadyExists:
        return ALREADY_RECORDED
    except Exception as error:
        print(f"⚠️ Could not record notification '{title}': {error}")
        return None


def record_delivery(db, notification_id, *, success_count, failure_count, reason=None):
    """Attach the send's outcome to its record. Never raises."""
    if not notification_id:
        return
    delivery = {"successCount": success_count, "failureCount": failure_count}
    if reason:
        delivery["reason"] = reason
    try:
        db.collection(COLLECTION).document(notification_id).update({"delivery": delivery})
    except Exception as error:
        print(f"⚠️ Could not record delivery for {notification_id}: {error}")


def is_visible_to(notification, user_id):
    """Whether ``user_id`` sees ``notification`` in their list.

    Addressed to them, or a broadcast someone else sent. The field app's
    notification page applies the same rule; the two must not drift, or the
    icon badge and the page count different things.
    """
    recipients = notification.get("recipientIds") or []
    if user_id in recipients:
        return True
    return ALL in recipients and notification.get("senderId") != user_id


def today_start_iso():
    """Midnight today, Iraq time, as a contract string — the lower bound of
    the section the app lists as الحالية."""
    midnight = datetime.now(IRAQ_TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    return to_iso(midnight)


def todays_counts(db, user_ids):
    """How many of today's important notifications each of ``user_ids`` sees.

    The number on the app icon while the app is closed. The app lowers it to
    the unread ones when it opens: which were opened is known only on the
    phone. One read of today's records serves every recipient. On failure
    returns ``{}``, so the send carries no badge and the icon keeps its last
    value rather than showing a wrong one.
    """
    counts = {user_id: 0 for user_id in user_ids if user_id}
    if not counts:
        return {}
    try:
        todays = db.collection(COLLECTION).where("createdAt", ">=", today_start_iso()).stream()
        for doc in todays:
            notification = doc.to_dict() or {}
            if not is_important(notification):
                continue
            for user_id in counts:
                if is_visible_to(notification, user_id):
                    counts[user_id] += 1
    except Exception as error:
        print(f"⚠️ Could not count today's notifications: {error}")
        return {}
    return counts


def badge_config(count):
    """The ``apns``/``android`` message options that put ``count`` on the icon.

    Empty when ``count`` is None. iOS always shows the number; on Android it
    depends on the launcher — Samsung and Xiaomi show it, stock launchers show
    a dot.
    """
    if count is None:
        return {}
    return {
        "apns": messaging.APNSConfig(payload=messaging.APNSPayload(aps=messaging.Aps(badge=count))),
        "android": messaging.AndroidConfig(
            notification=messaging.AndroidNotification(notification_count=count)
        ),
    }
