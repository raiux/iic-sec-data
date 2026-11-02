import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from normalize_financials import normalize,period

def fact(fid,concept,value,period_raw='2025-01-01T00:00:00/2026-01-01T00:00:00',extra=None):
 d={'concept':concept,'entity':'scheme:LEI','period':period_raw,'unit':'iso4217:EUR'}
 if extra:d.update(extra)
 return {'fact_id':fid,'concept':concept,'value':value,'period':period_raw,'unit':'iso4217:EUR','entity':'scheme:LEI','dimensions':d}
class Tests(unittest.TestCase):
 def test_period(self):
  self.assertEqual(period('2026-01-01T00:00:00'),('instant','2025-12-31'))
 def test_revenue(self):
  x=normalize({'matching_facts':[fact('a','ifrs-full:RevenueFromContractsWithCustomers','75372000000')],'report_year':2025},2025,'VINCI')
  self.assertEqual(x['metrics']['revenue']['value'],'75372000000')
 def test_duplicates(self):
  x=normalize({'matching_facts':[fact('a','ifrs-full:ProfitLoss','10'),fact('b','ifrs-full:ProfitLoss','10')]},2025)
  self.assertEqual(x['metrics']['net_income_consolidated']['candidate_count'],2)
  self.assertEqual(x['metrics']['net_income_consolidated']['status'],'CANDIDATE')
 def test_conflict(self):
  x=normalize({'matching_facts':[fact('a','ifrs-full:ProfitLoss','10'),fact('b','ifrs-full:ProfitLoss','11')]},2025)
  self.assertEqual(x['metrics']['net_income_consolidated']['status'],'CONFLICT')
 def test_dimension_exclusion(self):
  x=normalize({'matching_facts':[fact('a','ifrs-full:ProfitLoss','10',extra={'segment':'segmentA'})]},2025)
  self.assertEqual(x['metrics']['net_income_consolidated']['status'],'MISSING')
 def test_instant(self):
  x=normalize({'matching_facts':[fact('a','ifrs-full:Assets','133065000000','2026-01-01T00:00:00')]},2025)
  self.assertEqual(x['metrics']['total_assets']['status'],'CANDIDATE')
 def test_full_json(self):
  f=fact('a','ifrs-full:ProfitLoss','10')
  x=normalize({'facts':{'a':{'value':'10','dimensions':f['dimensions']}}},2025)
  self.assertEqual(x['input_coverage'],'full_xbrl_json')
if __name__=='__main__':unittest.main()
