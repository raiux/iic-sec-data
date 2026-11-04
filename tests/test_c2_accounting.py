"""Offline C2 tests. Fixture assertions never establish an official filing."""
import copy
import hashlib
import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from jsonschema import ValidationError

from scripts.b3_c1_pipeline import build_bundle
from scripts.normalize_sec import normalize_snapshot
from scripts.offline_pipeline import BASE, PublicExportBlocked, validator
from scripts.validate_sec_accounting import serialize, validate_bundle, validate_normalization
from tests.test_b3_c1_pipeline import identity_record, snapshot
from tests.test_normalize_sec import ROW, fixture as sec_fixture


CLOCK = datetime(2026, 11, 3, tzinfo=timezone.utc)
MSFT_CIK = '0000789019'
CAMECO_CIK = '0001009001'


def c1_bundle(*, rows=None, form='10-K', unit='USD', concept='Revenues', snapshot_cik='0000000001'):
    record = identity_record()
    raw_obj = sec_fixture()
    raw_obj['companies'][0]['cik'] = snapshot_cik
    raw_obj['companies'][0]['facts']['us-gaap'] = {
        concept: {'units': {unit: copy.deepcopy(rows if rows is not None else [dict(ROW, form=form)])}}
    }
    payload = json.dumps(raw_obj, separators=(',', ':'))
    with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
        return build_bundle(json.dumps([record]), payload, as_of=CLOCK,
                            source_locator='fixture-sec-cache.json')


def filing_evidence(*, url=None, accession='0000000001-26-000001', facts=None):
    return dict(
        issuer_cik='0000000001', accession=accession, form='10-K', filed='2026-08-01',
        taxonomy='us-gaap',
        document_url=(url or 'https://www.sec.gov/Archives/edgar/data/1/000000000126000001/filing.xml'),
        # This digest is only a fixture assertion; this test has no filing bytes.
        document_sha256=hashlib.sha256(b'fictional-unfetched-filing').hexdigest(),
        facts=copy.deepcopy(facts if facts is not None else [dict(
            namespace='us-gaap', concept='Revenues', unit='USD', value='100',
            start='2025-07-01', end='2026-06-30', dimensions={})]))


class C2AccountingTests(unittest.TestCase):
    def c2(self, bundle, evidence=()):
        return validate_bundle(bundle, evidence=evidence)

    def test_b3_c1_c2_keeps_separate_gates_and_raw_provenance(self):
        bundle = c1_bundle()
        report = self.c2(bundle)
        issuer = report['issuers'][0]
        observation = issuer['observations'][0]
        self.assertTrue(bundle['issuers'][0]['issuer_collection_eligible'])
        self.assertTrue(bundle['issuers'][0]['instruments'][0]['instrument_valuation_eligible'])
        self.assertFalse(issuer['instruments'][0]['valuation_usable'])
        self.assertEqual(issuer['instruments'][0]['provenance_assertion_status'],
                         'CALLER_ASSERTIONS_ONLY_NOT_REVALIDATED')
        self.assertFalse(report['valuation_usable'])
        self.assertEqual(issuer['accounting_validation'], 'UNCHECKED')
        self.assertEqual(report['source_snapshot']['raw_document'], bundle['normalization']['raw_document'])
        self.assertEqual(observation['raw_pointer'], '/companies/0/facts/us-gaap/Revenues/units/USD/0')
        self.assertEqual(observation['source_sha256'], report['source_snapshot']['sha256'])
        self.assertEqual(observation['checks']['raw_pointer'], 'PASS')
        self.assertEqual(observation['checks']['raw_sha256'], 'PASS')
        self.assertEqual(observation['checks']['period'], 'PASS')
        self.assertEqual(observation['checks']['unit'], 'PASS')
        self.assertEqual(observation['checks']['official_filing'], 'UNCHECKED_MISSING')
        self.assertNotEqual(report['source_verification'], 'VERIFIED')
        self.assertNotEqual(report['accounting_validation'], 'VERIFIED')
        self.assertNotEqual(observation['status'], 'VERIFIED')

    def test_filing_assertion_match_is_not_authenticated_or_verified(self):
        report = self.c2(c1_bundle(), [filing_evidence()])
        item = report['observations'][0]
        self.assertEqual(item['checks']['official_filing'], 'ASSERTIONS_MATCH_UNAUTHENTICATED')
        self.assertEqual(item['accounting_validation'], 'UNCHECKED')
        self.assertEqual(report['source_verification'], 'NOT_PERFORMED')
        self.assertFalse(report['valuation_usable'])

    def test_fictitious_reference_or_invalid_proof_blocks_as_conflict(self):
        evidence = filing_evidence(url='https://sec.example.invalid/Archives/edgar/data/1/000000000126000001/fake.xml')
        report = self.c2(c1_bundle(), [evidence])
        self.assertEqual(report['invalid_evidence'][0]['reasons'], ['EVIDENCE_REFERENCE_INVALID'])
        self.assertEqual(report['observations'][0]['checks']['official_filing'], 'INVALID_RECORD')
        self.assertEqual(report['accounting_validation'], 'CONFLICT')
        self.assertFalse(report['valuation_usable'])

    def test_conflicting_filing_fact_never_selects_a_value(self):
        fact = dict(namespace='us-gaap', concept='Revenues', unit='USD', value='101',
                    start='2025-07-01', end='2026-06-30', dimensions={})
        report = self.c2(c1_bundle(), [filing_evidence(facts=[fact])])
        item = report['observations'][0]
        self.assertEqual(item['checks']['official_filing'], 'CONFLICT')
        self.assertEqual(item['status'], 'CONFLICT')
        self.assertEqual(report['accounting_validation'], 'CONFLICT')
        self.assertFalse(item['valuation_usable'])

    def test_amendments_are_retained_and_flagged_for_review(self):
        rows = [dict(ROW, form='10-K/A', accn='0000000001-26-000002')]
        bundle = c1_bundle(rows=rows, form='10-K/A')
        report = self.c2(bundle)
        self.assertEqual(len(report['observations']), 1)
        self.assertEqual(report['observations'][0]['checks']['amendment'], 'REVIEW_REQUIRED')
        self.assertIsNone(report['issuers'][0]['groups'][0]['selected_observation_id'])
        self.assertIn('AMENDMENT_REVIEW_REQUIRED', report['issuers'][0]['valuation_blockers'])

    def test_c1_conflict_preserves_both_rows_without_arbitration(self):
        rows = [dict(ROW), dict(ROW, val=99, form='10-K/A', accn='0000000001-26-000002')]
        report = self.c2(c1_bundle(rows=rows))
        self.assertEqual(len(report['observations']), 2)
        self.assertTrue(all(item['status'] == 'CONFLICT' for item in report['observations']))
        self.assertEqual(report['issuers'][0]['groups'][0]['status'], 'CONFLICT')
        self.assertIsNone(report['issuers'][0]['groups'][0]['selected_observation_id'])

    def test_dimensions_period_unit_and_fiscal_period_are_explicit(self):
        dimensional = c1_bundle(rows=[dict(ROW, dimensions={'product': 'fictional'})])
        result = self.c2(dimensional)
        self.assertEqual(result['observations'][0]['checks']['dimensions'], 'OUT_OF_SCOPE')
        self.assertEqual(result['observations'][0]['structure'], 'OUT_OF_SCOPE')

        bad_period = c1_bundle(rows=[dict(ROW, fp='Q1')])
        result = self.c2(bad_period)
        self.assertEqual(result['observations'][0]['checks']['fiscal_exercise'], 'CONFLICT')
        self.assertEqual(result['observations'][0]['status'], 'CONFLICT')

        bad_unit = c1_bundle(unit='XYZ')
        result = self.c2(bad_unit)
        self.assertEqual(result['observations'][0]['checks']['unit'], 'FAIL')
        self.assertFalse(result['valuation_usable'])

    def test_excluded_row_pointer_identity_conflicts_at_all_levels(self):
        for field, value in [('concept', 'Assets'), ('id', '0' * 64)]:
            with self.subTest(field=field):
                bundle = c1_bundle(rows=[dict(ROW, dimensions={'segment': 'example'})])
                bundle['normalization']['observations'][0][field] = value
                report = self.c2(bundle)
                obs = report['observations'][0]
                self.assertEqual(obs['checks']['raw_pointer'], 'FAIL')
                self.assertEqual(obs['status'], 'CONFLICT')
                self.assertEqual(report['issuers'][0]['accounting_validation'], 'CONFLICT')
                self.assertEqual(report['accounting_validation'], 'CONFLICT')

    def test_raw_concept_controls_period_and_units_of_excluded_rows(self):
        shares = 'WeightedAverageNumberOfDilutedSharesOutstanding'
        for concept, unit, row, unit_status, period, status in [
            (shares, 'USD', ROW, 'FAIL', 'PASS', 'CONFLICT'),
            (shares, 'shares', dict(ROW, dimensions={'segment': 'example'}), 'PASS', 'PASS', 'UNCHECKED'),
            ('Revenues', 'USD', dict(ROW, start='2026-04-01'), 'PASS', 'OUT_OF_SCOPE', 'UNCHECKED'),
        ]:
            with self.subTest(concept=concept, unit=unit):
                obs = self.c2(c1_bundle(rows=[row], concept=concept, unit=unit))['observations'][0]
                self.assertEqual((obs['checks']['unit'], obs['checks']['period'], obs['status']),
                                 (unit_status, period, status))

    def test_conflicts_survive_deleted_or_relabelled_c1_groups(self):
        for remove in (True, False):
            with self.subTest(remove=remove):
                bundle = c1_bundle(rows=[ROW, dict(ROW, val=99)])
                groups = bundle['normalization']['groups']
                if remove:
                    groups.clear()
                else:
                    groups[0]['status'] = 'REPEATED_VALUE_CANDIDATES'
                report = self.c2(bundle)
                self.assertEqual(report['accounting_validation'], 'CONFLICT')
                self.assertTrue(all(o['status'] == 'CONFLICT' for o in report['observations']))
                self.assertIn('CONFLICT_UNARBITRATED', report['issuers'][0]['valuation_blockers'])

    def test_invalid_evidence_preserves_other_assertions(self):
        for field, value, reason in [
            *(('taxonomy', v, 'EVIDENCE_TAXONOMY_INVALID') for v in ([], {}, None, 7, 'unknown')),
            ('document_url', 'https://[', 'EVIDENCE_REFERENCE_INVALID'),
        ]:
            with self.subTest(field=field, value=value):
                bad = dict(filing_evidence(), **{field: value})
                report = self.c2(c1_bundle(), [bad, filing_evidence()])
                self.assertEqual(report['invalid_evidence'][0]['reasons'], [reason])
                self.assertEqual(report['observations'][0]['checks']['official_filing'],
                                 'ASSERTIONS_MATCH_UNAUTHENTICATED')
                self.assertEqual(report['visibility'], 'PRIVATE')
                self.assertFalse(report['valuation_usable'])
        for taxonomy in ('us-gaap', 'ifrs-full'):
            report = self.c2(c1_bundle(), [dict(filing_evidence(), taxonomy=taxonomy)])
            self.assertEqual(report['invalid_evidence'], [])

    def test_parent_attribution_stays_unchecked(self):
        normalization = normalize_snapshot(
            (BASE / 'data/sec_facts.json').read_bytes().decode('utf-8'),
            as_of=date(2026, 11, 3), source_locator='data/sec_facts.json')
        result = validate_normalization(normalization,
            taxonomy_by_cik={MSFT_CIK: 'us-gaap', CAMECO_CIK: 'ifrs-full'})
        for cik, taxonomy in ((MSFT_CIK, 'us-gaap'), (CAMECO_CIK, 'ifrs-full')):
            issuer = next(item for item in result['issuers'] if item['cik'] == cik)
            self.assertEqual(issuer['taxonomy'], taxonomy)
            self.assertTrue(any(item['namespace'] == taxonomy and item['structure'] == 'CANDIDATE'
                                for item in issuer['observations']))
            self.assertEqual(issuer['accounting_validation'], 'UNCHECKED')
            self.assertFalse(issuer['valuation_usable'])
        for item in result['observations']:
            if item['metric'] == 'net_income_reported':
                self.assertEqual(item['checks']['parent_attribution'], 'UNCHECKED_PARENT_ATTRIBUTION')
        self.assertEqual(result['source_verification'], 'NOT_PERFORMED')
        self.assertNotEqual(result['source_verification'], 'VERIFIED')
        self.assertNotEqual(result['accounting_validation'], 'VERIFIED')
        self.assertTrue(all(item['status'] != 'VERIFIED' for item in result['observations']))

    def test_corrupt_raw_hash_or_pointer_is_not_silently_trusted(self):
        bundle = c1_bundle()
        bundle['normalization']['observations'][0]['raw_pointer'] = '/companies/9/facts/us-gaap/Revenues/units/USD/0'
        result = self.c2(bundle)
        self.assertEqual(result['observations'][0]['checks']['raw_pointer'], 'FAIL')
        self.assertEqual(result['observations'][0]['status'], 'CONFLICT')
        self.assertFalse(result['valuation_usable'])

        bundle = c1_bundle()
        bundle['normalization']['raw_document'] += ' '
        result = self.c2(bundle)
        self.assertEqual(result['observations'][0]['checks']['raw_sha256'], 'FAIL')
        self.assertEqual(result['accounting_validation'], 'CONFLICT')

    def test_private_output_blocks_public_serialization(self):
        marker = 'PRIVATE_C2_FIXTURE_MARKER'
        evidence = filing_evidence(url=(
            'https://www.sec.gov/Archives/edgar/data/1/000000000126000001/' + marker + '.xml'))
        report = self.c2(c1_bundle(), [evidence])
        self.assertEqual(report['visibility'], 'PRIVATE')
        self.assertIn(marker, serialize(report))
        with self.assertRaises(PublicExportBlocked) as blocked:
            serialize(report, audience='public')
        self.assertNotIn(marker, str(blocked.exception))

    def test_output_schema_cannot_label_unverified_claim_as_verified(self):
        report = self.c2(c1_bundle())
        report['observations'][0]['checks']['official_filing'] = 'VERIFIED'
        with self.assertRaises(ValidationError):
            validator('c2-accounting-validation.schema.json').validate(report)

    def test_no_snapshot_does_not_create_fake_financial_input(self):
        record = identity_record()
        record['instrument']['kind'] = 'ETF'
        with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
            bundle = build_bundle(json.dumps([record]), 'not a cache; ETF must exit first',
                                  as_of=CLOCK, source_locator='no-cache')
        report = self.c2(bundle)
        self.assertIsNone(report['source_snapshot'])
        self.assertEqual(report['observations'], [])
        self.assertFalse(report['valuation_usable'])

    def test_missing_issuer_cik_in_snapshot_keeps_b3_gates_explicit(self):
        report = self.c2(c1_bundle(snapshot_cik='0000000002'))
        self.assertEqual({item['cik'] for item in report['issuers']}, {'0000000001'})
        issuer = next(item for item in report['issuers'] if item['cik'] == '0000000001')
        self.assertIsNone(issuer['coverage'])
        self.assertEqual(issuer['observations'], [])
        self.assertEqual(len(issuer['instruments']), 1)
        self.assertTrue(issuer['instruments'][0]['issuer_collection_eligible'])
        self.assertTrue(issuer['instruments'][0]['instrument_valuation_eligible'])
        self.assertFalse(issuer['instruments'][0]['valuation_usable'])
        self.assertIn('C1_CIK_NOT_PRESENT_IN_SNAPSHOT', issuer['valuation_blockers'])


if __name__ == '__main__':
    unittest.main()
