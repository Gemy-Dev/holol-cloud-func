"""Contract test for modules/dates — Firestore contract rule R1.

The `tasks` and `users` collections are written by three clients: the Flutter
field app, the Flutter dashboard, and this cloud function. R1 says every stored
date is a zone-less ISO-8601 string with millisecond precision — the exact
shape Dart's `DateTime.toIso8601String()` produces for a local value.

The mirror of this file is
`test/core/utils/date_conversion_contract_test.dart`, which exists in both
Flutter repositories. If you change what this asserts, change that too.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest

from modules.config import IRAQ_TIMEZONE
from modules.dates import now_iso, to_iso

# What Dart emits for a local DateTime whose microsecond component is zero.
DART_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}$")


class TestShape:
    """The written string must be byte-comparable with the Dart clients'."""

    def test_now_matches_the_dart_shape(self):
        assert DART_SHAPE.match(now_iso())

    def test_no_zone_offset(self):
        # `datetime.isoformat()` on an aware value appends `+03:00`, which
        # parses correctly on both clients but sorts after every offset-free
        # string rather than among them.
        written = now_iso()
        assert "+" not in written
        assert not written.endswith("Z")

    def test_milliseconds_not_microseconds(self):
        # Dart emits three fractional digits when microsecond == 0 and six
        # otherwise. Three always sorts correctly against either.
        assert len(now_iso().split(".")[-1]) == 3

    def test_none_round_trips_as_none(self):
        assert to_iso(None) is None


class TestZone:
    """Everything is written in Iraq time, whatever zone it arrived in."""

    def test_utc_input_is_converted_not_relabelled(self):
        # 2026-09-04T21:00Z is 2026-09-05T00:00 in Iraq — a different day. A
        # value merely stripped of its offset would land on the wrong date.
        utc = datetime(2026, 9, 4, 21, 0, tzinfo=timezone.utc)
        assert to_iso(utc) == "2026-09-05T00:00:00.000"

    def test_iraq_input_is_unchanged(self):
        local = datetime(2026, 9, 4, 10, 30, tzinfo=IRAQ_TIMEZONE)
        assert to_iso(local) == "2026-09-04T10:30:00.000"

    def test_naive_input_is_taken_as_iraq_time(self):
        assert to_iso(datetime(2026, 9, 4, 10, 30)) == "2026-09-04T10:30:00.000"

    def test_another_offset_is_converted(self):
        # UTC+14, the far end of the range, to prove the conversion is real.
        kiritimati = timezone(timedelta(hours=14))
        value = datetime(2026, 9, 4, 12, 0, tzinfo=kiritimati)
        # 12:00 at UTC+14 is 22:00 UTC the previous day, which is 01:00 on the
        # 4th in Iraq — the conversion crosses midnight twice over.
        assert to_iso(value) == "2026-09-04T01:00:00.000"


class TestOrdering:
    """Lexicographic order must match chronological order.

    This is the property the whole rule exists for: the field app orders
    `tasks` on `createdAt` and the dashboard runs range queries on
    `targetDate`, both against the stored string.
    """

    @pytest.mark.parametrize(
        "earlier,later",
        [
            (datetime(2026, 9, 4, 10, 30), datetime(2026, 9, 4, 10, 31)),
            (datetime(2026, 9, 4, 23, 59), datetime(2026, 9, 5, 0, 0)),
            (datetime(2026, 12, 31, 23, 59), datetime(2027, 1, 1, 0, 0)),
            (datetime(2026, 9, 4, 10, 30, 0, 1000),
             datetime(2026, 9, 4, 10, 30, 0, 2000)),
        ],
    )
    def test_string_order_matches_time_order(self, earlier, later):
        assert to_iso(earlier) < to_iso(later)

    def test_sorts_against_the_dart_six_digit_form(self):
        # Dart emits six fractional digits when the value has microseconds.
        # Three digits from here must still order correctly against them.
        ours = to_iso(datetime(2026, 9, 4, 10, 30, 0, 123000))
        dart_earlier = "2026-09-04T10:30:00.122999"
        dart_later = "2026-09-04T10:30:00.123001"

        assert dart_earlier < ours < dart_later

    def test_an_offset_suffix_would_have_broken_this(self):
        # Guarding the reason, not just the behaviour: with an offset suffix
        # the same two instants compare the wrong way round, because '+' (0x2B)
        # sorts before every digit.
        naive = "2026-09-04T10:30:00.000"
        with_offset = "2026-09-04T10:30:00+03:00"

        assert with_offset < naive  # the trap
        assert to_iso(datetime(2026, 9, 4, 10, 30)) == naive


class TestNoServerTimestampRemains:
    """The sentinel must not come back into a shared-collection payload."""

    def test_modules_write_no_server_timestamp(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for path in (root / "modules").glob("*.py"):
            if path.name == "dates.py":
                continue  # names it only in the docstring explaining why
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if "SERVER_TIMESTAMP" in line and not line.strip().startswith("#"):
                    offenders.append(f"{path.name}:{n}")

        assert not offenders, (
            "firestore.SERVER_TIMESTAMP stores a Firestore Timestamp, which "
            "rule R1 forbids in a shared collection. Use now_iso(). Found at: "
            + ", ".join(offenders)
        )
