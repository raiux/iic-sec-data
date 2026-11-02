#!/usr/bin/env python3
"""Indicative earnings-multiple valuation using reported VINCI EPS.

This is NOT a replacement for an unlevered FCFF DCF or a concessions SOTP.
Financial observations must reconcile with the existing ESEF extraction.
Multiples and next-year EPS growth are transparent, uncalibrated assumptions.
"""
import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

SCENARIOS = ("bear", "base", "bull")
CENT = Decimal("0.01")


def number(value, label):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label}: finite number required")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid number") from exc
    if not result.is_finite():
        raise ValueError(f"{label}: non-finite number")
    return result


def money(value):
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def check_sources(assumptions, normalized):
    errors = []
    checks = []
    if normalized.get("company") != assumptions.get("company"):
        errors.append("Company mismatch between assumptions and normalized XBRL")
    if normalized.get("input_coverage") != "full_xbrl_json":
        errors.append("Full XBRL coverage required")
    if assumptions.get("company") != "VINCI":
        errors.append("This pilot requires VINCI")
    if assumptions.get("fiscal_year") != 2025 or normalized.get("fiscal_year") != 2025:
        errors.append("Fiscal year must be 2025 for both sources")
    source_url = assumptions.get("published_source_url")
    if not isinstance(source_url, str) or not source_url.startswith("https://www.vinci.com/"):
        errors.append("Official VINCI source URL required")
    financials = assumptions.get("reported_financials", {})
    years = normalized.get("metrics_by_year", {})
    for year in ("2024", "2025"):
        fact = years.get(year, {}).get("net_income_parent", {})
        if fact.get("status") != "CANDIDATE" or not fact.get("source_fact_ids"):
            errors.append(f"{year}: ESEF parent net income unavailable/conflicting")
            continue
        try:
            observed = number(fact.get("value"), f"ESEF {year} parent earnings")
            reported = number(financials.get(year, {}).get("net_income_parent_eur"),
                              f"VINCI official {year} parent earnings")
            difference = abs(observed - reported)
            if difference > Decimal("1000000"):
                errors.append(f"{year}: earnings mismatch exceeds EUR 1m")
            else:
                checks.append({"year": int(year), "status": "PASS",
                               "esef_fact_ids": fact["source_fact_ids"],
                               "difference_eur": str(difference)})
        except (ValueError, AttributeError) as exc:
            errors.append(str(exc))
    checks_2025 = [x for x in normalized.get("checks", [])
                   if x.get("year") == 2025 and x.get("name") == "profit_attribution"]
    if not checks_2025 or checks_2025[0].get("status") != "PASS":
        errors.append("2025 profit-attribution check is not PASS")
    return errors, checks


def evaluate(assumptions, normalized):
    errors, cross_checks = check_sources(assumptions, normalized)
    result = {
        "schema": 1,
        "company": "VINCI",
        "model": "FORWARD_DILUTED_EPS_TIMES_PE",
        "method_class": "INDICATIVE_RELATIVE_VALUATION",
        "not_dcf": True,
        "fiscal_year": 2025,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"official_url": assumptions.get("published_source_url"),
                   "esef_url": normalized.get("source_document")},
        "cross_checks": cross_checks,
        "status": "BLOCKED",
        "production_ready": False,
        "confidence": "LOW",
        "warnings": [
            "Illustrative P/E multiples, not independently calibrated from peers or market data.",
            "2025 annual EPS is not a current 2026 earnings forecast.",
            "VINCI concessions need separate concession-life / SOTP valuation.",
            "Official free cash flow is not unlevered FCFF; it is not used in this model.",
            "Net financial debt is informational here; do not subtract it from equity earnings valuation.",
            "No live share price: no margin-of-safety or buy/sell signal.",
        ],
    }
    if errors:
        result["blocking_reasons"] = errors
        return result
    try:
        f = assumptions["reported_financials"]
        eps = number(f["2025"]["diluted_eps_eur"], "2025 diluted EPS")
        prior_eps = number(f["2024"]["diluted_eps_eur"], "2024 diluted EPS")
        if eps <= 0 or prior_eps <= 0:
            raise ValueError("Reported diluted EPS must be positive")
        result["observed"] = {
            "diluted_eps_2025_eur": str(eps),
            "diluted_eps_2024_eur": str(prior_eps),
            "observed_eps_growth_pct": str(((eps/prior_eps)-1)*100),
            "net_income_parent_2025_eur": str(number(f["2025"]["net_income_parent_eur"], "net income")),
            "reported_free_cash_flow_2025_eur": str(number(f["2025"]["free_cash_flow_eur"], "FCF")),
            "reported_net_financial_debt_2025_eur": str(number(f["2025"]["net_financial_debt_eur"], "net debt")),
        }
        scenarios = {}
        for label in SCENARIOS:
            cfg = assumptions["scenarios"][label]
            growth = number(cfg["next_year_eps_growth"], f"{label} EPS growth")
            multiple = number(cfg["pe_multiple"], f"{label} P/E")
            if not Decimal("-1") < growth <= Decimal("1"):
                raise ValueError(f"{label}: EPS growth outside (-100%, +100%]")
            if not Decimal("0") < multiple <= Decimal("100"):
                raise ValueError(f"{label}: P/E outside (0, 100]")
            forward_eps = eps * (1 + growth)
            scenarios[label] = {
                "status": "CALCULATED",
                "assumption_origin": "ILLUSTRATIVE_NOT_MARKET_CALIBRATED",
                "next_year_eps_growth": str(growth),
                "pe_multiple": str(multiple),
                "forward_eps_eur": money(forward_eps),
                "indicative_value_eur_per_share": money(forward_eps * multiple),
            }
        values = [number(scenarios[x]["indicative_value_eur_per_share"], x) for x in SCENARIOS]
        if values != sorted(values):
            raise ValueError("Scenario values not ordered bear <= base <= bull")
        stress = assumptions["central_counter_scenario"]
        shock = number(stress["eps_shock"], "stress EPS shock")
        mult_delta = number(stress["pe_multiple_delta"], "stress P/E change")
        base = assumptions["scenarios"]["base"]
        central_growth = number(base["next_year_eps_growth"], "base EPS growth")
        central_pe = number(base["pe_multiple"], "base P/E")
        if not Decimal("-1") < shock <= 0 or central_pe + mult_delta <= 0:
            raise ValueError("Invalid central contradiction stress assumptions")
        stressed_value = eps * (1 + central_growth) * (1 + shock) * (central_pe + mult_delta)
        result.update({
            "status": "CALCULATED_INDICATIVE",
            "scenarios": scenarios,
            "range_eur_per_share": {"low": money(values[0]), "central": money(values[1]),
                                    "high": money(values[2])},
            "central_counter_scenario": {
                "eps_shock": str(shock),
                "pe_multiple_delta": str(mult_delta),
                "value_eur_per_share": money(stressed_value),
                "delta_vs_central_pct": str(((stressed_value/values[1])-1)*100),
            },
            "blocking_reasons_for_final_dcf": [
                "Validated unlevered FCFF and concession maintenance/growth investments",
                "Consistent net debt including financial/lease treatment and minority interests",
                "Validated diluted shares outstanding (not an EPS-derived approximation)",
                "Documented WACC, terminal growth and segment-specific projections",
                "SOTP / concession finite-life adjustment",
            ],
        })
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        result["status"] = "BLOCKED"
        result["blocking_reasons"] = [str(exc)]
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--assumptions", required=True)
    p.add_argument("--enriched", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    assumptions = json.loads(Path(args.assumptions).read_text(encoding="utf-8"))
    normalized = json.loads(Path(args.enriched).read_text(encoding="utf-8"))
    result = evaluate(assumptions, normalized)
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("VINCI earnings valuation:", result["status"], "->", dest)
    if result["status"] != "CALCULATED_INDICATIVE":
        raise SystemExit("Source reconciliation or scenario validation failed")


if __name__ == "__main__":
    main()
