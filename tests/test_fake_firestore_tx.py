"""The FakeFirestore transaction and create() support that spec 2038 tests rely on."""
import pytest
from google.api_core.exceptions import AlreadyExists

from tests.conftest import FakeFirestore


def test_transaction_applies_buffered_writes_in_order_on_commit(db):
    tx = db.transaction()
    ref = db.collection('c').document('a')
    tx.set(ref, {'n': 1})
    tx.update(ref, {'n': 2})
    assert db._get_all('c') == {}  # nothing applied before commit
    tx.commit()
    assert db._get_all('c')['a'] == {'n': 2}


def test_transaction_reads_accept_the_transaction_keyword(db):
    db._put('c', 'a', {'n': 1})
    tx = db.transaction()
    assert db.collection('c').document('a').get(transaction=tx).to_dict() == {'n': 1}


def test_transaction_create_fails_when_the_document_exists(db):
    db._put('c', 'a', {'n': 1})
    tx = db.transaction()
    tx.create(db.collection('c').document('a'), {'n': 2})
    with pytest.raises(AlreadyExists):
        tx.commit()


def test_create_writes_once_then_raises_already_exists(db):
    ref = db.collection('c').document('a')
    ref.create({'n': 1})
    with pytest.raises(AlreadyExists):
        ref.create({'n': 2})
    assert db._get_all('c')['a'] == {'n': 1}


def test_set_merge_keeps_unrelated_fields(db):
    db._put('c', 'a', {'keep': 1, 'n': 1})
    db.collection('c').document('a').set({'n': 2}, merge=True)
    assert db._get_all('c')['a'] == {'keep': 1, 'n': 2}
