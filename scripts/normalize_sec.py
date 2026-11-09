"""C1 SEC snapshot normalization. Raw text is immutable; values remain unapproved."""
import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

# Offline ISO 4217 reference and exclusions are documented in MISSION_03_C1.md.
REPORTING_CURRENCIES = frozenset("""
    AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BHD BIF BMD BND
    BOB BRL BSD BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE
    CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD
    GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY
    KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD
    MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO
    NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF
    SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB
    TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VED VES VND
    VUV WST XAF XCD XCG XOF XPF YER ZAR ZMW ZWG
""".split())

# Granular reported metrics deliberately keep debt and share definitions separate.
MAPPING = {
    'ifrs-full': {
        'Revenue': ('revenue_reported', 'DURATION'),
        'RevenueFromContractsWithCustomers': ('revenue_reported', 'DURATION'),
        'ProfitLoss': ('net_income_reported', 'DURATION'),
        'CashFlowsFromUsedInOperatingActivities': ('operating_cash_flow_reported', 'DURATION'),
        'PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities': ('ppe_purchases_reported', 'DURATION'),
        'CashAndCashEquivalents': ('cash_and_equivalents_reported', 'INSTANT'),
        'Assets': ('assets_reported', 'INSTANT'),
        'Borrowings': ('borrowings_reported', 'INSTANT'),
        'LongtermBorrowings': ('long_term_borrowings_reported', 'INSTANT'),
        'WeightedAverageSharesDiluted': ('diluted_average_shares_reported', 'DURATION'),
        'WeightedAverageNumberOfDilutedSharesOutstanding': ('diluted_average_shares_reported', 'DURATION'),
    },
    'us-gaap': {
        'RevenueFromContractWithCustomerExcludingAssessedTax': ('revenue_reported', 'DURATION'),
        'Revenues': ('revenue_reported', 'DURATION'),
        'NetIncomeLoss': ('net_income_reported', 'DURATION'),
        'NetCashProvidedByUsedInOperatingActivities': ('operating_cash_flow_reported', 'DURATION'),
        'PaymentsToAcquirePropertyPlantAndEquipment': ('ppe_purchases_reported', 'DURATION'),
        'CashAndCashEquivalentsAtCarryingValue': ('cash_and_equivalents_reported', 'INSTANT'),
        'Assets': ('assets_reported', 'INSTANT'),
        'LongTermDebt': ('long_term_debt_reported', 'INSTANT'),
        'LongTermDebtNoncurrent': ('noncurrent_long_term_debt_reported', 'INSTANT'),
        'WeightedAverageNumberOfDilutedSharesOutstanding': ('diluted_average_shares_reported', 'DURATION'),
    },
    'dei': {'EntityCommonStockSharesOutstanding': ('outstanding_shares_reported', 'INSTANT')},
}
ANNUAL_FORMS = {'10-K', '10-K/A', '20-F', '20-F/A', '40-F', '40-F/A'}
CORE_METRICS = ('revenue_reported', 'net_income_reported', 'operating_cash_flow_reported',
                'ppe_purchases_reported', 'cash_and_equivalents_reported',
                'assets_reported', 'diluted_average_shares_reported')


def parse_raw(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('NON_JSON_NUMBER')
    return json.loads(payload, parse_float=Decimal, object_pairs_hook=pairs,
                      parse_constant=invalid_constant)


def pointer_part(value):
    return value.replace('~', '~0').replace('/', '~1')


def qualify(row, unit, metric, nature, as_of):
    """Annual candidates only, without guessing fiscal periods from fy/fp/frame."""
    if not isinstance(row, dict):
        return 'REJECTED', ['INVALID_ROW']
    value = row.get('val')
    if type(value) not in (int, Decimal) or (isinstance(value, Decimal) and not value.is_finite()):
        return 'REJECTED', ['INVALID_VALUE']
    try:
        end = date.fromisoformat(row['end'])
        filed = date.fromisoformat(row['filed'])
        start = date.fromisoformat(row['start']) if 'start' in row else None
    except (KeyError, TypeError, ValueError):
        return 'REJECTED', ['INVALID_DATES']
    if end > filed or filed > as_of:
        return 'REJECTED', ['INCONSISTENT_DATES']
    if not re.fullmatch(r'[0-9]{10}-[0-9]{2}-[0-9]{6}', str(row.get('accn', ''))):
        return 'REJECTED', ['ACCESSION_MISSING']
    reasons = []
    if row.get('form') not in ANNUAL_FORMS:
        reasons.append('FORM_OUT_OF_SCOPE')
    if row.get('dimensions') or row.get('segment'):
        reasons.append('DIMENSIONS_OUT_OF_SCOPE')
    share_metric = metric in {'diluted_average_shares_reported', 'outstanding_shares_reported'}
    unit_allowed = unit == 'shares' if share_metric else unit in REPORTING_CURRENCIES
    if not unit_allowed:
        reasons.append('UNIT_OUT_OF_SCOPE')
    if nature == 'INSTANT':
        if start is not None:
            reasons.append('INSTANT_HAS_START')
    elif start is None or not 300 <= (end - start).days <= 380:
        reasons.append('NON_ANNUAL_DURATION')
    return ('OUT_OF_SCOPE', reasons) if reasons else ('CANDIDATE', [])


def normalize_snapshot(payload, *, as_of, source_locator):
    """Source is a repository/cache snapshot, never evidence of a fresh SEC fetch."""
    if type(as_of) is not date or type(source_locator) is not str or not source_locator:
        raise ValueError('Explicit date and source locator required')
    raw = parse_raw(payload)
    if (not isinstance(raw, dict) or type(raw.get('schema')) is not int or raw.get('schema') != 1
            or not isinstance(raw.get('companies'), list)):
        raise ValueError('Expected SEC cache schema 1')
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    result = dict(schema_version='iic.sec-candidates.v1', visibility='PRIVATE',
                  as_of=as_of.isoformat(), mapping_version='sec-reported-1',
                  source=dict(kind='SNAPSHOT', locator=source_locator, sha256=digest,
                              online_verification='NOT_PERFORMED'),
                  raw_document=payload, observations=[], groups=[], coverage=[],
                  accounting_validation='UNCHECKED', valuation_usable=False,
                  limitations=['SOURCE_NOT_RECONCILED_WITH_OFFICIAL_FILINGS',
                               'REPORTED_METRICS_NOT_SECTOR_VALUATION_INPUTS'])
    groups = defaultdict(list)
    issuer_metrics = defaultdict(set)
    seen_ciks = set()
    for company_index, company in enumerate(raw['companies']):
        if not isinstance(company, dict):
            raise ValueError('Invalid company container')
        cik = company.get('cik')
        if type(cik) is not str or not re.fullmatch(r'[0-9]{10}', cik) or cik in seen_ciks:
            raise ValueError('Invalid or duplicate CIK; snapshot not rewritten')
        seen_ciks.add(cik)
        issuer_metrics[cik]  # Keep zero-coverage issuers visible.
        facts = company.get('facts')
        if not isinstance(facts, dict):
            raise ValueError('Invalid facts container; snapshot not rewritten')
        for namespace, concepts in facts.items():
            if not isinstance(concepts, dict):
                raise ValueError('Invalid namespace container')
            for concept, detail in concepts.items():
                if not isinstance(detail, dict) or not isinstance(detail.get('units'), dict):
                    raise ValueError('Invalid concept container')
                mapping = MAPPING.get(namespace, {}).get(concept)
                for unit, rows in detail['units'].items():
                    if not isinstance(rows, list):
                        raise ValueError('Invalid rows container')
                    for row_index, row in enumerate(rows):
                        pointer = (f'/companies/{company_index}/facts/{pointer_part(namespace)}/'
                                   f'{pointer_part(concept)}/units/{pointer_part(unit)}/{row_index}')
                        observation_id = hashlib.sha256((digest + pointer).encode()).hexdigest()
                        status, reasons = ('UNMAPPED', ['CONCEPT_NOT_MAPPED'])
                        if mapping:
                            status, reasons = qualify(row, unit, *mapping, as_of)
                        item = dict(id=observation_id, cik=cik, namespace=namespace, concept=concept,
                                    raw_pointer=pointer, source_sha256=digest,
                                    structure=status, reasons=reasons, normalized=None,
                                    accounting_validation='UNCHECKED', valuation_usable=False)
                        if status == 'CANDIDATE':
                            metric, nature = mapping
                            item['normalized'] = dict(
                                metric=metric, value=str(row['val']), unit=unit,
                                currency=None if unit == 'shares' else unit,
                                nature=nature, start=row.get('start'), end=row['end'],
                                reported_fy=row.get('fy'), reported_fp=row.get('fp'),
                                accession=row['accn'], filed=row['filed'], form=row['form'],
                                scope='REQUIRES_TAXONOMY_AND_FILING_REVIEW',
                                transformations=['EXACT_DECIMAL_TEXT_ONLY'])
                            key = (cik, metric, nature, row.get('start'), row['end'], unit)
                            groups[key].append(item)
                            issuer_metrics[cik].add(metric)
                        result['observations'].append(item)
    for key, members in sorted(groups.items(), key=lambda pair: repr(pair[0])):
        amounts = {Decimal(member['normalized']['value']) for member in members}
        group_status = 'SINGLE_CANDIDATE'
        if len(amounts) > 1:
            group_status = 'CONFLICT'
        elif len(members) > 1:
            group_status = 'REPEATED_VALUE_CANDIDATES'
        result['groups'].append(dict(
            cik=key[0], metric=key[1], nature=key[2], start=key[3], end=key[4], unit=key[5],
            status=group_status, observation_ids=[member['id'] for member in members],
            amendment_candidates=[member['id'] for member in members
                                  if member['normalized']['form'].endswith('/A')],
            selected_observation_id=None, valuation_usable=False))
    for cik, present in sorted(issuer_metrics.items()):
        result['coverage'].append(dict(cik=cik, candidate_metrics=sorted(present),
            missing_metrics=sorted(set(CORE_METRICS) - present),
            metric_coverage_scope='ALL_RETAINED_PERIODS_NOT_LATEST_FISCAL_YEAR',
            accounting_validation='UNCHECKED', valuation_usable=False))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--as-of', required=True, help='ISO date for deterministic admission checks')
    args = parser.parse_args()
    try:
        path = Path(args.input)
        payload = path.read_bytes().decode('utf-8')
        output = normalize_snapshot(payload, as_of=date.fromisoformat(args.as_of),
                                    source_locator=args.input)
    except (ValueError, OSError):
        parser.error('Invalid snapshot/date; original input preserved')
    print(json.dumps(output, sort_keys=True, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
