#!/usr/bin/env python3
"""Strict VINCI ESEF prototype: source data/vinci_xbrl_test.json; no valuation."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data' / 'vinci_xbrl_test.json'
TARGET = ROOT / 'data' / 'vinci_normalized.json'

# Exact concepts only: never guess a financial metric from keyword matches.
CONCEPTS = {
    'revenue': 'ifrs-full:Revenue',
    'net_income_consolidated': 'ifrs-full:ProfitLoss',
    'net_income_parent': 'ifrs-full:ProfitLossAttributableToOwnersOfParent',
    'operating_profit': 'ifrs-full:ProfitLossFromOperatingActivities',
    'operating_cash_flow': 'ifrs-full:CashFlowsFromUsedInOperatingActivities',
    'cash_and_equivalents': 'ifrs-full:CashAndCashEquivalents',
    'total_assets': 'ifrs-full:Assets',
}
INSTANT = {'cash_and_equivalents', 'total_assets'}

def parse_period(raw):
    if not isinstance(raw, str):
        return None
    try:
        if '/' in raw:
            a, b = raw.split('/', 1)
            start = date.fromisoformat(a[:10])
            end_exclusive = date.fromisoformat(b[:10])
            if end_exclusive <= start:
                return None
            return {'kind': 'duration', 'start': start.isoformat(), 'end': (end_exclusive - timedelta(days=1)).isoformat(), 'days': (end_exclusive - start).days}
        instant = date.fromisoformat(raw[:10])
        return {'kind': 'instant', 'as_of': (instant - timedelta(days=1)).isoformat()}
    except (ValueError, TypeError):
        return None

def normalize(data):
    if data.get('company') != 'VINCI' or data.get('report_year') != 2025:
        raise ValueError('Unexpected company or reporting year')
    if data.get('lei') != '213800WFQ334R8UXUG83':
        raise ValueError('Unexpected LEI')
    facts = data.get('matching_facts')
    if not isinstance(facts, list):
        raise ValueError('Missing matching_facts array')
    output = {'schema': 1, 'company': 'VINCI', 'lei': data['lei'], 'fiscal_year': 2025,
              'source_document': data.get('source_url'), 'source_file': 'data/vinci_xbrl_test.json',
              'generated_at': datetime.now(timezone.utc).isoformat(), 'metrics': {}}
    for metric, concept in CONCEPTS.items():
        matches = []
        for f in facts:
            if f.get('concept') != concept or f.get('unit') != 'iso4217:EUR':
                continue
            dims = f.get('dimensions')
            if not isinstance(dims, dict) or set(dims) - {'concept', 'entity', 'period', 'unit', 'language'}:
                continue  # dimensional facts are not silently treated as consolidated totals
            if dims.get('entity') != f.get('entity') or dims.get('period') != f.get('period'):
                continue
            p = parse_period(f.get('period'))
            if not p:
                continue
            if metric in INSTANT:
                if p != {'kind': 'instant', 'as_of': '2025-12-31'}:
                    continue
            elif p.get('kind') != 'duration' or p.get('start') != '2025-01-01' or p.get('end') != '2025-12-31':
                continue
            try:
                v = Decimal(str(f['value']))
                if not v.is_finite():
                    continue
            except (InvalidOperation, KeyError, TypeError):
                continue
            matches.append({'value': v, 'fact_id': f.get('fact_id'), 'period': p, 'decimals': f.get('decimals')})
        values = {m['value'] for m in matches}
        if not matches:
            status, value = 'MISSING', None
        elif len(values) > 1:
            status, value = 'CONFLICT', None
        else:
            status, value = 'CANDIDATE', str(next(iter(values)))
        output['metrics'][metric] = {'status': status, 'value': value, 'currency': 'EUR',
                                     'concept': concept, 'source_fact_ids': [m['fact_id'] for m in matches],
                                     'candidate_count': len(matches), 'reason': 'Exact concept, period, currency and no extra dimensions; still requires accounting review' if status == 'CANDIDATE' else ('Conflicting facts' if status == 'CONFLICT' else 'No exact eligible fact')}
    return output

def main():
    with SOURCE.open(encoding='utf-8') as f:
        source = json.load(f)
    result = normalize(source)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    temp = TARGET.with_suffix('.json.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write('\n')
    temp.replace(TARGET)
    for name, row in result['metrics'].items():
        print(f"{name}: {row['status']} {row['value'] or ''} (facts={row['candidate_count']})")

if __name__ == '__main__':
    main()
