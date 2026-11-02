
#!/usr/bin/env python3
"""SEC EDGAR -> compact public XBRL facts for IIC.

No API keys, no holdings, no generated financial figures.
Compatible with existing IIC Google Sheets import.
"""

import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
OUTPUT_PATH = BASE / "data" / "sec_facts.json"
CONFIG_PATH = BASE / "iic_companies.json"

UA = os.environ.get("SEC_USER_AGENT", "").strip()

if "@" not in UA or len(UA) < 12:
    sys.exit(
        "Set SEC_USER_AGENT, e.g. "
        "IIC Research contact@example.com"
    )

# Minimum interval between starts of SEC requests.
MIN_INTERVAL_SECONDS = max(
    1.0,
    float(os.environ.get("IIC_SEC_INTERVAL_SECONDS", "1"))
)

CACHE_HOURS = 24
MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 40

SEC_TICKERS_URL = (
    "https://www.sec.gov/files/company_tickers.json"
)
SEC_FACTS_URL = (
    "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"
)

TAGS = {
    "ifrs-full": {
        "revenue": [
            "Revenue",
            "RevenueFromContractsWithCustomers",
        ],
        "netIncome": [
            "ProfitLoss",
        ],
        "operatingCashFlow": [
            "CashFlowsFromUsedInOperatingActivities",
        ],
        "capex": [
            "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        ],
        "cash": [
            "CashAndCashEquivalents",
        ],
        "totalAssets": [
            "Assets",
        ],
        "borrowings": [
            "Borrowings",
            "LongtermBorrowings",
        ],
        "dilutedShares": [
            "WeightedAverageSharesDiluted",
            "WeightedAverageNumberOfDilutedSharesOutstanding",
        ],
    },
    "us-gaap": {
        "revenue": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
        ],
        "netIncome": [
            "NetIncomeLoss",
        ],
        "operatingCashFlow": [
            "NetCashProvidedByUsedInOperatingActivities",
        ],
        "capex": [
            "PaymentsToAcquirePropertyPlantAndEquipment",
        ],
        "cash": [
            "CashAndCashEquivalentsAtCarryingValue",
        ],
        "totalAssets": [
            "Assets",
        ],
        "borrowings": [
            "LongTermDebt",
            "LongTermDebtNoncurrent",
        ],
        "dilutedShares": [
            "WeightedAverageNumberOfDilutedSharesOutstanding",
        ],
    },
}

TAGS["dei"] = {
    "outstandingShares": [
        "EntityCommonStockSharesOutstanding",
    ],
}

ALLOWED_FORMS = {
    "10-K",
    "10-K/A",
    "20-F",
    "20-F/A",
    "40-F",
    "40-F/A",
}


class SecForbiddenError(Exception):
    """SEC returned HTTP 403. Stop all SEC requests."""


class SecRateLimitError(Exception):
    """SEC rate limit persisted after retries."""


class RequestLimiter:
    def __init__(self, interval):
        self.interval = interval
        self.last_request = None

    def wait(self):
        if self.last_request is not None:
            elapsed = time.monotonic() - self.last_request
            remaining = self.interval - elapsed
            if remaining > 0:
                time.sleep(remaining)

        self.last_request = time.monotonic()


LIMITER = RequestLimiter(MIN_INTERVAL_SECONDS)


def utc_now():
    return datetime.now(timezone.utc)


def parse_datetime(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def retry_after_seconds(header_value):
    if not header_value:
        return None

    try:
        return max(0, float(header_value))
    except (ValueError, TypeError):
        pass

    try:
        dt = parsedate_to_datetime(header_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(
            0,
            (dt - utc_now()).total_seconds()
        )
    except (ValueError, TypeError, OverflowError):
        return None


def fetch_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )

    for attempt in range(MAX_ATTEMPTS):
        LIMITER.wait()

        try:
            with urllib.request.urlopen(
                request,
                timeout=TIMEOUT_SECONDS
            ) as response:
                return json.load(response)

        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise SecForbiddenError(
                    "HTTP 403: SEC access denied. "
                    "Stopping all SEC requests."
                ) from exc

            if exc.code == 429:
                if attempt == MAX_ATTEMPTS - 1:
                    raise SecRateLimitError(
                        "HTTP 429: rate limit persisted"
                    ) from exc

                retry_after = retry_after_seconds(
                    exc.headers.get("Retry-After")
                )

                backoff = min(
                    120,
                    30 * (2 ** attempt)
                )

                delay = max(
                    backoff,
                    retry_after or 0
                )

                print(
                    f"HTTP 429: waiting {delay:.0f}s "
                    "before retry",
                    flush=True,
                )
                time.sleep(delay)
                continue

            if exc.code in (500, 502, 503, 504):
                if attempt == MAX_ATTEMPTS - 1:
                    raise

                delay = min(60, 5 * (2 ** attempt))
                print(
                    f"HTTP {exc.code}: retry in {delay}s",
                    flush=True,
                )
                time.sleep(delay)
                continue

            raise

        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
        ):
            if attempt == MAX_ATTEMPTS - 1:
                raise

            delay = min(60, 5 * (2 ** attempt))
            print(
                f"Network error: retry in {delay}s",
                flush=True,
            )
            time.sleep(delay)

    raise RuntimeError("SEC fetch failed")


def ticker_key(ticker):
    return re.sub(
        r"\.[A-Z]+$",
        "",
        str(ticker).upper().split(":")[-1].strip()
    )


def resolve_cik(company, by_ticker):
    cik = company.get("cik")

    if cik is not None and str(cik).strip():
        return str(int(cik)).zfill(10)

    ticker = ticker_key(company.get("ticker", ""))
    matches = by_ticker.get(ticker, [])

    if len(matches) != 1:
        return None

    return str(matches[0]).zfill(10)


def compact_facts(raw):
    result = {}
    cutoff = utc_now().year - 6

    for taxonomy, mapping in TAGS.items():
        source = raw.get("facts", {}).get(
            taxonomy, {}
        )
        chosen = {}

        for tags in mapping.values():
            for tag in tags:
                if tag not in source:
                    continue

                node = source[tag]
                units = {}

                for unit, observations in node.get(
                    "units", {}
                ).items():
                    filtered = []

                    for observation in observations:
                        if observation.get("form") not in ALLOWED_FORMS:
                            continue

                        end = str(
                            observation.get("end", "0000")
                        )

                        try:
                            year = int(end[:4])
                        except ValueError:
                            continue

                        if year < cutoff:
                            continue

                        value = observation.get("val")

                        if isinstance(value, bool):
                            continue

                        if not isinstance(
                            value, (int, float)
                        ):
                            continue

                        fields = (
                            "start",
                            "end",
                            "val",
                            "filed",
                            "form",
                            "accn",
                            "fy",
                            "fp",
                            "frame",
                        )

                        filtered.append({
                            key: observation[key]
                            for key in fields
                            if key in observation
                        })

                    if filtered:
                        units[unit] = filtered

                if units:
                    chosen[tag] = {
                        "units": units
                    }

        if chosen:
            result[taxonomy] = chosen

    return result


def load_previous():
    if not OUTPUT_PATH.exists():
        return {}, None

    try:
        payload = json.loads(
            OUTPUT_PATH.read_text(
                encoding="utf-8"
            )
        )

        companies = {}

        for company in payload.get("companies", []):
            cik = str(
                int(company["cik"])
            ).zfill(10)

            companies[cik] = company

        return companies, payload

    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
    ) as exc:
        print(
            f"WARNING: previous output invalid: {exc}",
            flush=True,
        )
        return {}, None


def cache_is_fresh(previous_payload, cik):
    if not previous_payload:
        return False

    # Existing schema has one global collection timestamp.
    # A partially refreshed export is never published.
    generated = parse_datetime(
        previous_payload.get("generated_at")
    )

    if generated is None:
        return False

    age = utc_now() - generated

    return (
        timedelta(0)
        <= age
        < timedelta(hours=CACHE_HOURS)
    )


def atomic_write_json(path, payload):
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_path = path.with_name(
        path.name + ".tmp"
    )

    with temp_path.open(
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        file.write("\n")

    os.replace(temp_path, path)


def main():
    if not CONFIG_PATH.exists():
        sys.exit(
            "Missing iic_companies.json"
        )

    config = json.loads(
        CONFIG_PATH.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(config, dict):
        sys.exit(
            "iic_companies.json must be an object "
            "containing a companies array"
        )

    companies = config.get("companies", [])

    if not isinstance(companies, list) or not companies:
        sys.exit(
            "No companies in iic_companies.json"
        )

    previous_companies, previous_payload = (
        load_previous()
    )

    # Only download SEC ticker index when a CIK is missing.
    needs_lookup = any(
        not str(company.get("cik", "")).strip()
        for company in companies
        if isinstance(company, dict)
    )

    by_ticker = {}

    if needs_lookup:
        print(
            "Fetching SEC ticker index...",
            flush=True,
        )

        try:
            index = fetch_json(
                SEC_TICKERS_URL
            )

            for item in index.values():
                ticker = ticker_key(
                    item["ticker"]
                )

                by_ticker.setdefault(
                    ticker, []
                ).append(
                    item["cik_str"]
                )

        except SecForbiddenError as exc:
            sys.exit(str(exc))

        except Exception as exc:
            sys.exit(
                "Cannot resolve missing CIKs: "
                f"{type(exc).__name__}: {exc}"
            )

    output = []
    failures = []

    fetched = 0
    cached = 0
    seen_ciks = set()

    for company in companies:
        if not isinstance(company, dict):
            failures.append(
                "Invalid company configuration"
            )
            continue

        name = str(
            company.get("name", "")
        ).strip()

        ticker = str(
            company.get("ticker", "")
        ).strip()

        if not name or not ticker:
            failures.append(
                "Company missing name or ticker"
            )
            continue

        try:
            cik = resolve_cik(
                company,
                by_ticker
            )
        except (ValueError, TypeError) as exc:
            failures.append(
                f"{name}: invalid CIK: {exc}"
            )
            continue

        if not cik:
            failures.append(
                f"{name}: no unambiguous SEC CIK "
                f"for {ticker}"
            )
            continue

        if cik in seen_ciks:
            failures.append(
                f"{name}: duplicate CIK {cik}"
            )
            continue

        seen_ciks.add(cik)

        previous = previous_companies.get(
            cik
        )

        if (
            previous
            and previous.get("facts")
            and cache_is_fresh(
                previous_payload, cik
            )
        ):
            output.append({
                "name": name,
                "ticker": ticker,
                "cik": cik,
                "facts": previous["facts"],
            })

            cached += 1

            print(
                f"CACHE {name}: {cik} "
                "using recent SEC facts",
                flush=True,
            )
            continue

        url = SEC_FACTS_URL.format(cik)

        try:
            raw = fetch_json(url)

            if int(
                raw.get("cik", -1)
            ) != int(cik):
                raise ValueError(
                    "SEC CIK mismatch"
                )

            facts = compact_facts(raw)

            if not facts:
                raise ValueError(
                    "No standard XBRL facts"
                )

            output.append({
                "name": name,
                "ticker": ticker,
                "cik": cik,
                "facts": facts,
            })

            fetched += 1

            print(
                f"OK {name}: {cik} "
                f"namespaces={','.join(facts)}",
                flush=True,
            )

        except SecForbiddenError as exc:
            failures.append(
                f"{name}: {exc}"
            )
            print(
                "STOP: SEC returned HTTP 403",
                flush=True,
            )
            break

        except SecRateLimitError as exc:
            failures.append(
                f"{name}: {exc}"
            )
            print(
                "STOP: persistent SEC rate limit",
                flush=True,
            )
            break

        except Exception as exc:
            failures.append(
                f"{name}: "
                f"{type(exc).__name__}: {exc}"
            )

    if failures:
        print(
            "Collection incomplete. "
            "Previous JSON preserved.",
            flush=True,
        )

        for failure in failures:
            print(
                "UNAVAILABLE",
                failure,
                flush=True,
            )

        sys.exit(1)

    if len(output) != len(companies):
        sys.exit(
            "Company count mismatch. "
            "Previous JSON preserved."
        )

    result = {
        "schema": 1,
        "generated_at": utc_now().isoformat(),
        "companies": output,
        "unavailable": [],
    }

    atomic_write_json(
        OUTPUT_PATH,
        result
    )

    print(
        f"Completed: {len(output)} companies; "
        f"{fetched} fetched; "
        f"{cached} cached; "
        "0 unavailable",
        flush=True,
    )


if __name__ == "__main__":
    main()
