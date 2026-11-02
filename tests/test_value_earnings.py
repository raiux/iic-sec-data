import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from value_earnings import evaluate


class TestVINCIIndicativeValue(unittest.TestCase):
    def setUp(self):
        self.a = json.loads((ROOT / "data" / "vinci_earnings_assumptions_2025.json").read_text())
        self.n = json.loads((ROOT / "data" / "vinci_enriched_2025.json").read_text())

    def test_published_values_reconciled(self):
        r = evaluate(self.a, self.n)
        self.assertEqual(r["status"], "CALCULATED_INDICATIVE")
        self.assertFalse(r["production_ready"])
        self.assertEqual(r["observed"]["diluted_eps_2025_eur"], "8.65")
        self.assertEqual(len(r["cross_checks"]), 2)
        self.assertTrue(all(x["status"] == "PASS" for x in r["cross_checks"]))

    def test_scenario_arithmetic(self):
        r = evaluate(self.a, self.n)
        self.assertEqual(r["range_eur_per_share"], {
            "low": "83.91", "central": "124.73", "high": "166.60"})
        self.assertLess(float(r["central_counter_scenario"]["value_eur_per_share"]), 124.73)

    def test_missing_esef_evidence_blocks(self):
        n = copy.deepcopy(self.n)
        n["metrics_by_year"]["2025"]["net_income_parent"]["status"] = "MISSING"
        self.assertEqual(evaluate(self.a, n)["status"], "BLOCKED")

    def test_inconsistent_reported_income_blocks(self):
        a = copy.deepcopy(self.a)
        a["reported_financials"]["2025"]["net_income_parent_eur"] = "5000000000"
        self.assertEqual(evaluate(a, self.n)["status"], "BLOCKED")

    def test_invented_scenario_order_blocks(self):
        a = copy.deepcopy(self.a)
        a["scenarios"]["bear"]["pe_multiple"] = "30"
        self.assertEqual(evaluate(a, self.n)["status"], "BLOCKED")

    def test_no_unverified_production_flag(self):
        r = evaluate(self.a, self.n)
        self.assertEqual(r["confidence"], "LOW")
        self.assertTrue(r["not_dcf"])


if __name__ == "__main__":
    unittest.main()
