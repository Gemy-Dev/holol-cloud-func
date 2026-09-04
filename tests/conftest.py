"""Shared test fixtures and helpers for reconcile_client_tasks tests."""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, patch
import pytest
from flask import Flask
import re

# Contract rule R1 (docs/firestore-contract.md in either Flutter repo): every
# stored date is a zone-less ISO-8601 string with millisecond precision, the
# exact shape Dart's DateTime.toIso8601String() produces for a local value. An
# offset suffix would parse fine on both clients but sort differently, and
# lexicographic order is the point of the rule.
_CONTRACT_ISO = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}$')


def is_contract_iso(value):
    """Whether a written date matches what the Flutter clients write."""
    return isinstance(value, str) and bool(_CONTRACT_ISO.match(value))

# Make the project root importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# In-memory Firestore helpers
# ---------------------------------------------------------------------------

class _FakeDocRef:
    """Minimal stand-in for a Firestore document reference."""

    def __init__(self, store: dict, collection: str, doc_id: str):
        self._store = store
        self._collection = collection
        self._doc_id = doc_id

    @property
    def id(self):
        return self._doc_id

    def set(self, data: dict):
        self._store.setdefault(self._collection, {})[self._doc_id] = dict(data)

    def update(self, data: dict):
        existing = self._store.setdefault(self._collection, {}).get(self._doc_id, {})
        existing.update(data)
        self._store[self._collection][self._doc_id] = existing

    def get(self):
        doc = self._store.get(self._collection, {}).get(self._doc_id)
        return _FakeDocSnapshot(self._doc_id, doc)

    def delete(self):
        self._store.setdefault(self._collection, {}).pop(self._doc_id, None)


class _FakeDocSnapshot:
    def __init__(self, doc_id: str, data: Optional[dict]):
        self._doc_id = doc_id
        self._data = data

    @property
    def id(self):
        return self._doc_id

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data else {}


class _FakeQueryResult:
    """Iterable of _FakeDocSnapshot returned by stream()."""

    def __init__(self, docs: list):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)

    def stream(self):
        return iter(self._docs)


class _FakeQuery:
    """Chainable fake query that filters an in-memory list."""

    def __init__(self, docs: list):
        self._docs = list(docs)

    def where(self, field: str, op: str, value) -> '_FakeQuery':
        """Apply a simple equality / inequality filter."""
        filtered = []
        for doc in self._docs:
            data = doc.to_dict()
            doc_val = data.get(field)
            if op == '==':
                if doc_val == value:
                    filtered.append(doc)
            elif op == '!=':
                if doc_val != value:
                    filtered.append(doc)
            elif op == 'array_contains':
                if isinstance(doc_val, list) and value in doc_val:
                    filtered.append(doc)
            elif op == 'in':
                if doc_val in value:
                    filtered.append(doc)
        return _FakeQuery(filtered)

    def limit(self, n: int) -> '_FakeQuery':
        return _FakeQuery(self._docs[:n])

    def select(self, fields: list) -> '_FakeQuery':
        return self

    def stream(self):
        return iter(self._docs)

    def __iter__(self):
        return iter(self._docs)


class _FakeCollection:
    def __init__(self, store: dict, name: str):
        self._store = store
        self._name = name

    def document(self, doc_id: str | None = None) -> _FakeDocRef:
        if doc_id is None:
            import uuid
            doc_id = str(uuid.uuid4())
        return _FakeDocRef(self._store, self._name, doc_id)

    def _all_docs(self) -> list:
        return [
            _FakeDocSnapshot(did, dict(data))
            for did, data in self._store.get(self._name, {}).items()
        ]

    def where(self, field: str, op: str, value) -> _FakeQuery:
        return _FakeQuery(self._all_docs()).where(field, op, value)

    def stream(self):
        return iter(self._all_docs())

    def limit(self, n: int) -> _FakeQuery:
        return _FakeQuery(self._all_docs()).limit(n)

    def select(self, fields: list) -> _FakeQuery:
        return _FakeQuery(self._all_docs()).select(fields)


class _FakeBatch:
    """Records all updates/sets for inspection."""

    def __init__(self, store: dict):
        self._store = store
        self.operations = []  # list of ('update'|'set'|'delete', ref, data)

    def update(self, ref: _FakeDocRef, data: dict):
        self.operations.append(('update', ref, data))

    def set(self, ref: _FakeDocRef, data: dict):
        self.operations.append(('set', ref, data))

    def delete(self, ref: _FakeDocRef):
        self.operations.append(('delete', ref, None))

    def commit(self):
        for op, ref, data in self.operations:
            if op == 'update':
                ref.update(data)
            elif op == 'set':
                ref.set(data)
            elif op == 'delete':
                ref.delete()


class FakeFirestore:
    """Minimal in-memory Firestore stand-in."""

    SERVER_TIMESTAMP = 'SERVER_TIMESTAMP'

    def __init__(self):
        self._store: dict[str, dict[str, dict]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._store, name)

    def batch(self) -> _FakeBatch:
        return _FakeBatch(self._store)

    # Convenience helpers for seeds
    def _put(self, collection: str, doc_id: str, data: dict):
        self._store.setdefault(collection, {})[doc_id] = dict(data)

    def _get_all(self, collection: str) -> dict:
        return dict(self._store.get(collection, {}))


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def make_influencer_doctor(name: str, priority: str = 'C') -> dict:
    return {
        'name': name,
        'phone': '050',
        'email': f'{name.lower().replace(" ", "")}@test.com',
        'isInfluencer': True,
        'priority': priority,
    }


def make_client(
    client_id: str = 'client1',
    city: str = 'الرياض',
    department: str = 'dept1',
    doctors: list | None = None,
) -> dict:
    return {
        'id': client_id,
        'name': 'عيادة الاختبار',
        'city': city,
        'department': department,
        'additionalInfo': {
            'doctors': doctors or [],
        },
    }


def make_task(
    task_id: str,
    client_id: str = 'client1',
    doctor_name: str = 'Dr. X',
    priority: str = 'C',
    status: str = 'pending',
    review_state: str = 'approved',
    plan_id: str = 'plan1',
    product_id: str = 'prod1',
    marketing_task: str = 'visit',
) -> dict:
    return {
        'id': task_id,
        'clientId': client_id,
        'doctorName': doctor_name,
        'priority': priority,
        'status': status,
        'reviewState': review_state,
        'planId': plan_id,
        'productId': product_id,
        'marketingTask': marketing_task,
        'taskType': 'planned',
        'createdAt': '2026-01-01T00:00:00.000',
        'updatedAt': '2026-01-01T00:00:00.000',
    }


def make_plan(
    plan_id: str = 'plan1',
    cities: list | None = None,
    departments: list | None = None,
    product_sales: list | None = None,
    clients_ids: list | None = None,
) -> dict:
    return {
        'id': plan_id,
        'title': 'خطة الاختبار',
        'cities': cities or ['الرياض'],
        'departmentsIds': departments or ['dept1'],
        'targetProductSales': product_sales or [{'productId': 'prod1', 'targetSales': 100}],
        'clientsIds': clients_ids or [],
        'endDate': datetime(2030, 1, 1, tzinfo=timezone.utc),
    }


def make_product(
    product_id: str = 'prod1',
    departments: list | None = None,
    marketing_tasks: list | None = None,
) -> dict:
    return {
        'id': product_id,
        'name': 'منتج الاختبار',
        'departmentsIds': departments or ['dept1'],
        'marketingTasks': marketing_tasks or ['visit'],
    }


def seed_db(db: FakeFirestore, *, clients=None, tasks=None, plans=None, products=None):
    """Seed the fake Firestore with test data."""
    for c in (clients or []):
        db._put('clients', c['id'], c)
    for t in (tasks or []):
        db._put('tasks', t['id'], t)
    for p in (plans or []):
        db._put('plans', p['id'], p)
    for pr in (products or []):
        db._put('products', pr['id'], pr)


# ---------------------------------------------------------------------------
# Pytest fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def app_context():
    """Push a Flask application context so jsonify() works in tests."""
    _app = Flask(__name__)
    with _app.app_context():
        yield


@pytest.fixture
def db():
    return FakeFirestore()
