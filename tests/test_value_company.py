import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from value_company import compute_scenario,value_payload
from prepare_vinci import build

class TestDCF(unittest.TestCase):
    def setUp(self):
        self.s={'base_fcff_eur':100,'net_debt_eur':50,'minority_interests_eur':10,'nonoperating_assets_eur':5,'diluted_shares':10,'wacc':0.1,'terminal_growth':0.02,'growth_rates':[0,0]}
    def test_exact_dcf(self):
        r=compute_scenario(self.s);self.assertEqual(r['status'],'CALCULATED');self.assertAlmostEqual(float(r['fair_value_per_share_eur']),117.22727272727273,places=4)
    def test_missing_blocks(self):
        s=dict(self.s,net_debt_eur=None);self.assertEqual(compute_scenario(s)['status'],'BLOCKED')
    def test_bad_spread(self):
        s=dict(self.s,terminal_growth=.1);self.assertEqual(compute_scenario(s)['status'],'INVALID')
    def test_no_shares(self):
        s=dict(self.s,diluted_shares=0);self.assertEqual(compute_scenario(s)['status'],'INVALID')
    def test_scenarios(self):
        p={'company':'TEST','input_mode':'synthetic_demo','scenarios':{k:self.s for k in ('bear','base','bull')}}
        r=value_payload(p);self.assertEqual(r['status'],'CALCULATED');self.assertFalse(r['production_ready'])
    def test_real_missing(self):
        p=build({'metrics_by_year':{'2025':{'revenue':{'value':'75372000000','status':'CANDIDATE'}}}})
        r=value_payload(p);self.assertEqual(r['status'],'INCOMPLETE');self.assertFalse(r['production_ready'])
if __name__=='__main__':unittest.main()
