# AGENTS.md — Investment Intelligence Center (IIC) V5

## Mission
Build a free, reproducible, auditable fundamental-analysis pipeline for public companies. Preserve the existing V4 Google Sheets / Apps Script / HTML experience until a replacement is tested. The repository currently contains only the Python/GitHub Actions financial-data and valuation parts, not the entire V4 application.

## Architecture and source of truth
- Python on GitHub Actions is the **only financial calculation engine** (collection, normalization, checks, ratios, valuations, scenarios).
- Google Sheets and Apps Script manage company selection, parameters and synchronization; HTML displays validated JSON. Gemini performs sourced qualitative analysis and counterarguments, never creates accounting facts or computes the authoritative fair value.
- Project requirements are recorded in the private Google Drive IIC V5 specification, Annex A (preserved features) and Annex B (prompts). They are **not automatically accessible to Codex**; ask the coordinator for missing requirements instead of guessing. A separately saved "V5 SIMPLE" prototype is not permission to remove V4 capabilities.
- The code, tests, published official financial statements and actual CI results determine what is implemented. Do not equate a requirement, a past test, or a demo with production readiness.
- Current pilots: Cameco (SEC IFRS), Microsoft (SEC US-GAAP), VINCI (ESEF IFRS). Broader international coverage, including Siemens, is future work.

## Non-negotiable safety and finance rules
- Never fabricate or silently substitute financial observations, FX rates, ISINs, prices, market multiples, forecasts, or source URLs.
- Every extracted observation should retain provenance: entity, concept, source document/fact ID, filing date, period start/end and type, currency, unit, dimensions and validation status. Record source conflicts, revisions, amendments and missing inputs explicitly.
- Never present CFO minus capex as unlevered FCFF without documented necessary adjustments. For FCFF DCF use consistent EBIT/tax/D&A/capex/working-capital definitions and net debt/minority/lease treatment. Do not subtract net debt from a P/E-derived equity valuation.
- Every valuation must label the method, input verification, assumptions, scenario range, counter-scenario, limitations and confidence. Missing mandatory inputs => BLOCKED/INCOMPLETE; calculations alone do not establish `production_ready`.
- For SEC EDGAR: descriptive contact User-Agent, at least 1 second between requests, bounded retry/backoff, respect 403/429, use a freshness-aware cache, and retain the last valid company data after transient errors. Tests must mock external requests.
- Keep the public repository free of personal portfolio holdings, exact positions, private financial information, credentials and API keys. Do not put private Google Drive document links or contents in public source. Store secrets in GitHub Actions secrets/variables as appropriate.
- No paid API or subscription dependency; no automatic trades. Do not reintroduce the abandoned personal portfolio vs S&P 500 comparison.

## Working agreement
1. Inspect relevant code, data schemas, docs, tests and workflows before edits. Make one small scoped change per branch and pull request; never push directly to `main` or auto-merge.
2. Preserve existing behavior unless the PR describes the necessity, tests and migration/rollback plan. Do not delete existing Sheets/HTML artifacts or simplify functionality without explicit approval.
3. Add or update deterministic regression tests (including missing data, conflicting facts, periods/units, invalid assumptions, retry/cache behavior). Never make CI dependent on live SEC/Gemini calls.
4. Run the relevant tests and report exact commands and results. Baseline command:
   `python -m unittest discover -s tests -p 'test_*.py' -v`
   Reproduce financial-pilot scripts with documented input files where feasible.
5. In PR descriptions report: intent, files changed, verified evidence, tests, unverified claims, known risks, and next step. Respond to the coordinator in French.
6. For the first task read `docs/CODEX_FIRST_MISSION.md` and execute its discovery/audit phase before implementing fixes.

## UX and later integration guardrails
Preserve the V4 dark professional layout and Simple/Expert modes; Annex A targets four final sections (Dashboard, Portefeuille, Analyse, Radar). Keep sourced company details, euro display with original currency/FX timestamp, scenarios, risk/catalyst analysis, quality/coverage, and independent review without false equivalence of two Gemini roles. UI changes are separate, explicitly scoped PRs after the data pipeline is verified.
