import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from collect_esef_full import download
from normalize_financials import normalize

class PipelineTests(unittest.TestCase):
    def test_disallow_untrusted_host(self):
        with self.assertRaises(ValueError): download('https://example.com/file.json')

    def test_full_document_normalization(self):
        d={'source_url':'https://filings.xbrl.org/test.json','facts':{
            'fact1':{'value':'100','dimensions':{'concept':'ifrs-full:RevenueFromContractsWithCustomers','unit':'iso4217:EUR','entity':'scheme:TEST','period':'2025-01-01T00:00:00/2026-01-01T00:00:00'}},
            'fact2':{'value':'300','dimensions':{'concept':'ifrs-full:Assets','unit':'iso4217:EUR','entity':'scheme:TEST','period':'2026-01-01T00:00:00'}},
            'fact3':{'value':'200','dimensions':{'concept':'ifrs-full:Liabilities','unit':'iso4217:EUR','entity':'scheme:TEST','period':'2026-01-01T00:00:00'}},
            'fact4':{'value':'100','dimensions':{'concept':'ifrs-full:Equity','unit':'iso4217:EUR','entity':'scheme:TEST','period':'2026-01-01T00:00:00'}}}}
        result=normalize(d,2025,'TEST')
        self.assertEqual(result['input_coverage'],'full_xbrl_json')
        self.assertEqual(result['metrics']['revenue']['value'],'100')
        self.assertEqual(result['checks'][0]['status'],'PASS')

    def test_missing_is_not_estimated(self):
        d={'facts':{'x':{'value':'10','dimensions':{'concept':'ifrs-full:Assets','unit':'iso4217:EUR','entity':'x','period':'2026-01-01T00:00:00'}}}}
        result=normalize(d,2025,'TEST')
        self.assertEqual(result['metrics']['revenue']['status'],'MISSING')
        self.assertIsNone(result['metrics']['revenue']['value'])

if __name__=='__main__': unittest.main()
