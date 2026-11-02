
#!/usr/bin/env python3
"""IIC V5 - Test de lecture du rapport ESEF VINCI 2025.

Découverte et inspection uniquement.
Aucune donnée financière inventée.
Aucune modification des données SEC.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DISCOVERY = ROOT / "data" / "esef_discovery.json"
OUTPUT = ROOT / "data" / "vinci_xbrl_test.json"

BASE_URL = "https://filings.xbrl.org"

USER_AGENT = os.environ.get(
    "IIC_USER_AGENT",
    "IIC-V5-Research/1.0"
)

KEYWORDS = [
    "revenue",
    "profitloss",
    "cashflowsfromusedinoperating",
    "purchaseofproperty",
    "propertyplantandequipment",
    "cashandcashequivalents",
    "assets",
    "borrowings",
    "debt",
    "weightedaveragenumberofshares",
]


def fetch_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=90
    ) as response:
        return json.load(response)


def locate_report():
    if not DISCOVERY.exists():
        raise FileNotFoundError(
            "Missing data/esef_discovery.json"
        )

    discovery = json.loads(
        DISCOVERY.read_text(encoding="utf-8")
    )

    for company in discovery.get("companies", []):
        if company.get("company") != "VINCI":
            continue

        for filing in company.get("filings", []):
            attrs = filing.get("attributes", {})
            entity = filing.get("entity", {})

            if (
                attrs.get("period_end") == "2025-12-31"
                and entity.get("identifier")
                == "213800WFQ334R8UXUG83"
            ):
                path = attrs.get("json_url")

                if not path or not path.startswith("/"):
                    raise ValueError(
                        "Invalid ESEF JSON path"
                    )

                return BASE_URL + path

    raise ValueError(
        "VINCI 2025 report not found"
    )


def inspect_report(payload):
    document_info = payload.get(
        "documentInfo", {}
    )

    facts = payload.get("facts", {})

    if not isinstance(facts, dict):
        raise ValueError(
            "Unexpected xBRL facts structure"
        )

    concepts = {}
    matching_facts = []

    for fact_id, fact in facts.items():
        if not isinstance(fact, dict):
            continue

        dimensions = fact.get(
            "dimensions", {}
        )

        concept = dimensions.get(
            "concept", ""
        )

        if not concept:
            continue

        concepts[concept] = (
            concepts.get(concept, 0) + 1
        )

        if any(
            keyword in concept.lower()
            for keyword in KEYWORDS
        ):
            matching_facts.append({
                "fact_id": fact_id,
                "concept": concept,
                "value": fact.get("value"),
                "unit": dimensions.get("unit"),
                "period": dimensions.get("period"),
                "entity": dimensions.get("entity"),
                "language": dimensions.get("language"),
                "decimals": fact.get("decimals"),
                "dimensions": dimensions,
            })

    return {
        "total_facts": len(facts),
        "unique_concepts": len(concepts),
        "matching_facts": matching_facts,
        "concept_names": sorted(concepts),
        "document_info": document_info,
    }


def main():
    url = locate_report()

    print(
        f"Downloading VINCI ESEF JSON: {url}",
        flush=True
    )

    payload = fetch_json(url)

    if not isinstance(payload, dict):
        raise ValueError(
            "Unexpected JSON root"
        )

    inspection = inspect_report(payload)

    if inspection["total_facts"] == 0:
        raise ValueError(
            "No XBRL facts found"
        )

    result = {
        "schema": 1,
        "company": "VINCI",
        "lei": "213800WFQ334R8UXUG83",
        "report_year": 2025,
        "source_url": url,
        "inspected_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "total_facts": inspection["total_facts"],
        "unique_concepts": inspection[
            "unique_concepts"
        ],
        "matching_facts": inspection[
            "matching_facts"
        ],
        "concept_names": inspection[
            "concept_names"
        ],
    }

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary = OUTPUT.with_suffix(
        ".json.tmp"
    )

    temporary.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    os.replace(temporary, OUTPUT)

    print(
        f"Total XBRL facts: "
        f"{inspection['total_facts']}"
    )

    print(
        f"Unique concepts: "
        f"{inspection['unique_concepts']}"
    )

    print(
        f"Financial candidate facts: "
        f"{len(inspection['matching_facts'])}"
    )

    print("Candidate concepts:")

    names = sorted({
        fact["concept"]
        for fact in inspection["matching_facts"]
    })

    for concept in names[:60]:
        print(f"  {concept}")

    print(
        "Inspection complete. "
        "No financial values normalized."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"ERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr
        )
        sys.exit(1)
