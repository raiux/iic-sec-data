"""Fictitious flat instances. Nothing is downloaded or authenticated."""
import base64
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from scripts.b3_c1_pipeline import build_bundle
from scripts.offline_pipeline import PublicExportBlocked
from scripts.reconcile_sec_xbrl import DIM, INSTANCE, ISO, XSI, reconcile, serialize
from scripts.validate_sec_accounting import validate_bundle
from tests.test_b3_c1_pipeline import identity_record
from tests.test_c2_accounting import CLOCK, c1_bundle
from tests.test_normalize_sec import ROW, fixture
from tests.test_sec_document_evidence import package

CONTEXT = ('<i:context id="c"><i:entity><i:identifier scheme="http://www.sec.gov/CIK">1</i:identifier>'
           '</i:entity><i:period><i:startDate>2025-07-01</i:startDate>'
           '<i:endDate>2026-06-30</i:endDate></i:period></i:context>')
UNIT = '<i:unit id="u"><i:measure>m:USD</i:measure></i:unit>'
FACT = '<t:Revenues id="f" contextRef="c" unitRef="u" decimals="2">100.000</t:Revenues>'
US = 'http://fasb.org/us-gaap/2025'


def xml(fact=FACT, ctx=CONTEXT, unit=UNIT, ns=US):
    return (f'<i:xbrl xmlns:i="{INSTANCE}" xmlns:m="{ISO}" xmlns:t="{ns}" '
            f'xmlns:xsi="{XSI}" xmlns:d="{DIM}">{ctx}{unit}{fact}</i:xbrl>').encode()


def attach(raw):
    return dict(package(), raw_bytes_base64=base64.b64encode(raw).decode(),
                declared_sha256=hashlib.sha256(raw).hexdigest())


class XbrlTests(unittest.TestCase):
    def report(self, raw, bundle=None):
        return reconcile(bundle or c1_bundle(), attach(raw))

    def test_prefix_aliases_exact_decimal_private_and_immutable(self):
        raw = xml().replace(b'xmlns:t=', b'xmlns:company=').replace(b't:Revenues', b'company:Revenues')
        bundle, pkg = c1_bundle(), attach(raw)
        before = copy.deepcopy((bundle, pkg))
        r = reconcile(bundle, pkg)
        self.assertEqual((bundle, pkg), before)
        self.assertEqual(r['c2'], validate_bundle(bundle))
        self.assertEqual(r['comparisons'][0]['status'], 'MATCH_UNAUTHENTICATED')
        self.assertEqual((r['facts'][0]['value'], r['facts'][0]['decimals']), ('100.000', '2'))
        self.assertEqual(r['facts'][0]['qname'], (US, 'Revenues'))
        self.assertEqual(r['document']['package'], pkg)
        self.assertFalse(r['valuation_usable'])
        self.assertEqual(r['parent_attribution'], 'UNCHECKED')
        with self.assertRaises(PublicExportBlocked):
            serialize(r, audience='public')

    def test_ifrs_instant_and_accuracy(self):
        record = fixture()
        value = 12345678901234567891234567891
        row = dict(ROW, val=value)
        del row['start']
        record['companies'][0]['facts'] = {'ifrs-full': {'Assets': {'units': {'USD': [row]}}}}
        bundle = build_bundle(json.dumps([identity_record()]), json.dumps(record), as_of=CLOCK,
                              source_locator='fictional-cache')
        ctx = CONTEXT.replace('<i:startDate>2025-07-01</i:startDate><i:endDate>2026-06-30</i:endDate>',
                              '<i:instant>2026-06-30</i:instant>')
        raw = xml(FACT.replace('Revenues', 'Assets').replace('100.000', str(value)), ctx,
                  ns='https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full')
        r = self.report(raw, bundle)
        self.assertEqual(r['facts'][0]['value'], str(value))
        self.assertEqual(r['comparisons'][0]['status'], 'MATCH_UNAUTHENTICATED')

    def test_context_mismatch_and_compound_units(self):
        for raw in (xml(ctx=CONTEXT.replace('>1<', '>2<')),
                    xml(ctx=CONTEXT.replace('gov/CIK', 'gov/other')),
                    xml(ctx=CONTEXT.replace('2025-07-01', '2025-07-02')),
                    xml(ns='https://example.invalid/us-gaap/2025'), xml(fact='')):
            with self.subTest(raw=raw):
                self.assertEqual(self.report(raw)['comparisons'][0]['status'], 'NO_COMPARABLE_FACT')
        num = '<i:unitNumerator><i:measure>m:USD</i:measure></i:unitNumerator>'
        den = '<i:unitDenominator><i:measure>i:shares</i:measure></i:unitDenominator>'
        raw = xml(unit=f'<i:unit id="u"><i:divide>{num}{den}</i:divide></i:unit>')
        r = self.report(raw)
        self.assertEqual(r['units']['u']['denominator'], [(INSTANCE, 'shares')])
        self.assertEqual(r['comparisons'][0]['status'], 'NO_COMPARABLE_FACT')

    def test_dimensions_kept_uninterpreted(self):
        for name, content in [('explicitMember', 't:Segment'), ('typedMember', '<t:Code>01</t:Code>')]:
            member = f'<d:{name} dimension="t:Axis">{content}</d:{name}>'
            r = self.report(xml(ctx=CONTEXT.replace('</i:entity>', '<i:segment>'+member+'</i:segment></i:entity>')))
            self.assertEqual(r['contexts']['c']['dimensions'][0]['axis'], (US, 'Axis'))
            self.assertEqual(r['comparisons'][0]['status'], 'NO_COMPARABLE_FACT')

    def test_invalid_or_ambiguous_refs_and_values(self):
        for raw, reason in [(xml(ctx=''), 'CONTEXT_MISSING_OR_AMBIGUOUS'),
                            (xml(ctx=CONTEXT*2), 'CONTEXT_MISSING_OR_AMBIGUOUS'),
                            (xml(unit=UNIT*2), 'UNIT_MISSING_INVALID_OR_AMBIGUOUS'),
                            (xml(fact=FACT.replace('id="f"', 'xsi:nil="true"')), 'NIL_FACT'),
                            (xml(fact=FACT.replace('100.000', 'NaN')), 'INVALID_NUMERIC_FACT')]:
            with self.subTest(reason=reason):
                r = self.report(raw)
                self.assertIn(reason, r['facts'][0]['reasons'])
                self.assertEqual(r['comparisons'][0]['fact_indices'], [])

    def test_duplicate_facts_and_amendments_remain_separate(self):
        for value, expected in [('100.000', 'MULTIPLE_MATCHES_UNSELECTED'), ('99', 'VALUE_CONFLICT')]:
            raw = xml(fact=FACT+FACT.replace('"f"', '"f2"').replace('100.000', value))
            bundle = c1_bundle(rows=[ROW, dict(ROW, form='10-K/A', accn='0000000001-26-000002')])
            comparisons = self.report(raw, bundle)['comparisons']
            self.assertEqual(comparisons[0]['fact_indices'], [0, 1])
            self.assertEqual([o['status'] for o in comparisons], [expected, 'PACKAGE_IDENTITY_DIVERGES'])

    def test_hostile_malformed_and_unsupported_inputs(self):
        for raw, reason in [
            (b'<broken', 'MALFORMED_XML'), (b'<html/>', 'UNSUPPORTED_FORMAT'),
            ('<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><x/>'.encode('utf-16'), 'DTD_OR_ENTITY_FORBIDDEN'),
            (b'<!DOCTYPE x [<!ENTITY a "a"><!ENTITY b "&a;&a;">]><x>&b;</x>', 'DTD_OR_ENTITY_FORBIDDEN'),
            (b'<x>'*65+b'</x>'*65, 'XML_COMPLEXITY_LIMIT'),
            (b'<x>'+b'<y/>'*20001+b'</x>', 'XML_COMPLEXITY_LIMIT'),
            (b'x'*(2*1024*1024+1), 'DOCUMENT_SIZE_LIMIT'),
        ]:
            with self.subTest(reason=reason), patch('socket.create_connection', side_effect=AssertionError):
                self.assertEqual(self.report(raw)['reasons'], [reason])

    def test_unknown_encoding_is_a_controlled_diagnostic(self):
        raw = b'<?xml version="1.0" encoding="NOT-A-CODEC"?>' + xml()
        self.assertEqual(self.report(raw)['reasons'], ['MALFORMED_XML'])

    def test_integrity_blocks_parsing(self):
        pkg = attach(xml())
        pkg['declared_sha256'] = '0'*64
        self.assertEqual(reconcile(c1_bundle(), pkg)['facts'], [])
