"""Fictional evidence; country of listing plays no part in these decisions."""
import unittest
from dataclasses import replace
from decimal import Decimal
from scripts.route_instrument import Instrument, SourceEvidence, route

SHARE = Instrument('share-A', 'ORDINARY_SHARE', 'issuer-A', True, 'identity-record',
                   listing_status='ACTIVE')
SEC = SourceEvidence('SEC_COMPANYFACTS', 'issuer-A', 'AUTHORIZED', 'official-record',
                     'companyfacts-probe', 'xbrl-filing', '0000000001', 'us-gaap', 'verified-xbrl-record')
ADR = replace(SHARE, instrument_id='receipt-A', kind='ADR', underlying_ref='verified-link')


class RouterTests(unittest.TestCase):
    def test_exclusions_examined_before_source(self):
        for kind in ('ETF', 'ETN', 'FUND'):
            result = route(replace(SHARE, kind=kind), object())
            self.assertEqual(result.status, 'EXCLUDED')
            self.assertFalse(result.issuer_collection_eligible or result.instrument_valuation_eligible)
            self.assertIsNone(result.adapter)

    def test_unresolved_identity(self):
        for changes in [dict(kind='UNKNOWN'), dict(conflicts=('AMBIGUOUS_TICKER',)),
                        dict(conflicts=('ISIN_CONFLICT',)), dict(conflicts=('MIC_CONFLICT',))]:
            self.assertEqual(route(replace(SHARE, **changes), SEC).status, 'NEEDS_REVIEW')

    def test_verified_us_canada_and_europe(self):
        for evidence in (SEC, replace(SEC, taxonomy='ifrs-full'),
                         replace(SEC, adapter='ESEF', cik='', taxonomy='ifrs-full')):
            result = route(SHARE, evidence)
            self.assertEqual(result.adapter, evidence.adapter)
            self.assertTrue(result.collection_required and result.instrument_valuation_eligible)
            self.assertIn(evidence.filing_ref, result.evidence_refs)

    def test_cik_alone_and_incomplete_evidence(self):
        for evidence in (SourceEvidence('SEC_COMPANYFACTS', 'issuer-A', cik=SEC.cik),
                         *(replace(SEC, **{field: ''}) for field in
                           ('filing_ref', 'xbrl_filing_ref', 'taxonomy', 'access_check_ref', 'official_source_ref')),
                         replace(SEC, taxonomy='dei'), replace(SEC, access='UNKNOWN')):
            result = route(SHARE, evidence)
            self.assertEqual(result.status, 'UNVERIFIED')
            self.assertEqual(result.proposed_adapter, SEC.adapter)
            self.assertIsNone(result.adapter)
            self.assertFalse(result.collection_required)

    def test_blocked_canada_no_substitution(self):
        result = route(SHARE, SourceEvidence('SEDAR_PLUS', 'issuer-A', access='DENIED'))
        self.assertEqual(result.status, 'BLOCKED')
        self.assertFalse(result.issuer_collection_eligible)
        self.assertIsNone(result.adapter)

    def test_receipt_shares_issuer_but_not_valuation(self):
        result = route(ADR, SEC)
        self.assertTrue(result.issuer_collection_eligible and result.collection_required)
        self.assertFalse(result.instrument_valuation_eligible)
        key = route(SHARE, SEC).collection_key
        seen = route(ADR, SEC, frozenset({key}))
        self.assertEqual(seen.collection_key, key)
        self.assertFalse(seen.collection_required or seen.instrument_valuation_eligible)
        known_ratio = replace(ADR, shares_per_receipt=Decimal('2'), ratio_ref='ratio-record')
        self.assertFalse(route(known_ratio, SEC).instrument_valuation_eligible)
        self.assertTrue(route(replace(known_ratio, rights_ref='rights-record'), SEC).instrument_valuation_eligible)

    def test_unknown_underlying_or_mismatched_filing(self):
        for instrument in (replace(ADR, issuer_id=None), replace(ADR, underlying_ref=''),
                           replace(ADR, issuer_id='different-issuer')):
            result = route(instrument, SEC)
            self.assertEqual(result.status, 'NEEDS_REVIEW')
            self.assertFalse(result.collection_required or result.instrument_valuation_eligible)

    def test_delisted(self):
        self.assertEqual(route(replace(SHARE, listing_status='DELISTED'), SEC).status, 'ARCHIVED')
