"""Tests for regenerate_plan_tasks: diff-based sync of a plan's tasks after an edit."""
from unittest.mock import patch

from tests.conftest import (
    FakeFirestore,
    make_client,
    make_influencer_doctor,
    make_plan,
    make_product,
    make_task,
    seed_db,
)
from modules.tasks import regenerate_plan_tasks


def _call(db, plan_data):
    with patch("modules.tasks.firestore") as mock_fs:
        mock_fs.SERVER_TIMESTAMP = "SERVER_TIMESTAMP"
        mock_fs.ArrayUnion = lambda x: x
        response = regenerate_plan_tasks({"plan": plan_data}, db)
    if isinstance(response, tuple):
        resp_obj, status = response
    else:
        resp_obj, status = response, 200
    return resp_obj.get_json(), status


def _tasks(db):
    return db._get_all("tasks")


def _seed_single_client_plan(db, *, products=('prod1', 'prod2'), tasks=None):
    """One client in الرياض/dept1 with one influencer doctor, two products."""
    seed_db(
        db,
        clients=[
            make_client(
                'client1',
                city='الرياض',
                department='dept1',
                doctors=[make_influencer_doctor('Dr. X', 'A')],
            )
        ],
        plans=[make_plan('plan1')],
        products=[make_product(p) for p in products],
        tasks=tasks or [],
    )


def _plan_with_products(*product_ids, cities=None):
    return make_plan(
        'plan1',
        cities=cities or ['الرياض'],
        product_sales=[{'productId': p, 'targetSales': 100} for p in product_ids],
    )


class TestRemovedProduct:
    """A product dropped from the plan takes its pending tasks with it."""

    def test_pending_task_for_removed_product_is_deleted_and_not_recreated(self, db: FakeFirestore):
        _seed_single_client_plan(db, tasks=[
            make_task('t1', product_id='prod1'),
            make_task('t2', product_id='prod2'),
        ])

        body, status = _call(db, _plan_with_products('prod1'))

        assert status == 200
        assert body['success'] is True
        # Only the removed product's task is deleted; the still-wanted one is kept
        assert body['tasksDeleted'] == 1
        assert body['tasksCreated'] == 0
        assert body['tasksKept'] == 1
        assert body['tasksProtected'] == 0

        remaining = _tasks(db)
        # The surviving task is the original document, not a recreation
        assert list(remaining) == ['t1']
        assert remaining['t1']['productId'] == 'prod1'
        assert remaining['t1']['status'] == 'pending'

    def test_completed_task_for_removed_product_survives(self, db: FakeFirestore):
        _seed_single_client_plan(db, products=('prod1', 'prod2'), tasks=[
            make_task('t1', product_id='prod1'),
            make_task('t2', product_id='prod2', status='completed'),
            make_task('t3', product_id='prod2'),
        ])

        body, _ = _call(db, _plan_with_products('prod1'))

        assert body['tasksProtected'] == 1
        assert body['tasksKept'] == 1
        assert body['tasksDeleted'] == 1
        assert body['tasksCreated'] == 0

        remaining = _tasks(db)
        assert set(remaining) == {'t1', 't2'}
        assert remaining['t2']['status'] == 'completed'

    def test_canceled_task_for_removed_product_is_deleted(self, db: FakeFirestore):
        _seed_single_client_plan(db, tasks=[
            make_task('t1', product_id='prod2', status='canceled'),
        ])

        body, _ = _call(db, _plan_with_products('prod1'))

        assert body['tasksDeleted'] == 1
        assert 't1' not in _tasks(db)


class TestProtectedTasks:
    """Tasks carrying real fieldwork survive an edit."""

    def test_completed_task_survives_and_is_not_duplicated(self, db: FakeFirestore):
        _seed_single_client_plan(db, tasks=[
            make_task('t1', product_id='prod1', status='completed'),
        ])

        body, _ = _call(db, _plan_with_products('prod1'))

        assert body['tasksProtected'] == 1
        assert body['tasksDeleted'] == 0
        # The recreate pass must skip the combination the completed task covers
        assert body['tasksCreated'] == 0
        assert body['tasksSkipped'] == 1

        remaining = _tasks(db)
        assert list(remaining) == ['t1']
        assert remaining['t1']['status'] == 'completed'

    def test_arabic_completed_status_is_protected(self, db: FakeFirestore):
        _seed_single_client_plan(db, tasks=[
            make_task('t1', product_id='prod1', status='مكتمل'),
        ])

        body, _ = _call(db, _plan_with_products('prod1'))

        assert body['tasksProtected'] == 1
        assert body['tasksDeleted'] == 0
        assert list(_tasks(db)) == ['t1']

    def test_pending_task_with_visit_result_survives(self, db: FakeFirestore):
        task = make_task('t1', product_id='prod1', status='pending')
        task['visitResult'] = 'تمت الزيارة'
        _seed_single_client_plan(db, tasks=[task])

        body, _ = _call(db, _plan_with_products('prod1'))

        assert body['tasksProtected'] == 1
        assert body['tasksDeleted'] == 0
        assert body['tasksCreated'] == 0
        assert list(_tasks(db)) == ['t1']

    def test_canceled_task_for_still_wanted_combo_is_kept(self, db: FakeFirestore):
        _seed_single_client_plan(db, tasks=[
            make_task('t1', product_id='prod1', status='canceled'),
        ])

        body, _ = _call(db, _plan_with_products('prod1'))

        assert body['tasksProtected'] == 0
        assert body['tasksDeleted'] == 0
        assert body['tasksCreated'] == 0
        assert body['tasksKept'] == 1

        remaining = _tasks(db)
        assert list(remaining) == ['t1']
        assert remaining['t1']['status'] == 'canceled'

    def test_other_plans_tasks_are_untouched(self, db: FakeFirestore):
        _seed_single_client_plan(db, tasks=[
            make_task('t1', product_id='prod1'),
            make_task('other', product_id='prod1', plan_id='plan2'),
        ])

        body, _ = _call(db, _plan_with_products('prod1'))

        assert body['tasksDeleted'] == 0
        assert body['tasksKept'] == 1
        assert set(_tasks(db)) == {'t1', 'other'}


class TestAdditiveEdits:
    """Adding a product or a city deletes nothing — only the missing tasks are created."""

    def test_added_product_creates_only_its_own_tasks(self, db: FakeFirestore):
        task = make_task('t1', product_id='prod1')
        task['targetDate'] = '2026-09-01T10:00:00.000Z'  # scheduled by the planner
        _seed_single_client_plan(db, tasks=[task])

        body, _ = _call(db, _plan_with_products('prod1', 'prod2'))

        assert body['tasksDeleted'] == 0
        assert body['tasksKept'] == 1
        assert body['tasksCreated'] == 1

        remaining = _tasks(db)
        assert 't1' in remaining
        # The kept task is untouched, including its scheduler assignment
        assert remaining['t1']['targetDate'] == '2026-09-01T10:00:00.000Z'
        product_ids = {t['productId'] for t in remaining.values()}
        assert product_ids == {'prod1', 'prod2'}

    def test_added_client_via_city_keeps_existing_tasks(self, db: FakeFirestore):
        seed_db(
            db,
            clients=[
                make_client('client1', city='الرياض', department='dept1',
                            doctors=[make_influencer_doctor('Dr. X', 'A')]),
                make_client('client2', city='جدة', department='dept1',
                            doctors=[make_influencer_doctor('Dr. Y', 'B')]),
            ],
            plans=[make_plan('plan1')],
            products=[make_product('prod1')],
            tasks=[make_task('t1', client_id='client1', product_id='prod1')],
        )

        body, _ = _call(db, _plan_with_products('prod1', cities=['الرياض', 'جدة']))

        assert body['tasksDeleted'] == 0
        assert body['tasksKept'] == 1
        assert body['tasksCreated'] == 1

        tasks = _tasks(db)
        assert 't1' in tasks
        client_ids = {t['clientId'] for t in tasks.values()}
        assert client_ids == {'client1', 'client2'}
        assert set(body['clientsIds']) == {'client1', 'client2'}

    def test_duplicate_pending_task_is_removed(self, db: FakeFirestore):
        _seed_single_client_plan(db, tasks=[
            make_task('t1', product_id='prod1'),
            make_task('t2', product_id='prod1'),  # duplicate combination
        ])

        body, _ = _call(db, _plan_with_products('prod1'))

        assert body['tasksKept'] == 1
        assert body['tasksDeleted'] == 1
        assert body['tasksCreated'] == 0
        assert len(_tasks(db)) == 1


class TestPlanCounters:
    """The plan's denormalised counters are refreshed after the rebuild."""

    def test_tasks_count_and_clients_ids_include_protected_work(self, db: FakeFirestore):
        # client2 is in جدة, which the edited plan no longer targets, but it
        # still holds a completed task under this plan.
        seed_db(
            db,
            clients=[
                make_client('client1', city='الرياض', department='dept1',
                            doctors=[make_influencer_doctor('Dr. X', 'A')]),
                make_client('client2', city='جدة', department='dept1',
                            doctors=[make_influencer_doctor('Dr. Y', 'B')]),
            ],
            plans=[make_plan('plan1')],
            products=[make_product('prod1')],
            tasks=[
                make_task('t1', client_id='client2', doctor_name='Dr. Y',
                          product_id='prod1', status='completed'),
            ],
        )

        body, _ = _call(db, _plan_with_products('prod1', cities=['الرياض']))

        assert body['tasksProtected'] == 1
        assert body['tasksCreated'] == 1
        assert body['tasksCount'] == 2

        plan = db._get_all('plans')['plan1']
        assert plan['tasksCount'] == 2
        # client2 keeps its seat at the table because its completed task counts
        # towards the plan's KPI denominators.
        assert set(plan['clientsIds']) == {'client1', 'client2'}
        assert set(body['clientsIds']) == {'client1', 'client2'}


class TestValidation:
    """Invalid payloads are rejected before anything is deleted."""

    def test_missing_plan_is_rejected(self, db: FakeFirestore):
        with patch("modules.tasks.firestore") as mock_fs:
            mock_fs.SERVER_TIMESTAMP = "SERVER_TIMESTAMP"
            response, status = regenerate_plan_tasks({}, db)
        assert status == 400
        assert response.get_json()['success'] is False

    def test_plan_without_products_is_rejected_and_keeps_tasks(self, db: FakeFirestore):
        _seed_single_client_plan(db, tasks=[make_task('t1', product_id='prod1')])

        # make_plan() falls back to a default product, so clear it explicitly
        plan = _plan_with_products('prod1')
        plan['targetProductSales'] = []
        body, status = _call(db, plan)

        assert status == 400
        assert body['success'] is False
        # Nothing was deleted
        assert list(_tasks(db)) == ['t1']

    def test_no_matching_clients_leaves_existing_tasks_alone(self, db: FakeFirestore):
        _seed_single_client_plan(db, tasks=[make_task('t1', product_id='prod1')])

        body, status = _call(db, _plan_with_products('prod1', cities=['مدينة غير موجودة']))

        assert status == 400
        assert body['success'] is False
        assert list(_tasks(db)) == ['t1']
