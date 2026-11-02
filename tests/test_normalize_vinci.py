import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from normalize_vinci import normalize, parse_period

class TestNormalize(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(parse_period('2025-01-01T00:00:00/2026-01-01T00:00:00')['end'], '2025-12-31')
    def test_instant(self):
        self.assertEqual(parse_period('2026-01-01T00:00:00')['as_of'], '2025-12-31')
    def test_missing_not_invented(self):
        r = normalize({'company':'VINCI','lei':'213800WFQ334R8UXUG83','report_year':2025,'matching_facts':[]})
        self.assertTrue(all(v['status']=='MISSING' and v['value'] is None for v in r['metrics'].values()))
    def test_conflict(self):
        f={'concept':'ifrs-full:Assets','unit':'iso4217:EUR','period':'2026-01-01T00:00:00','entity':'scheme:213800WFQ334R8UXUG83'}
        a=dict(f, fact_id='a', value='10', dimensions=dict(f))
        b=dict(f, fact_id='b', value='20', dimensions=dict(f))
        r=normalize({'company':'VINCI','lei':'213800WFQ334R8UXUG83','report_year':2025,'matching_facts':[a,b]})
        self.assertEqual(r['metrics']['total_assets']['status'],'CONFLICT')
        self.assertIsNone(r['metrics']['total_assets']['value'])

if __name__ == '__main__': unittest.main()
