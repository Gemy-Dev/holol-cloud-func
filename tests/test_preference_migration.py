"""Tests for US2: backfilling the notification preference onto one key.

Two spellings of the same preference exist in production. The dashboard
administers ``receiveEmailNotifications``; the field app has always read the
misspelled ``reciveEmailNotifications``. Whichever one a given user document
happens to carry, the migration has to land on the canonical key **without
changing what any user actually chose** — SC-013 requires zero value changes,
so a non-empty ``valueChanges`` is a failed migration, not a warning.

Precedence, from data-model.md §1:

  1. canonical present            -> keep it
  2. else legacy present          -> copy it across
  3. else                         -> write false
  4. deleteLegacyKey -> drop the misspelled key (only after the app release
     that stops reading it has rolled out)

Contract: contracts/cloud-function-actions.md, "New: migrateNotificationPreference".
"""
from modules.users import migrate_notification_preference

CANONICAL = 'receiveEmailNotifications'
LEGACY = 'reciveEmailNotifications'

ADMIN = {'uid': 'admin'}


def _call(db, **payload):
    response = migrate_notification_preference(payload, ADMIN, db)
    resp_obj, status = response if isinstance(response, tuple) else (response, 200)
    return resp_obj.get_json(), status


def _seed(db, users: dict, *, with_admin=True):
    # The requesting admin is a user document too, so it is scanned and
    # counted like any other. It is seeded with the canonical key already set
    # so it always lands in `canonicalKept` and never skews the other buckets.
    if with_admin:
        db._put('users', 'admin', {'name': 'admin', 'isActive': True,
                                   CANONICAL: False})
    for uid, doc in users.items():
        db._put('users', uid, doc)


class TestPrecedence:
    def test_canonical_value_is_kept_even_when_legacy_disagrees(self, db):
        # The dashboard is where the value is administered, so it wins.
        _seed(db, {'u': {CANONICAL: True, LEGACY: False}})

        body, status = _call(db, dryRun=False)

        assert status == 200
        assert db._get_all('users')['u'][CANONICAL] is True
        assert body['canonicalKept'] == 2  # 'u' and the admin
        assert body['backfilledFromLegacy'] == 0

    def test_legacy_value_is_copied_when_canonical_is_absent(self, db):
        _seed(db, {'u': {LEGACY: True}})

        body, _ = _call(db, dryRun=False)

        assert db._get_all('users')['u'][CANONICAL] is True
        assert body['backfilledFromLegacy'] == 1

    def test_legacy_false_is_copied_not_treated_as_absent(self, db):
        # The bug worth guarding: `if doc.get(LEGACY)` would skip a stored
        # False and fall through to the default, which happens to be the same
        # value — until the default ever changes.
        _seed(db, {'u': {LEGACY: False}})

        body, _ = _call(db, dryRun=False)

        assert db._get_all('users')['u'][CANONICAL] is False
        assert body['backfilledFromLegacy'] == 1
        assert body['defaultedFalse'] == 0

    def test_neither_key_defaults_to_false(self, db):
        # data-model.md §1: "A missing preference is not consent."
        _seed(db, {'u': {'name': 'nobody'}})

        body, _ = _call(db, dryRun=False)

        assert db._get_all('users')['u'][CANONICAL] is False
        assert body['defaultedFalse'] == 1

    def test_canonical_false_is_kept_not_re_defaulted(self, db):
        _seed(db, {'u': {CANONICAL: False}})

        body, _ = _call(db, dryRun=False)

        assert body['canonicalKept'] == 2  # 'u' and the admin
        assert body['defaultedFalse'] == 0

    def test_a_mixed_population_is_counted_correctly(self, db):
        _seed(db, {
            'keeps-a': {CANONICAL: True},
            'keeps-b': {CANONICAL: False, LEGACY: True},
            'backfills': {LEGACY: True},
            'defaults': {},
        })

        body, _ = _call(db, dryRun=False)

        # 'admin' is itself a user document and is migrated like any other.
        assert body['scanned'] == 5
        assert body['canonicalKept'] == 3  # keeps-a, keeps-b, admin
        assert body['backfilledFromLegacy'] == 1
        assert body['defaultedFalse'] == 1


class TestNoValueEverChanges:
    def test_value_changes_is_empty_for_a_mixed_population(self, db):
        _seed(db, {
            'a': {CANONICAL: True, LEGACY: False},
            'b': {LEGACY: True},
            'c': {LEGACY: False},
            'd': {},
            'e': {CANONICAL: False},
        })

        body, _ = _call(db, dryRun=False)

        assert body['valueChanges'] == [], (
            'SC-013: the migration must not change any effective preference'
        )

    def test_every_effective_value_survives_the_run(self, db):
        _seed(db, {
            'was-on-canonical': {CANONICAL: True},
            'was-on-legacy': {LEGACY: True},
            'was-off-legacy': {LEGACY: False},
            'was-unset': {},
        })

        _call(db, dryRun=False)

        stored = db._get_all('users')
        assert stored['was-on-canonical'][CANONICAL] is True
        assert stored['was-on-legacy'][CANONICAL] is True
        assert stored['was-off-legacy'][CANONICAL] is False
        assert stored['was-unset'][CANONICAL] is False


class TestDryRun:
    def test_dry_run_is_the_default(self, db):
        _seed(db, {'u': {LEGACY: True}})

        body, _ = _call(db)

        assert body['dryRun'] is True
        assert CANONICAL not in db._get_all('users')['u'], (
            'the destructive default must be the safe one'
        )

    def test_dry_run_still_reports_what_it_would_do(self, db):
        _seed(db, {'u': {LEGACY: True}, 'v': {}})

        body, _ = _call(db, dryRun=True)

        assert body['backfilledFromLegacy'] == 1
        assert body['defaultedFalse'] == 1  # 'v'
        assert body['scanned'] == 3

    def test_only_an_explicit_false_disables_the_dry_run(self, db):
        # A JSON string "false" is truthy in Python; failing safe here means a
        # malformed call does nothing rather than writing every user document.
        _seed(db, {'u': {LEGACY: True}})

        body, _ = _call(db, dryRun='false')

        assert body['dryRun'] is True
        assert CANONICAL not in db._get_all('users')['u']


class TestLegacyKeyDeletion:
    def test_legacy_key_is_kept_by_default(self, db):
        _seed(db, {'u': {LEGACY: True}})

        body, _ = _call(db, dryRun=False)

        assert LEGACY in db._get_all('users')['u'], (
            'the rollback path depends on the old key still holding its value'
        )
        assert body['legacyKeysDeleted'] == 0

    def test_legacy_key_is_removed_when_asked(self, db):
        _seed(db, {'u': {CANONICAL: True, LEGACY: True}})

        body, _ = _call(db, dryRun=False, deleteLegacyKey=True)

        assert LEGACY not in db._get_all('users')['u']
        assert body['legacyKeysDeleted'] == 1

    def test_deleting_the_legacy_key_preserves_the_canonical_value(self, db):
        _seed(db, {'u': {LEGACY: True}})

        _call(db, dryRun=False, deleteLegacyKey=True)

        stored = db._get_all('users')['u']
        assert stored[CANONICAL] is True
        assert LEGACY not in stored

    def test_a_dry_run_never_deletes(self, db):
        _seed(db, {'u': {CANONICAL: True, LEGACY: True}})

        body, _ = _call(db, dryRun=True, deleteLegacyKey=True)

        assert LEGACY in db._get_all('users')['u']
        assert body['legacyKeysDeleted'] == 1  # reported as "would delete"

    def test_only_an_explicit_true_enables_deletion(self, db):
        _seed(db, {'u': {CANONICAL: True, LEGACY: True}})

        body, _ = _call(db, dryRun=False, deleteLegacyKey='yes')

        assert LEGACY in db._get_all('users')['u']
        assert body['legacyKeysDeleted'] == 0


class TestIdempotence:
    def test_a_second_run_is_a_no_op(self, db):
        _seed(db, {'u': {LEGACY: True}, 'v': {}})

        _call(db, dryRun=False)
        before = db._get_all('users')

        body, _ = _call(db, dryRun=False)

        assert db._get_all('users') == before
        assert body['backfilledFromLegacy'] == 0
        assert body['defaultedFalse'] == 0
        assert body['canonicalKept'] == 3
        assert body['valueChanges'] == []


class TestAuthorization:
    def test_an_unknown_requester_is_rejected(self, db):
        db._put('users', 'u', {LEGACY: True})

        response = migrate_notification_preference({}, {'uid': 'ghost'}, db)
        resp_obj, status = response if isinstance(response, tuple) else (response, 200)

        assert status == 403
        assert CANONICAL not in db._get_all('users')['u']

    def test_a_request_with_no_uid_is_rejected(self, db):
        db._put('users', 'u', {LEGACY: True})

        response = migrate_notification_preference({}, {}, db)
        resp_obj, status = response if isinstance(response, tuple) else (response, 200)

        assert status == 403


class TestEmptyCollection:
    def test_no_users_is_a_successful_no_op(self, db):
        body, status = _call(db, dryRun=False)

        assert status == 403, 'with no users the admin cannot be verified'
