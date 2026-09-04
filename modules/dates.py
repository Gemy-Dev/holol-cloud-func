"""Writing dates into the shared Firestore documents.

The `tasks`, `users`, `clients` and `plans` collections are written by three
clients: the Flutter field app, the Flutter dashboard, and this cloud function.
All three must agree on how a date is stored, and the agreement is written down
in `docs/firestore-contract.md` in either Flutter repository.

**Rule R1: a stored date is an ISO-8601 string, never a Firestore Timestamp.**

Firestore orders values by *type group* before value, so a field holding both
representations splits every `orderBy` into two disjoint blocks and makes every
range query match only one of them. The dashboard's task scheduler used to
carry a workaround for exactly this — two queries, one with `Timestamp` bounds
and one with string bounds, merged by document id — because `targetDate` was a
`Timestamp` for planned tasks and a string for everything else.

`firestore.SERVER_TIMESTAMP` stores a `Timestamp`, so it is not usable here.
The clock is barely affected by the change: this still runs on a server, so the
value is a server clock either way — resolved by this instance when the payload
is built rather than by Firestore at commit time. What is given up is a few
milliseconds of precision about *when* the write landed, which nothing reads.
The one field that genuinely needs Firestore's own clock, `reports
.serverCreatedAt`, is written by the app and is the single documented exception
to R1.

**The shape matters as much as the type.** Dart's `DateTime.toIso8601String()`
on a local value emits `2026-09-04T10:30:00.000` — no zone offset, at least
three fractional digits. Python's default `datetime.isoformat()` on an aware
value emits `2026-09-04T07:30:00+00:00`, which parses correctly on both clients
but **sorts differently as a string**, and lexicographic order is the entire
point of R1. So the value is converted to Iraq time, stripped of its offset,
and emitted with millisecond precision.
"""

from datetime import datetime

from modules.config import IRAQ_TIMEZONE


def to_iso(value):
    """A datetime as the string both Flutter clients write, or None.

    Args:
        value: an aware or naive ``datetime``, or None. A naive value is taken
            to already be in Iraq time, which is what every naive datetime in
            this codebase means.

    Returns:
        ``2026-09-04T10:30:00.000``, or None when ``value`` is None.
    """
    if value is None:
        return None

    if value.tzinfo is not None:
        value = value.astimezone(IRAQ_TIMEZONE)

    # Zone-less, because Dart writes a local DateTime and an offset suffix
    # would sort after every offset-free string rather than among them.
    return value.replace(tzinfo=None).isoformat(timespec="milliseconds")


def now_iso():
    """Now, in the shape the contract requires.

    The replacement for ``firestore.SERVER_TIMESTAMP`` in any payload written
    to a shared collection.
    """
    return to_iso(datetime.now(IRAQ_TIMEZONE))
