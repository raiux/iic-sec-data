"""C1 fixtures are fictitious unless explicitly loading the existing repository snapshot."""
import copy
import hashlib
import json
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts.normalize_sec import normalize_snapshot, parse_raw

CLOCK = date(2026,11,3)
BASE = Path(__file__).resolve().parents[1]
ROW = dict(start='2025-07-01',end='2026-06-30',val=100,filed='2026-08-01',
           accn='0000000001-26-000001',form='10-K',fy=2026,fp='FY')


def fixture(rows=None):
    return dict(schema=1,companies=[dict(name='FICTIONAL ISSUER',cik='0000000001',
        facts={'us-gaap':{'Revenues':{'units':{'USD':rows if rows is not None else [dict(ROW)]}}}})])


def run(raw):
    return normalize_snapshot(json.dumps(raw),as_of=CLOCK,source_locator='fictional-cache.json')


class SecNormalizationTests(unittest.TestCase):
    def test_raw_immutable_and_deterministic(self):
        raw=fixture()
        before=copy.deepcopy(raw)
        payload=json.dumps(raw)
        a,b=run(raw),run(raw)
        self.assertEqual(a,b)
        self.assertEqual(raw,before)
        self.assertEqual(a['raw_document'],payload)
        self.assertEqual(a['source']['sha256'],hashlib.sha256(payload.encode()).hexdigest())
        self.assertEqual(a['visibility'],'PRIVATE')
        self.assertFalse(a['valuation_usable'])
        self.assertIsNone(a['groups'][0]['selected_observation_id'])

    def test_exact_decimal_lexical_preservation(self):
        payload=json.dumps(fixture()).replace('"val": 100','"val": 0.12345678901234567890123456789')
        result=normalize_snapshot(payload,as_of=CLOCK,source_locator='fixture')
        self.assertEqual(result['observations'][0]['normalized']['value'],'0.12345678901234567890123456789')
        self.assertEqual(result['raw_document'],payload)

    def test_duration_is_from_dates_not_frame_or_fy(self):
        result=run(fixture())
        entry=result['observations'][0]['normalized']
        self.assertEqual((entry['start'],entry['end']),('2025-07-01','2026-06-30'))
        self.assertEqual(entry['reported_fy'],2026)
        bad=run(fixture([dict(ROW,start='2026-04-01',fp='FY')]))
        self.assertEqual(bad['observations'][0]['structure'],'OUT_OF_SCOPE')
        self.assertEqual(bad['groups'],[])

    def test_invalid_row_does_not_remove_valid_siblings(self):
        invalid=[dict(ROW,val=None),dict(ROW,filed='bad'),dict(ROW,accn='unknown'),None]
        result=run(fixture([dict(ROW)]+invalid))
        self.assertEqual(len(result['observations']),5)
        self.assertEqual([o['structure'] for o in result['observations']],['CANDIDATE']+['REJECTED']*4)

    def test_out_of_scope_and_unmapped_raw_remain(self):
        raw=fixture([dict(ROW),dict(ROW,form='8-K'),dict(ROW,dimensions={'segment':'fictional'})])
        raw['companies'][0]['facts']['custom']={'Unmapped':{'units':{'USD':[dict(ROW)]}}}
        result=run(raw)
        self.assertEqual([o['structure'] for o in result['observations']],
                         ['CANDIDATE','OUT_OF_SCOPE','OUT_OF_SCOPE','UNMAPPED'])
        self.assertEqual(parse_raw(result['raw_document']),raw)

    def test_conflicts_amendments_and_repeats_not_overwritten(self):
        for other,expected in [(dict(ROW,val=99,form='10-K/A',accn='0000000001-26-000002'),'CONFLICT'),
                               (dict(ROW),'REPEATED_VALUE_CANDIDATES')]:
            result=run(fixture([dict(ROW),other]))
            group=result['groups'][0]
            self.assertEqual(group['status'],expected)
            self.assertEqual(len(group['observation_ids']),2)
            self.assertIsNone(group['selected_observation_id'])
            self.assertFalse(group['valuation_usable'])
            self.assertEqual(len(group['amendment_candidates']),int(expected=='CONFLICT'))

    def test_units_and_debt_definitions_distinct(self):
        raw=fixture()
        raw['companies'][0]['facts']['us-gaap']['Revenues']['units']['shares']=[dict(ROW)]
        for tag in ('LongTermDebt','LongTermDebtNoncurrent'):
            row=dict(ROW)
            del row['start']
            raw['companies'][0]['facts']['us-gaap'][tag]={'units':{'USD':[row]}}
        result=run(raw)
        self.assertIn('UNIT_OUT_OF_SCOPE',result['observations'][1]['reasons'])
        metrics={g['metric'] for g in result['groups']}
        self.assertIn('long_term_debt_reported',metrics)
        self.assertIn('noncurrent_long_term_debt_reported',metrics)
        self.assertNotIn('total_debt',metrics)

    def test_missing_not_zero_and_instant_not_duration(self):
        result=run(fixture())
        self.assertIn('diluted_average_shares_reported',result['coverage'][0]['missing_metrics'])
        raw=fixture()
        raw['companies'][0]['facts']['us-gaap']['Assets']={'units':{'USD':[dict(ROW)]}}
        result=run(raw)
        self.assertIn('INSTANT_HAS_START',result['observations'][-1]['reasons'])
        self.assertIsNone(result['observations'][-1]['normalized'])

    def test_reject_malformed_duplicate_cik_and_duplicate_json_key(self):
        for payload in ('{"schema":1,"schema":1,"companies":[]}','[NaN]','{}'):
            with self.assertRaises(ValueError):
                normalize_snapshot(payload,as_of=CLOCK,source_locator='fixture')
        raw=fixture()
        raw['companies'].append(copy.deepcopy(raw['companies'][0]))
        with self.assertRaises(ValueError):run(raw)
        raw=fixture()
        raw['schema']=True
        with self.assertRaises(ValueError):run(raw)

    def test_as_of_dates_and_cik_are_not_guessed(self):
        raw=fixture()
        raw['companies'][0]['cik']=1
        with self.assertRaises(ValueError):run(raw)
        result=run(fixture([dict(ROW,filed='2099-01-01')]))
        self.assertEqual(result['observations'][0]['structure'],'REJECTED')

    def test_real_snapshot_coverage_without_online_claim(self):
        payload=(BASE/'data/sec_facts.json').read_bytes().decode()
        with patch('urllib.request.urlopen',side_effect=AssertionError('Unexpected request')):
            result=normalize_snapshot(payload,as_of=CLOCK,source_locator='data/sec_facts.json')
        self.assertEqual(len(result['observations']),246)
        self.assertEqual(sum(o['structure']=='CANDIDATE' for o in result['observations']),242)
        self.assertEqual(sum(o['structure']=='OUT_OF_SCOPE' for o in result['observations']),4)
        self.assertEqual(result['source']['online_verification'],'NOT_PERFORMED')
        cameco=next(c for c in result['coverage'] if c['cik']=='0001009001')
        self.assertIn('diluted_average_shares_reported',cameco['missing_metrics'])
        for observation in result['observations']:
            self.assertFalse(observation['valuation_usable'])
            self.assertEqual(observation['accounting_validation'],'UNCHECKED')
        # Resolve every pointer back to exactly the retained source item.
        document=parse_raw(result['raw_document'])
        for observation in result['observations']:
            value=document
            for token in observation['raw_pointer'].split('/')[1:]:
                token=token.replace('~1','/').replace('~0','~')
                value=value[int(token)] if isinstance(value,list) else value[token]
            self.assertIsInstance(value,dict)

    def test_cli_preserves_snapshot(self):
        source=BASE/'data/sec_facts.json'
        before=source.read_bytes()
        cmd=[sys.executable,'-m','scripts.normalize_sec','--input','data/sec_facts.json','--as-of','2026-11-03']
        output=json.loads(subprocess.check_output(cmd,cwd=BASE,text=True))
        self.assertEqual(output['raw_document'].encode(),before)
        self.assertEqual(source.read_bytes(),before)
