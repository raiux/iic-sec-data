import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from enrich_vinci import normalize,canonical_period

def fact(concept,value,period,extra=None):
    d={'concept':concept,'entity':'scheme:VINCI','period':period,'unit':'iso4217:EUR'}
    if extra:d.update(extra)
    return {'value':value,'dimensions':d}

class Tests(unittest.TestCase):
    def test_period(self):
        self.assertEqual(canonical_period('2026-01-01T00:00:00'),('instant','2025-12-31'))
    def test_no_derived_pass(self):
        d={'facts':{'a':fact('ifrs-full:Assets',100,'2026-01-01T00:00:00'),'b':fact('ifrs-full:Equity',40,'2026-01-01T00:00:00')}}
        r=normalize(d);c=[x for x in r['checks'] if x['year']==2025]
        self.assertEqual(c[0]['status'],'NOT_TESTED');self.assertEqual(c[2]['value_eur'],'60')
    def test_conflict(self):
        p='2025-01-01T00:00:00/2026-01-01T00:00:00';d={'facts':{'a':fact('ifrs-full:ProfitLoss',10,p),'b':fact('ifrs-full:ProfitLoss',20,p)}}
        self.assertEqual(normalize(d)['metrics_by_year']['2025']['net_income_consolidated']['status'],'CONFLICT')
    def test_dimensions_excluded(self):
        p='2025-01-01T00:00:00/2026-01-01T00:00:00';d={'facts':{'a':fact('ifrs-full:ProfitLoss',10,p,{'segment':'X'})}}
        self.assertEqual(normalize(d)['metrics_by_year']['2025']['net_income_consolidated']['status'],'MISSING')
    def test_comparative(self):
        p='2024-01-01T00:00:00/2025-01-01T00:00:00';d={'facts':{'a':fact('ifrs-full:ProfitLoss',10,p)}}
        self.assertEqual(normalize(d)['metrics_by_year']['2024']['net_income_consolidated']['value'],'10')
if __name__=='__main__':unittest.main()
