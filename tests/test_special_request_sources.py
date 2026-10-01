"""Source readers and the request-record refresh table (data-model §3)."""
import pytest

from modules import special_requests as sr


def _report(**kw):
    base = {
        'type': 'مبيعات', 'clientId': 'c1', 'otherClientName': None, 'assignedToId': 'rep',
        'userName': 'المندوب', 'createdAt': '2026-10-01T09:00:00.000',
        'hasSpecialRequests': True, 'clientOrders': '10 علب',
    }
    base.update(kw)
    return base


def _seed_report(db, rid='r1', **kw):
    db._put('reports', rid, _report(**kw))
    db._put('clients', 'c1', {'name': 'عيادة النور'})


def _seed_visit(db, sid='s1', vid='v1', visit=None, parent=None):
    entry = {'id': vid, 'technicianId': 'tech', 'technicianName': 'الفني',
             'visitDate': '2026-10-01T08:00:00.000', 'createdAt': '2026-10-01T08:00:00.000',
             'hasSpecialRequests': True, 'clientOrders': 'طلب'}
    entry.update(visit or {})
    record = {'clientId': 'c1', 'clientName': 'عيادة النور', 'visitHistory': [entry]}
    record.update(parent or {})
    db._put('technical_support', sid, record)


def _refresh(db, source, intent, caller='rep'):
    tx = db.transaction()
    result = sr.refresh_request(tx, db, source, intent, caller)
    tx.commit()
    return result


def _doc(db, rid):
    return db._get_all('special_requests').get(rid)


# --- readers -----------------------------------------------------------------

def test_request_ids():
    assert sr.request_id_for_report('r1') == 'report_r1'
    assert sr.request_id_for_visit('s1', 'v1') == 'visit_s1_v1'


def test_report_source_fields_and_client_name(db):
    _seed_report(db)
    src = sr.read_report_source(db, 'r1')
    assert (src.request_id, src.family, src.rep_id, src.client_name) == ('report_r1', 'sales', 'rep', 'عيادة النور')
    assert src.flag is True and src.text == '10 علب' and not src.deleted and not src.is_report_copy


def test_other_client_name_wins_over_client_lookup(db):
    _seed_report(db, otherClientName='عميل آخر')
    assert sr.read_report_source(db, 'r1').client_name == 'عميل آخر'


@pytest.mark.parametrize('type_value', ['technical_support', 'دعم فني'])
def test_technical_type_report_is_technical_family_and_labelled(db, type_value):
    _seed_report(db, type=type_value)
    src = sr.read_report_source(db, 'r1')
    assert src.family == 'technical'
    assert sr.type_label(src) == 'تقرير دعم فني'


def test_sales_report_label_and_missing_report(db):
    _seed_report(db)
    assert sr.type_label(sr.read_report_source(db, 'r1')) == 'تقرير مبيعات'
    assert sr.read_report_source(db, 'nope') is None


def test_family_is_recomputed_when_the_type_is_corrected(db):
    _seed_report(db)
    assert sr.family_of(sr.read_report_source(db, 'r1').raw) == 'sales'
    db._put('reports', 'r1', _report(type='دعم فني'))
    assert sr.read_report_source(db, 'r1').family == 'technical'


def test_visit_source_reads_visit_history_not_the_subcollection(db):
    _seed_visit(db)
    src = sr.read_visit_source(db, 's1', 'v1')
    assert (src.request_id, src.family, src.rep_id, src.client_name) == ('visit_s1_v1', 'technical', 'tech', 'عيادة النور')
    assert src.text == 'طلب' and sr.type_label(src) == 'زيارة دعم فني'


def test_visit_missing_returns_none(db):
    _seed_visit(db)
    assert sr.read_visit_source(db, 's1', 'nope') is None
    assert sr.read_visit_source(db, 'nope', 'v1') is None


def test_visit_of_a_deleted_support_record_is_deleted(db):
    _seed_visit(db, parent={'reviewState': 'deleted'})
    assert sr.read_visit_source(db, 's1', 'v1').deleted is True


def test_visit_with_a_report_id_is_a_report_copy(db):
    _seed_visit(db, vid='r1')
    _seed_report(db, 'r1')
    assert sr.read_visit_source(db, 's1', 'r1').is_report_copy is True


def test_deleted_report_is_deleted(db):
    _seed_report(db, reviewState='deleted')
    assert sr.read_report_source(db, 'r1').deleted is True


def test_absent_key_is_distinguished_and_expected_values_can_be_substituted(db):
    _seed_visit(db, visit={'hasSpecialRequests': None})
    entry = db._get_all('technical_support')['s1']['visitHistory'][0]
    entry.pop('hasSpecialRequests'); entry.pop('clientOrders')
    src = sr.read_visit_source(db, 's1', 'v1')
    assert src.key_present is False and src.flag is False
    resolved = src.with_expected(True, 'نص المؤلف')
    assert resolved.flag is True and resolved.text == 'نص المؤلف' and resolved.key_present is True


def test_flag_on_with_empty_text_is_not_a_flag(db):
    _seed_report(db, clientOrders='   ')
    assert sr.read_report_source(db, 'r1').flag is False


def test_caller_may_review_checks_family_grant_and_active_account():
    active = {'isActive': True, 'permissions': ['reviewDailyReport']}
    assert sr.caller_may_review(active, 'sales')
    assert not sr.caller_may_review(active, 'technical')
    assert sr.caller_may_review({'isActive': True, 'permissions': ['reviewTechnicalReport']}, 'technical')
    assert sr.caller_may_review({'isActive': True, 'permissions': ['مراجعة تقرير فني']}, 'technical')
    assert not sr.caller_may_review({'isActive': False, 'permissions': ['reviewDailyReport']}, 'sales')
    assert not sr.caller_may_review({'permissions': ['reviewDailyReport']}, 'sales')


# --- refresh table -------------------------------------------------------------

def test_set_creates_an_awaiting_record_with_denormalised_fields(db):
    _seed_report(db)
    _refresh(db, sr.read_report_source(db, 'r1'), 'set')
    doc = _doc(db, 'report_r1')
    assert doc['state'] == 'awaiting' and doc['requestText'] == '10 علب'
    assert (doc['sourceType'], doc['reportId'], doc['family'], doc['clientName'],
            doc['representativeId'], doc['representativeName']) == (
        'report', 'r1', 'sales', 'عيادة النور', 'rep', 'المندوب')
    assert doc['sourceCreatedAt'] == '2026-10-01T09:00:00.000'


def test_set_on_a_visit_creates_a_visit_record(db):
    _seed_visit(db)
    _refresh(db, sr.read_visit_source(db, 's1', 'v1'), 'set', caller='tech')
    doc = _doc(db, 'visit_s1_v1')
    assert (doc['sourceType'], doc['supportRecordId'], doc['visitId'], doc['family']) == ('visit', 's1', 'v1', 'technical')


def test_set_updates_the_text_of_an_awaiting_record(db):
    _seed_report(db)
    _refresh(db, sr.read_report_source(db, 'r1'), 'set')
    db._put('reports', 'r1', _report(clientOrders='20 علبة'))
    _refresh(db, sr.read_report_source(db, 'r1'), 'set')
    assert _doc(db, 'report_r1')['requestText'] == '20 علبة'


def test_set_restores_a_withdrawn_record(db):
    _seed_report(db)
    _refresh(db, sr.read_report_source(db, 'r1'), 'set')
    db._put('reports', 'r1', _report(hasSpecialRequests=False))
    _refresh(db, sr.read_report_source(db, 'r1'), 'withdraw')
    assert _doc(db, 'report_r1')['state'] == 'withdrawn'
    db._put('reports', 'r1', _report(clientOrders='نص جديد'))
    _refresh(db, sr.read_report_source(db, 'r1'), 'set')
    assert (_doc(db, 'report_r1')['state'], _doc(db, 'report_r1')['requestText']) == ('awaiting', 'نص جديد')


def test_set_without_a_flag_is_an_invalid_intent_and_writes_nothing(db):
    _seed_report(db, hasSpecialRequests=False)
    with pytest.raises(sr.SpecialRequestError) as exc:
        _refresh(db, sr.read_report_source(db, 'r1'), 'set')
    assert (exc.value.code, exc.value.status) == ('invalid_intent', 400)
    assert _doc(db, 'report_r1') is None


def test_withdraw_needs_an_explicit_false_flag(db):
    _seed_report(db)
    _refresh(db, sr.read_report_source(db, 'r1'), 'set')
    with pytest.raises(sr.SpecialRequestError) as exc:  # flag still on
        _refresh(db, sr.read_report_source(db, 'r1'), 'withdraw')
    assert exc.value.code == 'invalid_intent'
    assert _doc(db, 'report_r1')['state'] == 'awaiting'


def test_withdraw_by_a_non_author_is_not_author(db):
    _seed_report(db)
    _refresh(db, sr.read_report_source(db, 'r1'), 'set')
    db._put('reports', 'r1', _report(hasSpecialRequests=False))
    with pytest.raises(sr.SpecialRequestError) as exc:
        _refresh(db, sr.read_report_source(db, 'r1'), 'withdraw', caller='manager')
    assert (exc.value.code, exc.value.status) == ('not_author', 403)
    assert _doc(db, 'report_r1')['state'] == 'awaiting'


def test_withdraw_with_no_record_writes_nothing(db):
    _seed_report(db, hasSpecialRequests=False)
    _refresh(db, sr.read_report_source(db, 'r1'), 'withdraw')
    assert _doc(db, 'report_r1') is None


def test_none_on_a_stripped_source_leaves_an_awaiting_record_untouched(db):
    _seed_visit(db)
    _refresh(db, sr.read_visit_source(db, 's1', 'v1'), 'set', caller='tech')
    entry = db._get_all('technical_support')['s1']['visitHistory'][0]
    entry.pop('hasSpecialRequests'); entry.pop('clientOrders')  # old build rewrote the history
    _refresh(db, sr.read_visit_source(db, 's1', 'v1'), 'none', caller='tech')
    doc = _doc(db, 'visit_s1_v1')
    assert doc['state'] == 'awaiting' and doc['requestText'] == 'طلب'


def test_none_with_no_record_creates_nothing(db):
    _seed_report(db)
    _refresh(db, sr.read_report_source(db, 'r1'), 'none')
    assert _doc(db, 'report_r1') is None


@pytest.mark.parametrize('intent', ['set', 'withdraw', 'none'])
def test_a_decided_record_is_never_touched(db, intent):
    _seed_report(db)
    _refresh(db, sr.read_report_source(db, 'r1'), 'set')
    db._get_all('special_requests')['report_r1']['salesManagerDecision'] = {'decision': 'approved', 'deciderId': 'm'}
    before = dict(_doc(db, 'report_r1'))
    db._put('reports', 'r1', _report(clientOrders='تغيير متأخر', hasSpecialRequests=intent != 'withdraw'))
    _refresh(db, sr.read_report_source(db, 'r1'), intent)
    assert _doc(db, 'report_r1') == before


def test_a_type_correction_refreshes_the_family_of_an_undecided_record(db):
    _seed_report(db)
    _refresh(db, sr.read_report_source(db, 'r1'), 'set')
    db._put('reports', 'r1', _report(type='دعم فني'))
    _refresh(db, sr.read_report_source(db, 'r1'), 'none')
    assert _doc(db, 'report_r1')['family'] == 'technical'


def test_as_iso_normalises_strings_datetimes_and_nothing():
    from datetime import datetime, timezone
    from modules.dates import as_iso
    assert as_iso('2026-10-01T09:00:00.000') == '2026-10-01T09:00:00.000'
    assert as_iso(datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)) == '2026-10-01T09:00:00.000'
    assert as_iso(None) is None and as_iso('') is None


def test_a_visit_history_stored_as_a_map_is_read(db):
    from modules.special_requests import read_visit_source, visit_entries
    db._put('technical_support', 's1', {
        'clientName': 'عيادة', 'visitHistory': {
            'v1': {'technicianId': 't', 'technicianName': 'الفني',
                   'createdAt': '2026-10-01T09:00:00.000',
                   'hasSpecialRequests': True, 'clientOrders': 'طلب'}}})
    assert [v['id'] for v in visit_entries(db._get_all('technical_support')['s1']['visitHistory'])] == ['v1']
    source = read_visit_source(db, 's1', 'v1')
    assert source is not None and source.flag is True and source.text == 'طلب'
