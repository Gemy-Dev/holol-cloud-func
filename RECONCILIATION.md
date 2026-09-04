# Deployment reconciliation — port-in list

**Status: nothing deployed, nothing backfilled. This is the list only.**

The deployed `app` function is not built from any committed state of this
repository. Code was deployed from a working tree and never committed, so
**production is ahead of `main` in three modules and behind it in one.**
Running `deploy.sh` from the checkout as it stands would delete three live
functions and four live test files.

This document is the ordered list of what has to move, in which direction,
before anything is deployed.

## Provenance

| | |
|---|---|
| Function | `app`, project `test-medical-80e1b`, region `us-central1` |
| Deployed | 2026-09-02T11:13:44Z, revision `app-00009-vaf`, state ACTIVE |
| Source | `gs://gcf-v2-sources-27531863258-us-central1/app/function-source.zip` |
| Generation | `1788347544608541` |
| Checkout | branch `2029-sync-influencer-tasks`, HEAD `05faf89` |

Pull it again with:

```bash
gcloud functions describe app --region=us-central1 --project=test-medical-80e1b \
  --format="value(buildConfig.source.storageSource.bucket,buildConfig.source.storageSource.object,buildConfig.source.storageSource.generation)"
gsutil cp "gs://<bucket>/<object>#<generation>" /tmp/deployed.zip && unzip -q /tmp/deployed.zip -d /tmp/deployed
```

Never infer what is live from `updateTime` or from the checkout.

---

## Summary

| File | Live-only | Local-only | Direction |
|---|---|---|---|
| `main.py` | 11 | 1 | **port in** |
| `modules/users.py` | 145 | 4 | **port in** (local 4 = the R1 date fix, keep) |
| `modules/notifications.py` | 143 | 24 | **port in** |
| `modules/tasks.py` | 67 | 86 | local is ahead — **nothing to port in** |
| `tests/conftest.py` | 20 | 16 | **port in** (local 16 = R1 test helper, keep) |
| `tests/test_notification_audience.py` | 298 | — | **port in whole file** |
| `tests/test_notification_single_target.py` | 176 | — | **port in whole file** |
| `tests/test_notification_token_pruning.py` | 146 | — | **port in whole file** |
| `tests/test_preference_migration.py` | 259 | — | **port in whole file** |
| `tests/test_reconcile_delete.py` | 1 | 4 | local is ahead |
| `tests/test_reconcile_priority.py` | 1 | 4 | local is ahead |
| `tests/test_regenerate_plan_tasks.py` | 24 | 86 | local is ahead |
| `modules/dates.py` | — | new | local only, ships on next deploy |
| `tests/test_dates_contract.py` | — | new | local only |
| `tests/test_get_tasks_paginated.py` | — | new | local only, untracked |

Three functions and three module constants are live and absent from the
checkout:

| Live-only function | Module | Size |
|---|---|---|
| `migrate_notification_preference` | `users.py` | 128 lines |
| `_prune_token` | `notifications.py` | 20 lines |
| `_is_permanently_invalid_token` | `notifications.py` | 19 lines |

Nothing in an existing module is local-only. The only functions the checkout
adds are `to_iso` and `now_iso` in the new `modules/dates.py`, which production
has never seen.

---

## What to port in, in order

The order is a dependency order, not a preference. Each step's tests pass
before the next begins.

### 1 · `tests/conftest.py` — `DELETE_FIELD` support

The live `_FakeDocRef.update` honours Firestore's real field-deletion sentinel;
the checkout's stores it as a value. Port in:

- the `DELETE_FIELD` import block (`from firebase_admin import firestore as
  _firestore`, falling back to a private sentinel so the pure-logic tests still
  run without `firebase_admin`)
- the `if value is DELETE_FIELD: existing.pop(key, None)` branch in
  `_FakeDocRef.update`
- the `sys.path.insert(0, ...)` project-root line

**Do this first.** `migrate_notification_preference` writes
`firestore.DELETE_FIELD` (live `users.py:238`), and without this its tests pass
against code that never deletes anything.

**Keep local:** the `is_contract_iso` helper and the `'2026-01-01T00:00:00.000'`
fixture dates. Do **not** take the live `'SERVER_TIMESTAMP'` fixture strings —
they describe the behaviour the R1 fix removed.

### 2 · `modules/users.py` — `migrate_notification_preference`

Port in, all live-only:

- `CANONICAL_PREFERENCE_KEY = "receiveEmailNotifications"`
- `LEGACY_PREFERENCE_KEY = "reciveEmailNotifications"` (the misspelling is the point)
- `_MIGRATION_BATCH_SIZE = 400`
- `def migrate_notification_preference(data, decoded_token, db)` — 128 lines

**Re-add `firestore` to the import line.** The R1 fix removed it because it
became unused once `SERVER_TIMESTAMP` was gone; this function needs it back for
`DELETE_FIELD`. That is a genuine re-add, not a merge conflict:

```python
from firebase_admin import auth, firestore
```

**Keep local:** `now_iso()` at the two former `SERVER_TIMESTAMP` sites (lines 40
and 86). Do **not** take the live `firestore.SERVER_TIMESTAMP` lines back —
they are the 78-value regression this whole step exists to stop.

### 3 · `main.py` — route the action

- widen the `modules.users` import to include `migrate_notification_preference`
- add the dispatch branch:

```python
elif action == "migrateNotificationPreference":
    return migrate_notification_preference(data, decoded_token, db)
```

Its comment references `contracts/cloud-function-actions.md`, which exists in
neither the checkout nor the deployed zip. Either write it or drop the
reference — a pointer to a missing document is worse than none.

### 4 · `modules/notifications.py` — token pruning

Port in, all live-only:

- `_is_permanently_invalid_token(exception)` — 19 lines. Distinguishes a
  permanent rejection (`UnregisteredError`, `SenderIdMismatchError`) from a
  transient send failure, guarded because a stubbed `messaging` module hands
  back non-classes and `isinstance` against one raises.
- `_prune_token(db, user_id, token)` — 20 lines
- the enlarged `handle_send_notification` (95 live vs 60 local): adds
  `targetUserId` addressing, server-side token resolution, the
  `no_valid_registration` 200 response, and the `token_pruned` path
- the enlarged `handle_send_notification_to_all` (137 live vs 96 local)

The `targetUserId` path is load-bearing: the field app has no read access to
another user's document, so requiring an `fcmToken` up front meant it could
only ever address itself and fell back to broadcasting.

### 5 · Four test files — copy verbatim

| File | Tests |
|---|---|
| `tests/test_notification_audience.py` | 15 |
| `tests/test_notification_single_target.py` | 13 |
| `tests/test_notification_token_pruning.py` | 4 |
| `tests/test_preference_migration.py` | 20 |

52 tests covering exactly the code in steps 2 and 4. The current suite is 79;
expect ~131 after.

---

## What NOT to touch

**`modules/tasks.py` has nothing to port in.** Its 67 live-only lines are the
old `regenerate_plan_tasks` body, which the working tree replaces with the
diff-based rewrite (188 live → 206 local). Production is behind here, not
ahead. No function is live-only in this module.

**Do not revert these local changes.** They are newer than production:

- `modules/dates.py` and the ten `now_iso()` call sites — contract rule R1
- `tests/test_dates_contract.py` — 15 tests
- the `regenerate_plan_tasks` rework in `modules/tasks.py` and
  `tests/test_regenerate_plan_tasks.py` — uncommitted, and not mine
- `tests/test_reconcile_delete.py` / `test_reconcile_priority.py` — the two
  assertions that pinned the `SERVER_TIMESTAMP` sentinel now assert the
  contract ISO shape
- `tests/test_get_tasks_paginated.py` — untracked, currently passing

---

## Gate before deploying

- [ ] Steps 1–5 done, suite green (~131 tests)
- [ ] `grep -rn SERVER_TIMESTAMP modules/` returns nothing outside `dates.py`'s
      docstring — the port-in re-adds `firestore` to `users.py` and it must not
      bring the sentinel back with it
- [ ] Diff the reconciled checkout against the deployed zip again; the only
      differences should be the intended new work
- [ ] Commit. **The reason this list exists is that the last deploy was not
      committed** — a deploy from an uncommitted tree makes the next person do
      this again.
- [ ] Only then deploy, and only then re-run
      `tools/audit_date_types.js` (in the dashboard repo) to confirm the `tasks`
      and `users` Timestamp counts stop growing.

The date backfill stays blocked until the deploy lands. Converting the 132
stored Timestamps while production still writes new ones converts everything
and watches 78 come back.
