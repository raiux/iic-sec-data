import copy
import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.ingest_identity import load_json, prepare, reconcile, plan, route_prepared

AS_OF = datetime(2026, 11, 3, tzinfo=timezone.utc)
FIXTURE = json.loads((Path(__file__).parent / 'fixtures/identity-fictional.json').read_text())[0]
HAS_B1 = importlib.util.find_spec('scripts.route_instrument') is not None


def record():
    return copy.deepcopy(FIXTURE)


def proof(ref='fictional-extra-proof'):
    return dict(ref=ref, checked_at='2026-01-01T00:00:00Z', result='VERIFIED')


class IngestionTests(unittest.TestCase):
    def test_json_boundary(self):
        for payload in ('{', '{"x":1,"x":2}', '[NaN]', '[Infinity]', None, 2):
            raw, error = load_json(payload)
            self.assertIsNone(raw)
            self.assertEqual(error.status, 'NEEDS_REVIEW')
        self.assertIsNone(load_json(json.dumps([FIXTURE]))[1])

    def test_malformed_records(self):
        for bad in (None, [], 'x', {}, {'issuer': []}, True):
            self.assertEqual(prepare(bad, as_of=AS_OF).status, 'NEEDS_REVIEW')
        for section, field, value in [('issuer','cik',1),('issuer','lei',{}),
                                      ('instrument','isin',123),('listing','mic',[]),
                                      ('listing','ticker',' FICT'),('instrument','kind',{})]:
            r = record()
            r[section][field] = value
            self.assertEqual(prepare(r, as_of=AS_OF).status, 'NEEDS_REVIEW')

    def test_no_implicit_identifier_conversion(self):
        for section, field, value in [('issuer','cik','1'),('listing','mic','xxxx'),
                                      ('instrument','isin','zz0000000000')]:
            r = record()
            r[section][field] = value
            self.assertEqual(prepare(r, as_of=AS_OF).status, 'NEEDS_REVIEW')

    def test_identity_dates_and_conflicts(self):
        for changes in ({'result':'CONFLICT'}, {'checked_at':'yesterday'},
                        {'checked_at':'2099-01-01T00:00:00Z'}, {'checked_at':'2026-01-01'}):
            r = record()
            r['instrument']['identity'].update(changes)
            self.assertEqual(prepare(r, as_of=AS_OF).status, 'NEEDS_REVIEW')

    def test_etf_before_source(self):
        for kind in ('ETF','ETN','FUND'):
            r = record()
            r['instrument']['kind'] = kind
            r['source'] = object()
            self.assertEqual(route_prepared(prepare(r, as_of=AS_OF))['status'], 'EXCLUDED')

    def test_missing_and_unverified_sources(self):
        for mutation in ('missing','date','reference_only','type','taxonomy_type'):
            r = record()
            if mutation == 'missing':del r['source']
            if mutation == 'date':r['source']['access']['identity']['checked_at']='bad'
            if mutation == 'reference_only':r['source']['official_check']['result']='UNVERIFIED'
            if mutation == 'type':r['source']['access']['authorization']=True
            if mutation == 'taxonomy_type':r['source']['xbrl']['taxonomy']=[]
            self.assertEqual(prepare(r, as_of=AS_OF).status,'UNVERIFIED')

    def test_blocked_and_source_mismatch(self):
        for key, val in [('authorization','DENIED'),('result','UNAVAILABLE')]:
            r = record()
            r['source']['access'][key]=val
            self.assertEqual(prepare(r, as_of=AS_OF).status,'BLOCKED')
        r=record()
        r['source']['issuer_id']='other'
        self.assertEqual(prepare(r, as_of=AS_OF).status,'NEEDS_REVIEW')

    def test_duplicate_and_collision_order_independent(self):
        self.assertTrue(all(x.status=='PREPARED' for x in reconcile([record(),record()],as_of=AS_OF)))
        for section, field, value in [('issuer','cik','0000000002'),
                                      ('instrument','isin','ZZ0000000001'),
                                      ('listing','mic','YYYY')]:
            a,b=record(),record()
            b[section][field]=value
            for rows in ([a,b],[b,a]):
                self.assertEqual([x.status for x in reconcile(rows,as_of=AS_OF)],
                                 ['NEEDS_REVIEW','NEEDS_REVIEW'])

    def test_ticker_ambiguity(self):
        a,b=record(),record()
        b['instrument']['id']='another-security'
        b['instrument']['isin']='ZZ0000000001'
        b['listing']['instrument_id']='another-security'
        b['listing']['id']='another-listing'
        self.assertTrue(all(x.status=='NEEDS_REVIEW' for x in reconcile([a,b],as_of=AS_OF)))

    def test_reverse_identifier_collision_and_access_conflict(self):
        a,b=record(),record()
        b['issuer']['id']='other-issuer'
        b['instrument']['issuer_id']='other-issuer'
        b['source']['issuer_id']='other-issuer'
        b['instrument']['id']='other-share'
        b['listing'].update(id='other-listing',instrument_id='other-share',ticker='OTHER')
        b['instrument']['isin']='ZZ0000000001'
        self.assertTrue(all(x.status=='NEEDS_REVIEW' for x in reconcile([a,b],as_of=AS_OF)))
        b=record()
        b['source']['access']['authorization']='DENIED'
        self.assertTrue(all(x.status=='NEEDS_REVIEW' for x in reconcile([a,b],as_of=AS_OF)))

    def test_adr_unknown_underlying(self):
        r=record()
        r['instrument']['kind']='ADR'
        self.assertEqual(prepare(r,as_of=AS_OF).status,'NEEDS_REVIEW')

    def test_no_source_call_even_for_valid_plan(self):
        with patch('socket.socket',side_effect=AssertionError('Network forbidden')):
            decisions=plan([record()],as_of=AS_OF)
            self.assertIn(decisions[0]['status'],('ROUTABLE','DEPENDENCY_BLOCKED'))

    def test_delisted_and_provenance_immutable(self):
        r=record()
        r['listing']['status']='DELISTED'
        before=copy.deepcopy(r)
        item=prepare(r,as_of=AS_OF)
        self.assertEqual(item.status,'ARCHIVED')
        self.assertEqual(item.provenance,before)
        self.assertEqual(r,before)

    def test_missing_b1_explicit(self):
        item=prepare(record(),as_of=AS_OF)
        with patch('scripts.ingest_identity.import_module',
                   side_effect=ModuleNotFoundError(name='scripts.route_instrument')):
            self.assertEqual(route_prepared(item)['status'],'DEPENDENCY_BLOCKED')

    @unittest.skipUnless(HAS_B1, 'B1 dependency absent; exercised separately with pinned B1 commit')
    def test_b1_receipt_ratios_and_deduplication(self):
        a=record()
        for ratio in (None,'2',2,2.0,{}, {'decimal':'0','identity':proof()},
                      {'decimal':2,'identity':proof()}, {'decimal':'NaN','identity':proof()}):
            b=record()
            b['instrument'].update(id='receipt-A',kind='ADR',isin='ZZ0000000001',
                                   underlying=dict(issuer_id=a['issuer']['id'],identity=proof()),rights=proof())
            b['listing'].update(id='receipt-listing',instrument_id='receipt-A',ticker='FICT-ADR')
            if ratio is not None:b['instrument']['ratio']=ratio
            decisions=plan([a,b],as_of=AS_OF)
            self.assertTrue(decisions[0]['collection_required'])
            self.assertTrue(decisions[1]['issuer_collection_eligible'])
            self.assertFalse(decisions[1]['collection_required'])
            self.assertFalse(decisions[1]['instrument_valuation_eligible'])

    @unittest.skipUnless(HAS_B1, 'B1 dependency absent')
    def test_b1_verified_typed_ratio(self):
        r=record()
        r['instrument'].update(kind='ADR',underlying=dict(issuer_id=r['issuer']['id'],identity=proof()),
                               ratio=dict(decimal='0.125',identity=proof()),rights=proof())
        item=prepare(r,as_of=AS_OF)
        self.assertEqual(str(item.instrument['shares_per_receipt']),'0.125')
        self.assertTrue(route_prepared(item)['instrument_valuation_eligible'])

    @unittest.skipUnless(HAS_B1, 'B1 dependency absent')
    def test_b1_source_gate(self):
        for adapter,taxonomy in [('SEC_COMPANYFACTS','us-gaap'),('SEC_COMPANYFACTS','ifrs-full'),
                                 ('ESEF','ifrs-full')]:
            r=record()
            r['source']['adapter']=adapter
            r['source']['xbrl']['taxonomy']=taxonomy
            self.assertEqual(plan([r],as_of=AS_OF)[0]['status'],'ROUTABLE')
        r=record()
        r['source']['xbrl']['taxonomy']='dei'
        self.assertEqual(plan([r],as_of=AS_OF)[0]['status'],'UNVERIFIED')
