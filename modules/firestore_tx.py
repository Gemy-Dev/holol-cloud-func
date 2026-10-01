"""Firestore transaction helper.

Kept in one place so tests can replace it: ``FakeFirestore`` has no
contention, so tests monkeypatch ``run_transaction`` to call ``fn`` with the
fake's own transaction.
"""
from firebase_admin import firestore


def run_transaction(db, fn):
    """Run ``fn(transaction)`` in a Firestore transaction and return its result.

    ``fn`` must read with ``ref.get(transaction=transaction)`` and write only
    through the transaction. It may be retried by Firestore on contention, so
    it must not have side effects beyond those writes.
    """
    @firestore.transactional
    def _run(transaction):
        return fn(transaction)

    return _run(db.transaction())
