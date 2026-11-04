"""Fictitious annual reports only; no collection, valuation or authenticated data."""
import base64
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from scripts.annual_history import (MAX_SOURCE_BYTES, import_report, issuer_views,
                                    new_history, serialize, validate_history)
from scripts.offline_pipeline import PublicExportBlocked
from tests.test_normalize_sec import ROW, fixture

CIK = dict(scheme='CIK', identifier='0000000001')
LEI = dict(scheme='LEI', identifier='FICTIONAL00000000001')


def binary(raw):
    return dict(raw_bytes_base64=base64.b64encode(raw).decode(),
                declared_sha256=hashlib.sha256(raw).hexdigest())


def report(year=2025, *, amount=100, received=None, filed=None, start=None,
           namespace='us-gaap', document_id=None, rows=None):
    end = f'{year}-06-30'
    start = start or f'{year-1}-07-01'
    filed = filed or f'{year}-08-01'
    row = dict(ROW, start=start, end=end, filed=filed, fy=year, val=amount,
               accn=f'0000000001-{str(year)[-2:]}-000001')
    doc = fixture(rows if rows is not None else [row])
    if namespace == 'ifrs-full':
        doc['companies'][0]['facts'] = {'ifrs-full': {'Revenue': {'units': {'CAD': [row]}}}}
    raw = json.dumps(doc).encode()
    return dict(schema_version='iic.annual-report.v1', visibility='PRIVATE', valuation_usable=False,
                issuer=copy.deepcopy(CIK),
                reporting_period=dict(start=start, end=end),
                received_at=received or f'{year}-08-02T10:00:00Z',
                revision_kind='INITIAL', revises=[],
                source=dict(format='SEC_SNAPSHOT', document_id=document_id or f'fictional-{year}',
                            filed_at=filed, locator='fictional:annual-report',
                            provenance=dict(method='SYNTHETIC_TEST', reference='fictional-fixture'),
                            **binary(raw)))


def esef_report():
    result = report()
    result['issuer'] = copy.deepcopy(LEI)
    result['reporting_period'] = dict(start='2025-01-01', end='2025-12-31')
    result['received_at'] = '2026-03-02T10:00:00Z'
    period = '2025-01-01T00:00:00/2026-01-01T00:00:00'
    entity = 'scheme:' + LEI['identifier']
    doc = dict(lei=LEI['identifier'], matching_facts=[dict(
        fact_id='fictional-esef-1', concept='ifrs-full:Revenue', value='100.000000000000000001',
        unit='iso4217:EUR', period=period, entity=entity,
        dimensions=dict(concept='ifrs-full:Revenue', unit='iso4217:EUR', period=period, entity=entity))])
    result['source'].update(format='ESEF_JSON', document_id='fictional-esef-2025',
                            filed_at='2026-03-01', **binary(json.dumps(doc).encode()))
    return result


def add(history, item):
    return import_report(history, item)


class AnnualHistoryTests(unittest.TestCase):
    def test_n_plus_one_preserves_all_versions_bytes_and_input_hashes(self):
        history = new_history()
        previous = []
        for year in (2023, 2024, 2025):
            original = copy.deepcopy(history)
            item = report(year)
            source = copy.deepcopy(item)
            updated = add(history, item)
            self.assertEqual(history, original)
            self.assertEqual(item, source)
            self.assertEqual(updated['reports'][:-1], previous)
            history = updated
            previous = copy.deepcopy(history['reports'])
        views = issuer_views(history, CIK)
        self.assertEqual([p['end'] for p in views['annual_periods']],
                         ['2023-06-30', '2024-06-30', '2025-06-30'])
        for record in history['reports']:
            raw = base64.b64decode(record['input']['source']['raw_bytes_base64'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), record['input']['source']['declared_sha256'])
            self.assertEqual(len(record['version_id']), 64)
        self.assertEqual(views['latest_available']['periods'][0]['end'], '2025-06-30')
        self.assertFalse(views['valuation_usable'])
        self.assertIsNone(views['latest_admissible']['period'])

    def test_reimport_is_idempotent_including_a_later_receipt(self):
        item = report()
        history = add(new_history(), item)
        self.assertEqual(add(history, item), history)
        later = dict(item, received_at='2026-01-01T00:00:00Z')
        self.assertEqual(add(history, later), history)
        self.assertEqual(history['reports'][0]['input']['received_at'], item['received_at'])

    def test_out_of_order_receipt_does_not_change_latest_year(self):
        history = add(new_history(), report(2025))
        old = report(2023, received='2026-01-01T00:00:00Z')
        history = add(history, old)
        views = issuer_views(history, CIK)
        self.assertEqual(views['latest_received']['version_ids'], [history['reports'][-1]['version_id']])
        self.assertEqual(views['latest_available']['periods'][0]['end'], '2025-06-30')
        self.assertIsNone(views['latest_admissible']['period'])

    def test_restated_old_period_keeps_both_versions_and_conflict_without_selection(self):
        history = add(new_history(), report(2024))
        first = history['reports'][0]['version_id']
        history = add(history, report(2025))
        restated = report(2024, amount=99, filed='2026-01-01',
                          received='2026-01-02T00:00:00Z', document_id='fictional-restatement')
        restated.update(revision_kind='RESTATEMENT', revises=[first])
        history = add(history, restated)
        self.assertEqual(len(history['reports']), 3)
        views = issuer_views(history, CIK)
        old_period = views['annual_periods'][0]
        self.assertEqual(old_period['status'], 'CONFLICT')
        self.assertEqual(len(old_period['version_ids']), 2)
        self.assertIsNone(old_period['selected_version_id'])
        self.assertEqual(len(old_period['conflicts'][0]['observation_refs']), 2)
        self.assertIsNone(old_period['conflicts'][0]['selected_observation'])
        self.assertEqual(history['reports'][-1]['input']['revises'], [first])
        self.assertEqual(views['latest_available']['periods'][0]['end'], '2025-06-30')

    def test_later_report_can_carry_restatement_of_comparative_period(self):
        old = report(2024)
        history = add(new_history(), old)
        rows = [dict(ROW, start='2024-07-01', end='2025-06-30', val=101, filed='2025-08-01', fy=2025),
                dict(ROW, start='2023-07-01', end='2024-06-30', val=99, filed='2025-08-01', fy=2025)]
        history = add(history, report(2025, rows=rows))
        views = issuer_views(history, CIK)
        self.assertEqual(views['annual_periods'][0]['status'], 'CONFLICT')
        self.assertEqual(len(views['annual_periods'][0]['version_ids']), 2)
        self.assertEqual(views['annual_periods'][1]['end'], '2025-06-30')

    def test_missing_new_report_does_not_discard_last_available_period(self):
        history = add(new_history(), report(2024))
        empty = report(2025, rows=[])
        history = add(history, empty)
        views = issuer_views(history, CIK)
        self.assertEqual(views['latest_received']['version_ids'], [history['reports'][-1]['version_id']])
        self.assertEqual(views['latest_available']['periods'][0]['end'], '2024-06-30')
        self.assertEqual(views['annual_periods'][-1]['status'], 'MISSING')

    def test_fiscal_calendar_is_not_import_or_calendar_year(self):
        item = report(2025, start='2024-06-30', received='2026-01-01T00:00:00Z')
        history = add(new_history(), item)
        period = issuer_views(history, CIK)['latest_available']['periods'][0]
        self.assertEqual((period['start'], period['end']), ('2024-06-30', '2025-06-30'))
        self.assertEqual(history['reports'][0]['input']['source']['filed_at'], '2025-08-01')

    def test_ifrs_and_us_gaap_and_esef_use_same_history_api(self):
        for item, identity in [(report(namespace='us-gaap'), CIK),
                               (report(namespace='ifrs-full'), CIK), (esef_report(), LEI)]:
            with self.subTest(format=item['source']['format'], concept=item['source']['document_id']):
                history = add(new_history(), item)
                self.assertEqual(len(history['reports'][0]['observations']), 1)
                views = issuer_views(history, identity)
                self.assertEqual(views['latest_available']['status'], 'AVAILABLE_UNAUTHENTICATED')
                self.assertIsNone(views['latest_admissible']['period'])
                self.assertIsNone(views['last_valid_output'])
        history = add(new_history(), esef_report())
        self.assertEqual(history['reports'][0]['observations'][0]['value'], '100.000000000000000001')
        period = issuer_views(history, LEI)['annual_periods'][0]
        self.assertEqual((period['start'], period['end']), ('2025-01-01', '2025-12-31'))

    def test_instruments_are_not_history_keys_and_foreign_company_is_configurable(self):
        config = dict(issuer=dict(scheme='CIK', identifier='0000000042'),
                      instruments=['FICTIONAL-ORDINARY', 'FICTIONAL-ADR'])
        item = report()
        item['issuer'] = config['issuer']
        doc = fixture([dict(ROW, start='2024-07-01', end='2025-06-30', filed='2025-08-01')])
        doc['companies'][0]['cik'] = '0000000042'
        item['source'].update(binary(json.dumps(doc).encode()))
        history = add(new_history(), item)
        for instrument in config['instruments']:
            self.assertEqual(issuer_views(history, config['issuer'])['latest_received']['version_ids'],
                             [history['reports'][0]['version_id']])
        self.assertEqual(len(history['reports']), 1)
        self.assertEqual(issuer_views(history, CIK)['latest_available']['status'], 'MISSING')
        self.assertNotIn('instruments', serialize(history))

    def test_retains_prior_output_artifact_without_claiming_current_validity(self):
        retained = dict(issuer=copy.deepcopy(CIK), generated_at='2024-08-03T00:00:00Z',
                        reference='fictional:old-output', input_version_ids=['a'*64],
                        **binary(b'fictional previous output; validity not established'))
        history = new_history(retained_output=retained)
        for year in (2024, 2025):
            history = add(history, report(year))
        self.assertEqual(history['retained_output'], retained)
        views = issuer_views(history, CIK)
        self.assertEqual(views['retained_output'], retained)
        self.assertEqual(views['retained_output_validation'], 'NOT_REVALIDATED')
        self.assertIsNone(views['last_valid_output'])
        self.assertIsNone(issuer_views(history, LEI)['retained_output'])

    def test_malformed_or_inconsistent_import_is_atomic_in_memory_and_retryable(self):
        history = add(new_history(), report(2024))
        before = copy.deepcopy(history)
        bad_reports = [None, {}, dict(report(), valuation_usable=True)]
        for field, value in [('raw_bytes_base64', '!!!!'), ('declared_sha256', '0'*64)]:
            bad = report()
            bad['source'][field] = value
            bad_reports.append(bad)
        bad = report();bad['reporting_period']['end']='2025-06-31';bad_reports.append(bad)
        bad = report();bad['source'].update(binary(b'{bad'));bad_reports.append(bad)
        bad = report();bad['issuer']['identifier']='0000000002';bad_reports.append(bad)
        for bad in bad_reports:
            with self.subTest(value=type(bad).__name__), self.assertRaises(ValueError):
                add(history, bad)
            self.assertEqual(history, before)
        resumed = add(history, report())
        self.assertEqual(len(resumed['reports']), 2)
        self.assertEqual(resumed['reports'][0], before['reports'][0])

    def test_caller_statuses_or_mutated_state_cannot_promote_or_erase_conflicts(self):
        history = add(new_history(), report())
        for field, value in [('controls', dict(source_verification='VERIFIED', accounting_validation='UNCHECKED', valuation_usable=True)),
                             ('observations', [])]:
            bad = copy.deepcopy(history)
            bad['reports'][0][field] = value
            with self.assertRaises(ValueError):
                validate_history(bad)
        item = dict(report(), verified=True)
        with self.assertRaises(ValueError):
            add(history, item)
        bad = copy.deepcopy(history)
        bad['reports'][0]['input']['reporting_period']['start']='2024-07-02'
        with self.assertRaisesRegex(ValueError, 'HISTORY_INTEGRITY_INVALID'):
            validate_history(bad)

    def test_metadata_collision_is_not_silently_ignored(self):
        item = report()
        history = add(new_history(), item)
        changed = copy.deepcopy(item)
        changed['source']['locator'] = 'fictional:other-declared-source'
        with self.assertRaisesRegex(ValueError, 'REPORT_METADATA_CONFLICT'):
            add(history, changed)

    def test_revision_references_cannot_link_missing_or_foreign_issuer(self):
        history = add(new_history(), report())
        other = report(2024, received='2026-01-01T00:00:00Z')
        other['revises'] = ['0'*64]
        with self.assertRaisesRegex(ValueError, 'REVISION_REFERENCE_INVALID'):
            add(history, other)
        esef = esef_report()
        esef['revises'] = [history['reports'][0]['version_id']]
        with self.assertRaisesRegex(ValueError, 'REVISION_REFERENCE_INVALID'):
            add(history, esef)

    def test_equal_receipt_dates_and_multiple_periods_do_not_select_one_version(self):
        history = add(new_history(), report(2024, received='2026-01-01T00:00:00Z'))
        history = add(history, report(2025, received='2026-01-01T01:00:00+01:00'))
        views = issuer_views(history, CIK)
        self.assertEqual(len(views['latest_received']['version_ids']), 2)
        self.assertTrue(all(p['selected_version_id'] is None for p in views['annual_periods']))

    def test_source_size_duplicate_json_keys_and_nonfinite_values_are_rejected(self):
        for raw in (b'x'*(MAX_SOURCE_BYTES+1), b'{"schema":1,"schema":1}', b'{"value":NaN}'):
            item = report();item['source'].update(binary(raw))
            with self.assertRaises(ValueError):
                add(new_history(), item)

    def test_malformed_nested_source_is_a_controlled_rejection(self):
        item = report(rows=[dict(ROW, form=[])])
        history = add(new_history(), report(2024))
        before = copy.deepcopy(history)
        with self.assertRaises(ValueError):
            add(history, item)
        self.assertEqual(history, before)

    def test_interrupted_pure_import_leaves_old_state_and_can_resume(self):
        history = add(new_history(), report(2024))
        before = copy.deepcopy(history)
        with patch('scripts.annual_history._extract', side_effect=RuntimeError('simulated interruption')):
            with self.assertRaises(RuntimeError):
                add(history, report(2025))
        self.assertEqual(history, before)
        self.assertEqual(len(add(history, report(2025))['reports']), 2)

    def test_duplicate_versions_and_invalid_retained_identity_are_rejected(self):
        history = add(new_history(), report())
        history['reports'].append(copy.deepcopy(history['reports'][0]))
        with self.assertRaises(ValueError):
            validate_history(history)
        with self.assertRaises(PublicExportBlocked):
            serialize({'private_marker': 'fictional-sensitive'}, audience='public')
        retained = dict(issuer=dict(scheme='LEI', identifier='invalid'),
                        generated_at='2025-01-01T00:00:00Z', reference='fictional',
                        input_version_ids=[], **binary(b'fictional output'))
        with self.assertRaises(ValueError):
            new_history(retained_output=retained)

    def test_instant_facts_and_ambiguous_annual_starts_are_not_silently_assigned(self):
        item = report()
        doc = fixture()
        doc['companies'][0]['facts']['us-gaap'] = {
            'Revenues': {'units': {'USD': [dict(ROW, start='2024-07-01', end='2025-06-30',
                                              filed='2025-08-01')]}},
            'Assets': {'units': {'USD': [dict(val=50, end='2025-06-30', filed='2025-08-01',
                                            accn='0000000001-25-000001', form='10-K', fy=2025, fp='FY')]}}}
        item['source'].update(binary(json.dumps(doc).encode()))
        history = add(new_history(), item)
        self.assertEqual(len(issuer_views(history, CIK)['annual_periods'][0]['observation_refs']), 2)
        ambiguous = copy.deepcopy(item)
        ambiguous['reporting_period']['start'] = '2024-06-30'
        history = add(new_history(), ambiguous)
        view = issuer_views(history, CIK)
        periods = view['annual_periods']
        self.assertEqual(view['unassigned_observation_refs'][0]['reason'], 'AMBIGUOUS_ANNUAL_PERIOD')
        self.assertEqual(sum(len(p['observation_refs']) for p in periods), 1)
        self.assertEqual(len(history['reports'][0]['observations']), 2)

    def test_esef_full_facts_preserve_exact_value_and_real_source_pointer(self):
        item = esef_report()
        document = json.loads(base64.b64decode(item['source']['raw_bytes_base64']))
        fact = document.pop('matching_facts')[0]
        document['facts'] = {'fact/with~escape': dict(value=fact['value'], dimensions=fact['dimensions'])}
        item['source'].update(binary(json.dumps(document).encode()))
        history = add(new_history(), item)
        observation = history['reports'][0]['observations'][0]
        self.assertEqual(observation['source_pointer'], '/facts/fact~1with~0escape')
        self.assertEqual(observation['source_fact_id'], 'fact/with~escape')
        self.assertEqual(observation['value'], '100.000000000000000001')
        self.assertEqual(issuer_views(history, LEI)['annual_periods'][0]['status'], 'UNCHECKED')

    def test_boolean_dimension_tampering_cannot_hide_behind_python_equality(self):
        item = report(rows=[dict(ROW, start='2024-07-01', end='2025-06-30', filed='2025-08-01',
                                 dimensions={'fictional_axis': True})])
        history = add(new_history(), item)
        history['reports'][0]['observations'][0]['dimensions']['fictional_axis'] = 1
        with self.assertRaisesRegex(ValueError, 'HISTORY_INTEGRITY_INVALID'):
            validate_history(history)

    def test_export_public_is_blocked_and_private_roundtrip_revalidates(self):
        history = add(new_history(), report())
        with self.assertRaises(PublicExportBlocked):
            serialize(history, audience='public')
        restored = json.loads(serialize(history))
        self.assertEqual(validate_history(restored), history)
        self.assertFalse(restored['valuation_usable'])
        self.assertEqual(restored['visibility'], 'PRIVATE')


if __name__ == '__main__':
    unittest.main()
