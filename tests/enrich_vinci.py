#!/usr/bin/env python3
"""Conservative supplemental metrics and concept inventory from complete XBRL-JSON."""
import argparse
import json
from collections import defaultdict
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

# Concepts here are intentionally narrow: CAPEX and debt require separate scope review.
CONCEPTS = {
    'revenue': ('ifrs-full:RevenueFromContractsWithCustomers', 'duration'),
    'operating_profit': ('ifrs-full:ProfitLossFromOperatingActivities', 'duration'),
    'net_income_consolidated': ('ifrs-full:ProfitLoss', 'duration'),
    'net_income_parent': ('ifrs-full:ProfitLossAttributableToOwnersOfParent', 'duration'),
    'net_income_noncontrolling': ('ifrs-full:ProfitLossAttributableToNoncontrollingInterests', 'duration'),
    'operating_cash_flow': ('ifrs-full:CashFlowsFromUsedInOperatingActivities', 'duration'),
    'cash_and_equivalents': ('ifrs-full:CashAndCashEquivalents', 'instant'),
    'total_assets': ('ifrs-full:Assets', 'instant'),
    'total_equity': ('ifrs-full:Equity', 'instant'),
    'total_liabilities': ('ifrs-full:Liabilities', 'instant'),
}
DIAGNOSTIC_WORDS = ('Liabilit', 'Borrow', 'Debt', 'Depreciation', 'Amortisation', 'Amortization', 'PurchaseOf', 'Purchaseof', 'Expenditure', 'CashFlowsFromUsedInInvesting')

def canonical_period(raw):
    try:
        if '/' in raw:
            a,b=raw.split('/',1)
            return ('duration',date.fromisoformat(a[:10]).isoformat(),(date.fromisoformat(b[:10])-timedelta(days=1)).isoformat())
        return ('instant',(date.fromisoformat(raw[:10])-timedelta(days=1)).isoformat())
    except (ValueError,TypeError,AttributeError):return None

def normalize(doc, year=2025):
    facts=doc.get('facts')
    if not isinstance(facts,dict) or not facts:raise ValueError('Complete XBRL-JSON facts required')
    index=defaultdict(list); diagnostic=defaultdict(lambda: {'count':0,'years':set(),'units':set()})
    for fid,f in facts.items():
        if not isinstance(f,dict):continue
        d=f.get('dimensions') or {}
        if not isinstance(d,dict):continue
        concept=d.get('concept'); unit=d.get('unit'); p=canonical_period(d.get('period'))
        if not isinstance(concept,str):continue
        if any(w.lower() in concept.lower() for w in DIAGNOSTIC_WORDS):
            e=diagnostic[concept];e['count']+=1
            if p:e['years'].add(p[1][:4]);
            if unit:e['units'].add(unit)
        if unit!='iso4217:EUR' or set(d)-{'concept','entity','period','unit','language'}:continue
        if not p or not isinstance(d.get('entity'),str):continue
        try:
            v=Decimal(str(f.get('value')))
            if not v.is_finite():continue
        except (InvalidOperation,TypeError):continue
        index[(concept,p)].append((v,fid))
    output={'schema':3,'company':'VINCI','fiscal_year':year,'source_document':doc.get('source_url'),'input_coverage':'full_xbrl_json','generated_at':datetime.now(timezone.utc).isoformat(),'metrics_by_year':{},'checks':[], 'diagnostics':{}}
    for y in (year-1,year):
        metrics={}
        for name,(concept,typ) in CONCEPTS.items():
            p=('duration',f'{y}-01-01',f'{y}-12-31') if typ=='duration' else ('instant',f'{y}-12-31')
            matches=index.get((concept,p),[]); values={v for v,_ in matches}
            status='MISSING' if not matches else 'CONFLICT' if len(values)>1 else 'CANDIDATE'
            metrics[name]={'status':status,'value':str(next(iter(values))) if status=='CANDIDATE' else None,'currency':'EUR','concept':concept,'source_fact_ids':[fid for _,fid in matches],'candidate_count':len(matches),'period':p[1:]}
        output['metrics_by_year'][str(y)]=metrics
        def check(a,b,c,title):
            entries=[metrics[x] for x in (a,b,c)]
            if not all(e['status']=='CANDIDATE' for e in entries):return {'name':title,'year':y,'status':'NOT_TESTED','reason':'Independent tagged component missing or conflicting'}
            delta=Decimal(entries[0]['value'])-Decimal(entries[1]['value'])-Decimal(entries[2]['value'])
            return {'name':title,'year':y,'status':'PASS' if delta==0 else 'REVIEW','difference_eur':str(delta)}
        output['checks'].append(check('total_assets','total_liabilities','total_equity','balance_sheet_identity'))
        output['checks'].append(check('net_income_consolidated','net_income_parent','net_income_noncontrolling','profit_attribution'))
        # Derived liability is a diagnostic, never an independent verification.
        a,e=metrics['total_assets'],metrics['total_equity']
        if a['status']==e['status']=='CANDIDATE':
            output['checks'].append({'name':'derived_liabilities_reference','year':y,'status':'DERIVED_ONLY','value_eur':str(Decimal(a['value'])-Decimal(e['value'])),'reason':'Assets minus equity; must not be used to independently pass balance sheet check'})
    output['diagnostics']['candidate_concepts']=[{'concept':k,'fact_count':v['count'],'years':sorted(v['years']),'units':sorted(v['units'])} for k,v in sorted(diagnostic.items())]
    output['diagnostics']['note']='Inventory only: debt, depreciation and CAPEX require accounting definition and validation before mapping.'
    return output

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',default='data/vinci_xbrl_full_2025.json');p.add_argument('--output',default='data/vinci_enriched_2025.json');a=p.parse_args()
    result=normalize(json.loads(Path(a.input).read_text(encoding='utf-8')))
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for year,metrics in result['metrics_by_year'].items():print(year,[(k,v['status']) for k,v in metrics.items()])
    for c in result['checks']:print(c)
if __name__=='__main__':main()
