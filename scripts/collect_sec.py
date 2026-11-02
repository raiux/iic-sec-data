#!/usr/bin/env python3
"""Collect compact SEC XBRL facts for IIC; preserve verified per-company cache timestamps."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
CONFIG = BASE / 'iic_companies.json'
OUTPUT = BASE / 'data' / 'sec_facts.json'
UA = os.environ.get('SEC_USER_AGENT', '').strip()
if '@' not in UA or len(UA) < 12:
    sys.exit('Set SEC_USER_AGENT to a descriptive value containing a contact email')
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
                delay = max(30 * (2 ** attempt), seconds)
                print(f'HTTP 429: waiting {delay:.0f}s', flush=True)
                time.sleep(delay)
            elif error.code in (500, 502, 503, 504) and attempt < 2:
