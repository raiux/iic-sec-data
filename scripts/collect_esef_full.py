#!/usr/bin/env python3
"""Fetch the complete official ESEF XBRL-JSON document with basic safeguards."""
import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VINCI_2025 = 'https://filings.xbrl.org/213800WFQ334R8UXUG83/2025-12-31/ESEF/FR/0/VINCI-2025-12-31-1-fr.json'
MAX_BYTES = 80 * 1024 * 1024

def download(url, attempts=3):
    if not url.startswith('https://filings.xbrl.org/'):
        raise ValueError('Only filings.xbrl.org HTTPS documents allowed')
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={'User-Agent':'IIC-V5-research/0.3 (public financial data)', 'Accept':'application/json'})
            with urllib.request.urlopen(req, timeout=90) as response:
                if response.status != 200:
                    raise RuntimeError(f'HTTP {response.status}')
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError('Document exceeds size limit')
            doc = json.loads(data)
            if not isinstance(doc, dict) or not isinstance(doc.get('facts'), dict) or not doc['facts']:
                raise ValueError('Invalid XBRL-JSON: missing facts object')
            return doc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if i == attempts - 1: raise RuntimeError(f'Unable to download after {attempts} attempts: {exc}') from exc
            time.sleep(2 ** i)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default=VINCI_2025)
    parser.add_argument('--output', default='data/vinci_xbrl_full_2025.json')
    args = parser.parse_args()
    doc = download(args.url)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Preserve original JSON facts, add source metadata for downstream traceability.
    doc['source_url'] = args.url
    doc['collected_at'] = datetime.now(timezone.utc).isoformat()
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(doc, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    temp.replace(target)
    print(f'Fetched {len(doc["facts"])} full XBRL facts to {target}')

if __name__ == '__main__':
    main()
