"""Offline interface tests. Financial rows use the existing explicitly fictional C1 fixture."""
import copy
import hashlib
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts.b3_c1_pipeline import build_bundle
from scripts.offline_pipeline import BASE, PublicExportBlocked, canonical_json
from scripts.normalize_sec import parse_raw
from tests.test_normalize_sec import ROW, fixture as sec_fixture
from tests.test_offline_pipeline import FIXTURE, receipt as b3_receipt, record as b3_record


CLOCK = datetime(2026, 11, 3, tzinfo=timezone.utc)


def identity_record(*, adr=False, ratio_date='2026-01-01T00:00:00Z', ratio='2'):
    row = copy.deepcopy(FIXTURE)
    row['issuer']['cik'] = '0000000001'
    row['source']['issuer_id'] = row['issuer']['id']
    row['source']['xbrl']['taxonomy'] = 'us-gaap'
    if adr:
        row = b3_receipt()
        row['issuer']['cik'] = '0000000001'
        row['source']['issuer_id'] = row['issuer']['id']
        row['instrument']['ratio'] = {
            'decimal': ratio,
            'identity': {'ref': 'fictional-ratio-proof', 'checked_at': ratio_date,
                         'result': 'VERIFIED'},
        }
        row['instrument']['rights'] = {'ref': 'fictional-rights-proof',
                                       'checked_at': '2026-01-01T00:00:00Z',
                                       'result': 'VERIFIED'}
    return row


def snapshot(*, units=None, rows=None):
    raw = sec_fixture()
    raw['companies'][0]['facts']['us-gaap']['Revenues']['units'] = {
        unit: copy.deepcopy(rows if rows is not None else [ROW]) for unit in (units or ['USD'])
    }
    return json.dumps(raw, separators=(',', ':'))


class B3C1PipelineTests(unittest.TestCase):
    def run_bundle(self, identities, raw=None):
        payload = json.dumps(identities, separators=(',', ':'))
        raw = raw if raw is not None else snapshot()
        with patch('urllib.request.urlopen', side_effect=AssertionError('Network request')):
            result = build_bundle(payload, raw, as_of=CLOCK, source_locator='fictional-cache.json')
        self.assertEqual(result['visibility'], 'PRIVATE')
        self.assertFalse(result['valuation_usable'])
        return result, payload, raw

    def test_eligible_b3_intent_joins_c1_with_immutable_provenance(self):
        result, _, raw = self.run_bundle([identity_record()])
        issuer = result['issuers'][0]
        self.assertEqual(issuer['issuer_collection_eligible'], True)
        self.assertEqual(issuer['collection_intent']['state'], 'SIMULATED')
        self.assertEqual(issuer['normalization_status'], 'C1_NORMALIZED')
        self.assertEqual(result['normalization']['raw_document'], raw)
        self.assertEqual(issuer['source_sha256'], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(issuer['observations'][0]['structure'], 'CANDIDATE')
        self.assertEqual(issuer['observations'][0]['raw_pointer'],
                         '/companies/0/facts/us-gaap/Revenues/units/USD/0')
        self.assertEqual(issuer['observations'][0]['source_sha256'], issuer['source_sha256'])
        self.assertEqual(parse_raw(issuer and result['normalization']['raw_document'])['companies'][0]['cik'],
                         issuer['cik'])
        instrument = issuer['instruments'][0]
        self.assertTrue(instrument['instrument_valuation_eligible'])
        self.assertFalse(instrument['valuation_usable'])
        self.assertEqual(issuer['accounting_validation'], 'UNCHECKED')
        self.assertIn('ACCOUNTING_VALIDATION_UNCHECKED', instrument['valuation_blockers'])

    def test_compact_invalid_adr_proof_keeps_issuer_intent_but_closes_receipt_gate(self):
        record = identity_record(adr=True, ratio_date='20261103T000000Z')
        result, _, _ = self.run_bundle([record])
        issuer = result['issuers'][0]
        decision = issuer['instruments'][0]
        self.assertTrue(issuer['issuer_collection_eligible'])
        self.assertEqual(result['b3_plan']['collection_intents'][0]['state'], 'SIMULATED')
        self.assertEqual(decision['quality']['structure'], 'INVALID')
        self.assertFalse(decision['instrument_valuation_eligible'])
        self.assertFalse(decision['valuation_usable'])
        self.assertIn('B3_STRUCTURE_INVALID', decision['valuation_blockers'])
        self.assertEqual(issuer['observations'][0]['structure'], 'CANDIDATE')

    def test_valid_adr_keeps_separate_instrument_and_accounting_gates(self):
        result, _, _ = self.run_bundle([identity_record(adr=True)])
        issuer = result['issuers'][0]
        decision = issuer['instruments'][0]
        self.assertTrue(issuer['issuer_collection_eligible'])
        self.assertTrue(decision['instrument_valuation_eligible'])
        self.assertFalse(decision['valuation_usable'])
        self.assertEqual(issuer['accounting_validation'], 'UNCHECKED')
        self.assertIn('ACCOUNTING_VALIDATION_UNCHECKED', decision['valuation_blockers'])

    def test_currency_and_share_units_remain_metric_specific(self):
        for unit, expected in [('XYZ', 'OUT_OF_SCOPE'), ('USD', 'CANDIDATE'),
                               ('CAD', 'CANDIDATE')]:
            with self.subTest(unit=unit):
                result, _, raw = self.run_bundle([identity_record()], snapshot(units=[unit]))
                observation = result['issuers'][0]['observations'][0]
                self.assertEqual(observation['structure'], expected)
                self.assertEqual(result['normalization']['raw_document'], raw)
                self.assertEqual(observation['raw_pointer'].split('/units/')[1], unit + '/0')
                self.assertEqual(observation['accounting_validation'], 'UNCHECKED')
                self.assertFalse(observation['valuation_usable'])

        raw_obj = sec_fixture()
        raw_obj['companies'][0]['facts']['us-gaap'] = {
            'WeightedAverageNumberOfDilutedSharesOutstanding': {
                'units': {'shares': [copy.deepcopy(ROW)]}}
        }
        result, _, _ = self.run_bundle([identity_record()], json.dumps(raw_obj))
        item = result['issuers'][0]['observations'][0]
        self.assertEqual(item['structure'], 'CANDIDATE')
        self.assertEqual(item['normalized']['unit'], 'shares')
        self.assertIsNone(item['normalized']['currency'])

    def test_unarbitrated_conflicts_block_valuation_and_preserve_both_rows(self):
        rows = [copy.deepcopy(ROW), copy.deepcopy(ROW)]
        rows[1].update(val=99, form='10-K/A', accn='0000000001-26-000002')
        result, _, _ = self.run_bundle([identity_record()], snapshot(rows=rows))
        issuer = result['issuers'][0]
        self.assertEqual(issuer['groups'][0]['status'], 'CONFLICT')
        self.assertIsNone(issuer['groups'][0]['selected_observation_id'])
        self.assertEqual(len(issuer['observations']), 2)
        self.assertIn('CONFLICT_UNARBITRATED', issuer['valuation_blockers'])
        self.assertTrue(all(not item['valuation_usable'] for item in issuer['instruments']))

    def test_duplicate_instruments_share_one_issuer_source_intent(self):
        ordinary = identity_record()
        adr = identity_record(adr=True)
        # Distinct security/listing identities for the same verified issuer.
        adr['instrument']['isin'] = 'ZZ0000000002'
        adr['listing']['ticker'] = 'FICTADR'
        adr['listing']['mic'] = 'YYYY'
        result, _, _ = self.run_bundle([ordinary, adr])
        self.assertEqual(len(result['b3_plan']['collection_intents']), 1)
        self.assertEqual(len(result['issuers']), 1)
        self.assertEqual(len(result['issuers'][0]['instruments']), 2)

    def test_public_export_is_blocked_and_source_marker_is_only_private(self):
        marker = 'PRIVATE_FIXTURE_MARKER'
        record = identity_record()
        record['source']['reference'] = marker
        result, _, _ = self.run_bundle([record])
        private = canonical_json(result)
        self.assertIn(marker, private)
        with self.assertRaises(PublicExportBlocked) as exc:
            canonical_json(result, audience='public')
        self.assertNotIn(marker, str(exc.exception))

    def test_etf_is_excluded_before_c1_and_non_sec_intent_is_explicit(self):
        fund = identity_record()
        fund['instrument']['kind'] = 'ETF'
        result, _, _ = self.run_bundle([fund], 'malformed SEC snapshot must not be parsed')
        self.assertEqual(result['b3_plan']['decisions'][0]['status'], 'EXCLUDED')
        self.assertEqual(result['b3_plan']['collection_intents'], [])
        self.assertIsNone(result['normalization'])
        self.assertEqual(result['issuers'], [])

        non_sec = identity_record()
        non_sec['source']['adapter'] = 'ISSUER_REPORT'
        # B1 does not authorize an alternative route; no C1 normalization is run.
        result, _, _ = self.run_bundle([non_sec], 'malformed SEC snapshot must not be parsed')
        self.assertIsNone(result['normalization'])
        self.assertEqual(result['issuers'], [])
        self.assertEqual(result['unsupported_intents'][0]['status'], 'B3_GATE_UNVERIFIED')

        denied = identity_record()
        denied['source']['access']['authorization'] = 'DENIED'
        result, _, _ = self.run_bundle([denied], 'malformed SEC snapshot must not be parsed')
        self.assertEqual(result['b3_plan']['decisions'][0]['status'], 'BLOCKED')
        self.assertEqual(result['unsupported_intents'][0]['status'], 'B3_GATE_BLOCKED')
        self.assertIsNone(result['normalization'])


if __name__ == '__main__':
    unittest.main()
