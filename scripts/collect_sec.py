#!/usr/bin/env python3
"""Collect compact SEC XBRL facts for IIC; preserve verified per-company cache timestamps."""
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
CONFIG = BASE / 'iic_companies.json'
OUTPUT = BASE / 'data' / 'sec_facts.json'
UA = os.environ.get('SEC_USER_AGENT', '').strip()
INTERVAL = max(1.0, float(os.environ.get('IIC_SEC_INTERVAL_SECONDS', '1')))
CACHE_AGE = timedelta(hours=24)
FORMS = {'10-K', '10-K/A', '20-F', '20-F/A', '40-F', '40-F/A'}
TAGS = {
    'ifrs-full': {
        'revenue': ['Revenue', 'RevenueFromContractsWithCustomers'],
        'netIncome': ['ProfitLoss'],
        'operatingCashFlow': ['CashFlowsFromUsedInOperatingActivities'],
        'capex': ['PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'],
        'cash': ['CashAndCashEquivalents'],
        'totalAssets': ['Assets'],
        'borrowings': ['Borrowings', 'LongtermBorrowings'],
        'dilutedShares': ['WeightedAverageSharesDiluted', 'WeightedAverageNumberOfDilutedSharesOutstanding'],
    },
    'us-gaap': {
        'revenue': ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues'],
        'netIncome': ['NetIncomeLoss'],
        'operatingCashFlow': ['NetCashProvidedByUsedInOperatingActivities'],
        'capex': ['PaymentsToAcquirePropertyPlantAndEquipment'],
        'cash': ['CashAndCashEquivalentsAtCarryingValue'],
        'totalAssets': ['Assets'],
        'borrowings': ['LongTermDebt', 'LongTermDebtNoncurrent'],
        'dilutedShares': ['WeightedAverageNumberOfDilutedSharesOutstanding'],
    },
    'dei': {'outstandingShares': ['EntityCommonStockSharesOutstanding']},
}
LAST_REQUEST = None

class SecBlocked(Exception):
    pass


def now():
    return datetime.now(timezone.utc)


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def request_json(url):
    global LAST_REQUEST
    if '@' not in UA or len(UA) < 12:
        raise ValueError('Set SEC_USER_AGENT to a descriptive value containing a contact email')
    request = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    for attempt in range(3):
        if LAST_REQUEST is not None:
            time.sleep(max(0, INTERVAL - (time.monotonic() - LAST_REQUEST)))
        LAST_REQUEST = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 403:
                raise SecBlocked('HTTP 403: stop SEC requests') from error
            if error.code == 429:
                if attempt == 2:
                    raise SecBlocked('HTTP 429 persists: stop SEC requests') from error
                retry = error.headers.get('Retry-After')
                seconds = 0
                if retry:
                    try:
                        seconds = max(0, float(retry))
                    except ValueError:
                        try:
                            parsed = parsedate_to_datetime(retry)
                            seconds = max(0, (parsed - now()).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            pass
                if not math.isfinite(seconds) or seconds > 120:
                    raise SecBlocked('HTTP 429: Retry-After exceeds retry budget; stop SEC requests') from error
                delay = max(30 * (2 ** attempt), seconds)
                print(f'HTTP 429: waiting {delay:.0f}s', flush=True)
                time.sleep(delay)
            elif error.code in (500, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt)
            else:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def compact_facts(document, cik, *, downloaded=True):
    """Validate transport/identity and retain sourced candidates, NOT accounting approval.

    Keep revisions and conflicting values separately. No concept aggregation or FX.
    SEC companyfacts supplies entity-wide facts; reject explicit dimensions if present.
    """
    if not isinstance(document, dict) or not isinstance(document.get('facts'), dict):
        raise ValueError('Invalid companyfacts object')
    if downloaded and str(document.get('cik', '')).zfill(10) != cik:
        raise ValueError('Companyfacts CIK mismatch')
    result = {}
    for namespace, metrics in TAGS.items():
        source = document['facts'].get(namespace, {})
        if not isinstance(source, dict):
            raise ValueError('Invalid namespace')
        for metric, tags in metrics.items():
            for tag in tags:
                if tag not in source:
                    continue
                concept = source[tag]
                if not isinstance(concept, dict) or not isinstance(concept.get('units'), dict):
                    raise ValueError('Invalid concept units')
                units = {}
                for unit, rows in concept['units'].items():
                    if not isinstance(rows, list):
                        raise ValueError('Invalid facts list')
                    if (metric in {'dilutedShares', 'outstandingShares'} and unit != 'shares') or (
                        metric not in {'dilutedShares', 'outstandingShares'} and not re.fullmatch(r'[A-Z]{3}', unit)
                    ):
                        continue
                    eligible = []
                    for row in rows:
                        if not isinstance(row, dict):
                            raise ValueError('Invalid fact')
                        if row.get('form') not in FORMS or row.get('dimensions') or row.get('segment'):
                            continue
                        value = row.get('val')
                        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                            raise ValueError('Non-finite or missing value')
                        try:
                            end = date.fromisoformat(row['end'])
                            filed = date.fromisoformat(row['filed'])
                            start = date.fromisoformat(row['start']) if 'start' in row else None
                        except (ValueError, TypeError, KeyError) as exc:
                            raise ValueError('Invalid fact dates') from exc
                        if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', str(row.get('accn', ''))):
                            raise ValueError('Missing accession provenance')
                        if end > filed or filed > now().date():
                            raise ValueError('Inconsistent fact dates')
                        if metric in {'cash', 'totalAssets', 'borrowings', 'outstandingShares'}:
                            if start is not None:
                                continue
                        elif start is None or not 300 <= (end - start).days <= 380:
                            continue
                        # Retain the supplied provenance and all amendments/comparatives.
                        eligible.append(dict(row))
                    if eligible:
                        units[unit] = eligible
                if units:
                    result.setdefault(namespace, {})[tag] = {'units': units}
    if not any(ns in result for ns in ('ifrs-full', 'us-gaap')):
        raise ValueError('No eligible financial facts')
    return result


def collect(companies, previous):
    old = {str(c.get('cik', '')).zfill(10): c for c in previous.get('companies', [])}
    output = {'schema': 1, 'generated_at': now().isoformat(), 'companies': [], 'unavailable': []}
    blocked = False
    for company in companies:
        cik = str(company['cik']).zfill(10)
        if not re.fullmatch(r'\d{10}', cik):
            raise ValueError('Invalid configured CIK')
        cached = old.get(cik)
        if cached:
            try:
                compact_facts(cached, cik, downloaded=False)
            except ValueError:
                cached = None
        stamp = parse_time(cached.get('fetched_at')) if cached else None
        fresh = stamp is not None and timedelta(0) <= now() - stamp < CACHE_AGE
        source = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
        if fresh:
            item = {**cached, **company, 'cik': cik, 'status': 'CACHE_FRESH'}
        else:
            try:
                if blocked:
                    raise SecBlocked('SEC requests stopped after blocking response')
                facts = compact_facts(request_json(source), cik)
                if cached:
                    previous_facts = compact_facts(cached, cik, downloaded=False)
                    for ns, tags in previous_facts.items():
                        for tag, concept in tags.items():
                            for unit, rows in concept['units'].items():
                                incoming = facts.get(ns, {}).get(tag, {}).get('units', {}).get(unit, [])
                                keys = {(r.get('start'), r['end'], r['accn'], r['val']) for r in incoming}
                                if any((r.get('start'), r['end'], r['accn'], r['val']) not in keys for r in rows):
                                    raise ValueError('Incomplete or revised response: prior observation missing; review required')
                item = dict(company, cik=cik, facts=facts, fetched_at=now().isoformat(),
                            source_url=source, status='FETCHED', validation_status='CANDIDATE')
            except (SecBlocked, urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as exc:
                blocked = blocked or isinstance(exc, SecBlocked)
                error = str(exc)
                if cached:
                    # Never relabel old observations with a new fetch timestamp.
                    item = {**cached, **company, 'cik': cik, 'status': 'STALE_RETAINED', 'error': error}
                else:
                    output['unavailable'].append(dict(company, cik=cik, status='UNAVAILABLE', error=error))
                    continue
        if item['status'] != 'STALE_RETAINED':
            item.pop('error', None)
        item['validation_status'] = 'CANDIDATE'
        output['companies'].append(item)
    return output


def write_atomic(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main():
    # Fail closed on corrupt local state: do not overwrite an unreadable cache.
    previous = json.loads(OUTPUT.read_text(encoding='utf-8')) if OUTPUT.exists() else {}
    companies = json.loads(CONFIG.read_text(encoding='utf-8'))['companies']
    result = collect(companies, previous)
    write_atomic(OUTPUT, result)
    for item in result['companies'] + result['unavailable']:
        print(item['name'], item['status'])


if __name__ == '__main__':
    main()
