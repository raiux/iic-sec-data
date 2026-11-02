#!/usr/bin/env python3
"""SEC EDGAR -> compact public XBRL facts for IIC V4.5.
No API keys, no holdings, no generated financial figures.
"""
import json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
UA = os.environ.get("SEC_USER_AGENT", "").strip()
if "@" not in UA or len(UA) < 12:
    sys.exit("Set GitHub Actions variable SEC_USER_AGENT, e.g. IIC Research you@example.com")
TAGS = {
 "ifrs-full": {
  "revenue": ["Revenue","RevenueFromContractsWithCustomers"],
  "netIncome": ["ProfitLoss"],
  "operatingCashFlow": ["CashFlowsFromUsedInOperatingActivities"],
  "capex": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
  "cash": ["CashAndCashEquivalents"],
  "totalAssets": ["Assets"],
  "borrowings": ["Borrowings","LongtermBorrowings"],
  "dilutedShares": ["WeightedAverageSharesDiluted","WeightedAverageNumberOfDilutedSharesOutstanding"]},
 "us-gaap": {
  "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax","Revenues"],
  "netIncome": ["NetIncomeLoss"],
  "operatingCashFlow": ["NetCashProvidedByUsedInOperatingActivities"],
  "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
  "cash": ["CashAndCashEquivalentsAtCarryingValue"],
  "totalAssets": ["Assets"],
  "borrowings": ["LongTermDebt","LongTermDebtNoncurrent"],
  "dilutedShares": ["WeightedAverageNumberOfDilutedSharesOutstanding"]}
}
TAGS["dei"]={"outstandingShares":["EntityCommonStockSharesOutstanding"]}
ALLOWED_FORMS={"10-K","10-K/A","20-F","20-F/A","40-F","40-F/A"}
def fetch_json(url):
    request=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request,timeout=40) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code not in (429,500,502,503,504) or attempt==2: raise
            time.sleep(3*(attempt+1))
    raise RuntimeError("Fetch failed")
def ticker_key(ticker):
    return re.sub(r"\.[A-Z]+$","",str(ticker).upper().split(":")[-1].strip())
def resolve_cik(company, by_ticker):
    if company.get("cik"):
        return str(int(company["cik"])).zfill(10)
    ticker=ticker_key(company.get("ticker",""))
    hits=by_ticker.get(ticker,[])
    if len(hits)!=1: return None
    return str(hits[0]).zfill(10)
def compact_facts(raw):
    result={}
    cutoff=datetime.now(timezone.utc).year-6
    for taxonomy, mapping in TAGS.items():
        src=raw.get("facts",{}).get(taxonomy,{})
        chosen={}
        for tags in mapping.values():
            for tag in tags:
                if tag not in src: continue
                node=src[tag]
                units={}
                for unit, obs in node.get("units",{}).items():
                    filtered=[{k:x[k] for k in ("start","end","val","filed","form","accn","fy","fp","frame") if k in x}
                              for x in obs if x.get("form") in ALLOWED_FORMS and
                              int(str(x.get("end","0000"))[:4])>=cutoff and
                              isinstance(x.get("val"),(int,float))]
                    if filtered: units[unit]=filtered
                if units: chosen[tag]={"units":units}
        if chosen: result[taxonomy]=chosen
    return result
def main():
    cfg=json.loads((BASE/"iic_companies.json").read_text())
    companies=cfg.get("companies",[])
    if not companies: sys.exit("No companies in iic_companies.json")
    index=fetch_json("https://www.sec.gov/files/company_tickers.json")
    by_ticker={}
    for item in index.values():
        by_ticker.setdefault(ticker_key(item["ticker"]),[]).append(item["cik_str"])
    prior_path=BASE/"data/sec_facts.json"
    prior={}
    if prior_path.exists():
        try:
            prior={x["name"]:x for x in json.loads(prior_path.read_text()).get("companies",[])}
        except (ValueError,KeyError): pass
    output=[];failures=[];success=0
    for company in companies:
        name=str(company.get("name","")).strip()
        ticker=str(company.get("ticker","")).strip()
        if not name or not ticker: continue
        cik=resolve_cik(company,by_ticker)
        if not cik:
            failures.append(f"{name}: no unambiguous SEC CIK for {ticker}")
            continue
        url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        try:
            data=fetch_json(url)
            if int(data.get("cik",-1))!=int(cik): raise ValueError("CIK mismatch")
            facts=compact_facts(data)
            if not facts: raise ValueError("No standard XBRL facts")
            output.append({"name":name,"ticker":ticker,"cik":cik,"facts":facts})
            success+=1
            print(f"OK {name}: {cik} namespaces={','.join(facts)}",flush=True)
        except Exception as e:
            failures.append(f"{name}: {type(e).__name__}: {e}")
            # Never pretend stale data is current. Retain it only as a diagnostic,
            # not in the published current payload.
        time.sleep(0.25)
    if success==0:
        sys.exit("No successful SEC company fetch; preserving previous output. Errors: "+"; ".join(failures))
    result={"schema":1,"generated_at":datetime.now(timezone.utc).isoformat(),
            "companies":output,"unavailable":failures}
    prior_path.parent.mkdir(exist_ok=True)
    prior_path.write_text(json.dumps(result,ensure_ascii=False,separators=(",",":"))+"\n")
    print(f"Completed: {success} companies; {len(failures)} unavailable")
    for f in failures: print("UNAVAILABLE",f)
if __name__=="__main__": main()
