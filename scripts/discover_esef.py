
#!/usr/bin/env python3
"""IIC V5 — Découverte des rapports financiers européens ESEF."""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "esef_discovery.json"
BASE_URL = "https://filings.xbrl.org/api/filings"

UA = os.environ.get(
    "SEC_USER_AGENT",
    "IIC research contact@example.com"
)

COMPANIES = [
    {
        "name": "VINCI",
        "country": "FR",
        "search": "VINCI"
    },
    {
        "name": "Siemens AG",
        "country": "DE",
        "search": "Siemens"
    }
]


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": (
                "application/vnd.api+json, "
                "application/json"
            )
        }
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                request,
                timeout=40
            ) as response:
                return json.load(response)

        except urllib.error.HTTPError as exc:
            if exc.code not in (
                429, 500, 502, 503, 504
            ) or attempt == 2:
                raise

            retry = exc.headers.get(
                "Retry-After", ""
            )

            delay = min(
                120,
                max(
                    5 * (2 ** attempt),
                    int(retry) if retry.isdigit() else 0
                )
            )

            print(
                f"HTTP {exc.code}: retry in {delay}s",
                flush=True
            )
            time.sleep(delay)

        except (
            urllib.error.URLError,
            TimeoutError
        ):
            if attempt == 2:
                raise

            time.sleep(5 * (2 ** attempt))

    raise RuntimeError("No response")


def search(company):
    filters = [
        {
            "name": "country",
            "op": "eq",
            "val": company["country"]
        },
        {
            "name": "entity.name",
            "op": "ilike",
            "val": "%" + company["search"] + "%"
        }
    ]

    query = urllib.parse.urlencode({
        "filter": json.dumps(
            filters,
            separators=(",", ":")
        ),
        "sort": "-period_end",
        "page[size]": "20",
        "include": "entity"
    })

    payload = get_json(
        BASE_URL + "?" + query
    )

    entities = {
        str(item.get("id")):
            item.get("attributes", {})
        for item in payload.get("included", [])
        if item.get("type") == "entity"
    }

    matches = []

    for item in payload.get("data", []):
        attrs = item.get(
            "attributes", {}
        )

        rel = (
            item.get("relationships", {})
            .get("entity", {})
            .get("data")
            or {}
        )

        entity = entities.get(
            str(rel.get("id")),
            {}
        )

        matches.append({
            "filing_id": item.get("id"),
            "attributes": attrs,
            "entity": entity,
            "source": BASE_URL
        })

    return matches


def main():
    results = []

    for company in COMPANIES:
        try:
            matches = search(company)

            print(
                f"{company['name']}: "
                f"{len(matches)} candidate filing(s)",
                flush=True
            )

            results.append({
                "company": company["name"],
                "country": company["country"],
                "status": (
                    "FOUND"
                    if matches
                    else "NOT_FOUND"
                ),
                "filings": matches
            })

        except Exception as exc:
            print(
                f"{company['name']}: ERROR "
                f"{type(exc).__name__}: {exc}",
                flush=True
            )

            results.append({
                "company": company["name"],
                "country": company["country"],
                "status": "ERROR",
                "error": str(exc),
                "filings": []
            })

        time.sleep(1)

    output = {
        "schema": 1,
        "generated_at": (
            datetime.now(timezone.utc).isoformat()
        ),
        "purpose": (
            "discovery_only_no_financial_facts"
        ),
        "companies": results
    }

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp = OUTPUT.with_suffix(
        ".json.tmp"
    )

    temp.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    os.replace(temp, OUTPUT)

    print(
        f"Wrote {OUTPUT.relative_to(ROOT)}",
        flush=True
    )

    if all(
        row["status"] == "ERROR"
        for row in results
    ):
        sys.exit(
            "All ESEF discovery requests failed"
        )


if __name__ == "__main__":
    main()
