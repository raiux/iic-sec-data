# VINCI 2025 — first indicative valuation (NOT DCF)

This pilot calculates a **three-scenario next-year diluted EPS × P/E range** from VINCI's published 2025 EPS of **EUR 8.65**. The 2024 reported diluted EPS is EUR 8.43. The published parent net profit of EUR 4,903m (2025) and EUR 4,863m (2024) is independently reconciled against XBRL fact IDs in `data/vinci_enriched_2025.json`.

- Official annual consolidated statements: https://www.vinci.com/en/finance/results-and-key-figures/2025-annual-financial-statements
- Official release with free cash flow and net financial debt: https://www.vinci.com/en/newsroom/press-releases/outstanding-performance-record-free-cash-flow

**Explicit illustrative assumptions (not researched peer multiples):** bear -3% EPS growth × 10 P/E, central +3% × 14, optimistic +7% × 18; counter-scenario -15% EPS shock and P/E contraction by 2. The resulting figures are demonstrations of a financial valuation pipeline, **not an investment decision, investment advice, a DCF, a verified price target or an intrinsic-value claim**. Input sources are preserved in the output JSON.

The company publishes EUR 7,010m of 2025 free cash flow and EUR 19.1bn consolidated net financial debt (rounded). These are **informational only**: cash flow is not transformed into unlevered FCFF and debt is not subtracted from an equity-earnings valuation.

**Important accounting discrepancy to investigate:** VINCI's consolidated 2025 statement shows total equity **EUR 34,428m**, while the current ESEF enrichment maps equity as **EUR 34,328m**. This pilot deliberately does not mark the valuation production ready, and does not infer independent liabilities from assets minus equity. Resolve this before trusting balance-sheet-dependent valuations.

Run:
```bash
python -m unittest discover -s tests -p 'test_value_earnings.py' -v
python scripts/value_earnings.py --assumptions data/vinci_earnings_assumptions_2025.json --enriched data/vinci_enriched_2025.json --output out/vinci_earnings_indicative_2025.json
```

Workflow: `.github/workflows/value-earnings.yml`. Output: `out/vinci_earnings_indicative_2025.json`. Continue the original FCFF DCF and concessional SOTP separately, with validated base cash flows and WACC.
