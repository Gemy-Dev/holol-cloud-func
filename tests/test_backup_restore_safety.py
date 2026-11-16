"""Phase 0: a restore saves the live data first and replaces only what it holds."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from modules import backups


def test_collections_in_backup_reads_kind_folders():
    names = [
        "firestore-backups/20261115_010203/20261115_010203.overall_export_metadata",
        "firestore-backups/20261115_010203/all_namespaces/kind_users/output-0",
        "firestore-backups/20261115_010203/all_namespaces/kind_users/all_namespaces_kind_users.export_metadata",
        "firestore-backups/20261115_010203/all_namespaces/kind_tasks/output-0",
        "firestore-backups/20261115_010203/all_namespaces/kind_not_ours/output-0",
    ]
    assert backups._collections_in_backup(names) == ["users", "tasks"]


def test_collections_in_backup_ignores_collections_it_lacks():
    names = ["x/all_namespaces/kind_users/output-0"]
    assert "material_transfers" not in backups._collections_in_backup(names)


def _service(operations):
    """A firestore_service whose operations().get() returns `operations` in turn."""
    service = MagicMock()
    service.projects().databases().operations().get().execute.side_effect = operations
    return service


def test_wait_for_operation_succeeds_when_done():
    service = _service([{"done": False}, {"done": True}])
    assert backups._wait_for_operation(service, "op", 60, sleep=lambda _s: None) is True


def test_wait_for_operation_fails_on_error():
    service = _service([{"done": True, "error": {"code": 7}}])
    assert backups._wait_for_operation(service, "op", 60, sleep=lambda _s: None) is False


def test_wait_for_operation_gives_up_at_the_deadline():
    ticks = iter([0, 10, 70])
    service = _service([{"done": False}, {"done": False}])
    assert backups._wait_for_operation(
        service, "op", 60, sleep=lambda _s: None, clock=lambda: next(ticks)) is False


def _bucket_with(names):
    bucket = MagicMock()
    bucket.list_blobs.return_value = [SimpleNamespace(name=n) for n in names]
    client = MagicMock()
    client.bucket.return_value = bucket
    return client


BACKUP = ["firestore-backups/T/all_namespaces/kind_users/output-0",
          "firestore-backups/T/all_namespaces/kind_clients/output-0"]


def test_restore_deletes_nothing_when_the_snapshot_fails():
    service = MagicMock()
    with patch.object(backups.storage, "Client", return_value=_bucket_with(BACKUP)), \
            patch.object(backups, "take_pre_restore_snapshot",
                         side_effect=RuntimeError("Pre-restore snapshot did not complete")), \
            patch.object(backups, "delete_all_collections_data") as delete:
        with pytest.raises(RuntimeError):
            backups.restore_firestore_backup_direct(service, "proj", "T")
    delete.assert_not_called()
    service.projects().databases().importDocuments.assert_not_called()


def test_restore_replaces_only_the_collections_in_the_backup():
    service = MagicMock()
    service.projects().databases().importDocuments().execute.return_value = {
        "name": "projects/proj/databases/(default)/operations/abc"}
    calls = []
    with patch.object(backups.storage, "Client", return_value=_bucket_with(BACKUP)), \
            patch.object(backups, "take_pre_restore_snapshot",
                         side_effect=lambda *_a, **_k: calls.append("snapshot") or {"timestamp": "S"}), \
            patch.object(backups, "delete_all_collections_data",
                         side_effect=lambda c: calls.append(("delete", tuple(c))) or {}):
        result = backups.restore_firestore_backup_direct(service, "proj", "T")

    assert calls == ["snapshot", ("delete", ("users", "clients"))]
    body = service.projects().databases().importDocuments.call_args.kwargs["body"]
    assert body["collectionIds"] == ["users", "clients"]
    assert result["pre_restore_backup"] == "S"
    assert result["collections"] == ["users", "clients"]


def test_restore_refuses_a_backup_with_no_known_collections():
    service = MagicMock()
    with patch.object(backups.storage, "Client",
                      return_value=_bucket_with(["firestore-backups/T/readme.txt"])), \
            patch.object(backups, "take_pre_restore_snapshot") as snapshot, \
            patch.object(backups, "delete_all_collections_data") as delete:
        with pytest.raises(ValueError):
            backups.restore_firestore_backup_direct(service, "proj", "T")
    snapshot.assert_not_called()
    delete.assert_not_called()


def test_restore_error_does_not_echo_the_request():
    response = backups.handle_restore_backup({"uid": "u"}, {"secret": "do-not-echo"})
    body, status = response
    assert status == 400
    assert "do-not-echo" not in str(body.get_json())
