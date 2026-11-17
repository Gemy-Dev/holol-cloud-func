"""The nightly repair of stored dates (rule R1, docs/firestore-contract.md).

Every date is stored as a zone-less ISO string — `2026-09-04T10:30:00.000`,
Iraq time — because Firestore orders values by type before value and strings
by their characters, and the date range queries depend on that order. Older
releases of both apps, and this function before R1, stored other forms: a
Timestamp, a UTC string ending in `Z` or `+00:00`, a space instead of the `T`,
an HTTP date (what Flask emits), a bare day. A phone that has not updated may
still write them.

Rather than refuse those writes — which would stop an old phone's offline
queue until it updates — this job rewrites them every night. It looks at the
top-level date fields (a name ending in `At` or `Date`), the ones queries
order and filter on, and at the dates inside two lists of records: a support
record's `visitHistory` and a task's `dateChanges` (whose `from` and `to` are
dates too). A list is rewritten whole, and only when one of its dates
changes. Each document is written only if it has not changed since
it was read, so a phone's edit in the meantime is never overwritten; that
document is simply repaired the next night. A value that cannot be read as a
date is left alone and reported.
"""
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from flask import jsonify

from modules.dates import to_iso

COLLECTIONS = (
    "tasks", "clients", "plans", "users", "deals", "main_opportunities",
    "reports", "technical_support", "special_requests", "material_transfers",
)

# The single field R1 lets hold a Timestamp: Firestore's own clock.
EXEMPT = {("reports", "serverCreatedAt")}

_CONTRACT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}(\d{3})?$")
_DAY_FIRST = ("%d-%m-%Y", "%d/%m/%Y")


def is_date_field(name):
    return name.endswith("At") or name.endswith("Date")


def repaired(value):
    """The contract string for ``value``; ``value`` itself when it already is
    one or is empty; None when it cannot be read as a date."""
    if value is None or value == "":
        return value
    if isinstance(value, str):
        if _CONTRACT.match(value):
            return value
        text = value.strip()
        try:
            return to_iso(datetime.fromisoformat(text.replace("Z", "+00:00")))
        except ValueError:
            pass
        try:
            return to_iso(parsedate_to_datetime(text))
        except (TypeError, ValueError, IndexError):
            pass
        for pattern in _DAY_FIRST:
            try:
                return to_iso(datetime.strptime(text, pattern))
            except ValueError:
                pass
        return None
    if isinstance(value, datetime):  # a Timestamp reads as a datetime
        return to_iso(value)
    return None


# Lists of records whose entries carry dates of their own, and the keys in
# them that are dates besides those named like one.
NESTED = {"visitHistory": set(), "dateChanges": {"from", "to"}}


def _repaired_entries(name, entries, unreadable):
    """[entries] with their dates repaired, or None when none changed."""
    if not isinstance(entries, list):
        return None
    extra = NESTED[name]
    changed, out = False, []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        fixed_entry = dict(entry)
        for key, value in entry.items():
            if not (is_date_field(key) or key in extra) or value is None or value == "":
                continue
            fixed = repaired(value)
            if fixed is None:
                unreadable.append(f"{name}[{index}].{key}")
            elif fixed != value:
                fixed_entry[key] = fixed
                changed = True
        out.append(fixed_entry)
    return out if changed else None


def changes_for(collection, data):
    """The fields of one document to rewrite, and those that cannot be read."""
    changes, unreadable = {}, []
    for name, value in data.items():
        if name in NESTED:
            entries = _repaired_entries(name, value, unreadable)
            if entries is not None:
                changes[name] = entries
            continue
        if not is_date_field(name) or (collection, name) in EXEMPT:
            continue
        # An empty date is a date not set — nothing to repair, nothing to report.
        if value is None or value == "":
            continue
        fixed = repaired(value)
        if fixed is None:
            unreadable.append(name)
        elif fixed != value:
            changes[name] = fixed
    return changes, unreadable


def _write_unless_changed(db, snapshot, changes):
    """Writes ``changes`` only if the document is still as it was read."""
    from google.api_core.exceptions import FailedPrecondition
    try:
        snapshot.reference.update(
            changes, option=db.write_option(last_update_time=snapshot.update_time))
        return True
    except FailedPrecondition:
        return False


def repair_stored_dates(db, *, dry_run=False, write=None):
    write = write or _write_unless_changed
    report = {"repaired": 0, "skippedChanged": 0, "unreadable": [], "byCollection": {}}
    for collection in COLLECTIONS:
        fixed = 0
        for snapshot in db.collection(collection).stream():
            changes, unreadable = changes_for(collection, snapshot.to_dict() or {})
            for name in unreadable:
                if len(report["unreadable"]) < 50:
                    report["unreadable"].append(f"{collection}/{snapshot.id}.{name}")
            if not changes:
                continue
            if dry_run or write(db, snapshot, changes):
                fixed += 1
            else:
                report["skippedChanged"] += 1
        report["byCollection"][collection] = fixed
        report["repaired"] += fixed
    return report


def handle_repair_stored_dates(db, data=None):
    dry_run = bool((data or {}).get("dryRun"))
    report = repair_stored_dates(db, dry_run=dry_run)
    print(f"🗓️ Date repair ({'dry run' if dry_run else 'applied'}): {report}")
    return jsonify({"success": True, "dryRun": dry_run, **report}), 200
