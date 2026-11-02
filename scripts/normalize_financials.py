#!/usr/bin/env python3
"""Auditable IFRS ESEF normalization; US-GAAP adapter intentionally not yet enabled."""
import argparse
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

MAPPINGS = {
 'revenue': ['ifrs-full:RevenueFromContractsWithCustomers', 'ifrs-full:Revenue'],
 'operating_profit': ['ifrs-full:ProfitLossFromOperatingActivities'],
 'net_income_consolidated': ['ifrs-full:ProfitLoss'],
 'net_income_parent': ['ifrs-full:ProfitLossAttributableToOwnersOfParent'],
 'operating_cash_flow': ['ifrs-full:CashFlowsFromUsedInOperatingActivities'],
 'cash_and_equivalents': ['ifrs-full:CashAndCashEquivalents'],
 'total_assets': ['ifrs-full:Assets'],
 'total_equity': ['ifrs-full:Equity'],
 'total_liabilities': ['ifrs-full:Liabilities'],
}
INSTANT = {'cash_and_equivalents','total_assets','total_equity','total_liabilities'}

def period(raw):
    if not isinstance(raw,str): return None
    try:
        if '/' in raw:
            a,b=raw.split('/',1); start=date.fromisoformat(a[:10]); stop=date.fromisoformat(b[:10]);
            if stop<=start:return None
            return ('duration',start.isoformat(),(stop-timedelta(days=1)).isoformat())
        return ('instant',(date.fromisoformat(raw[:10])-timedelta(days=1)).isoformat())
    except ValueError:return None

def extract_facts(doc):
    if isinstance(doc.get('matching_facts'),list):return doc['matching_facts'],'test_subset'
    raw=doc.get('facts')
    if isinstance(raw,dict):
        result=[]
        for fid,f in raw.items():
            if not isinstance(f,dict):continue
            dims=f.get('dimensions') or {}
            result.append({'fact_id':fid,'concept':dims.get('concept'),'value':f.get('value'), 'unit':dims.get('unit'), 'period':dims.get('period'),'entity':dims.get('entity'),'dimensions':dims,'decimals':f.get('decimals')})
        return result,'full_xbrl_json'
    raise ValueError('Expected matching_facts list or facts object')

def normalize(doc,year,company=None):
    facts,coverage=extract_facts(doc)
    if coverage=='test_subset' and doc.get('report_year') not in (None,year):raise ValueError('Year mismatch')
    company=company or doc.get('company') or 'UNKNOWN'
    output={'schema':2,'company':company,'fiscal_year':year,'source_document':doc.get('source_url'),'input_coverage':coverage,'generated_at':datetime.now(timezone.utc).isoformat(),'metrics':{},'checks':[]}
    for metric,concepts in MAPPINGS.items():
        by_concept={}
        for concept in concepts:
            matches=[]
            for f in facts:
                if f.get('concept')!=concept or f.get('unit')!='iso4217:EUR':continue
                dims=f.get('dimensions')
                if not isinstance(dims,dict) or set(dims)-{'concept','entity','period','unit','language'}:continue
                if any(dims.get(k)!=f.get(k) for k in ('entity','period','unit')):continue
                p=period(f.get('period'))
                if metric in INSTANT:
                    if p!=('instant',f'{year}-12-31'):continue
                elif p!=('duration',f'{year}-01-01',f'{year}-12-31'):continue
                try:
                    val=Decimal(str(f['value']))
                    if not val.is_finite():continue
                except (KeyError,TypeError,InvalidOperation):continue
                matches.append((val,f.get('fact_id')))
            if matches:by_concept[concept]=matches
        allvals={v for rows in by_concept.values() for v,_ in rows}
        if not by_concept: status,value,chosen='MISSING',None,None
        elif len(allvals)>1:status,value,chosen='CONFLICT',None,None
        else:
            status,value,chosen='CANDIDATE',str(next(iter(allvals))),next(c for c in concepts if c in by_concept)
        output['metrics'][metric]={'status':status,'value':value,'currency':'EUR','concept':chosen,'matched_concepts':list(by_concept),'source_fact_ids':[fid for rows in by_concept.values() for _,fid in rows],'candidate_count':sum(map(len,by_concept.values())),'reason':{'MISSING':'No eligible fact in supplied input','CONFLICT':'Multiple distinct values across eligible facts/concepts','CANDIDATE':'Matching exact period, unit and undimensioned entity; accounting validation pending'}[status]}
    a=output['metrics']
    for name,parts in [('balance_sheet_identity',('total_assets','total_liabilities','total_equity'))]:
        rows=[a[k] for k in parts]
        if all(r['status']=='CANDIDATE' for r in rows):
            delta=Decimal(rows[0]['value'])-Decimal(rows[1]['value'])-Decimal(rows[2]['value'])
            output['checks'].append({'name':name,'status':'PASS' if delta==0 else 'REVIEW','difference_eur':str(delta)})
        else:output['checks'].append({'name':name,'status':'NOT_TESTED','reason':'Missing or conflicting components'})
    return output

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--year',type=int,required=True);p.add_argument('--company');args=p.parse_args()
    src=Path(args.input);out=Path(args.output)
    result=normalize(json.loads(src.read_text(encoding='utf8')),args.year,args.company)
    result['source_file']=str(src)
    out.parent.mkdir(parents=True,exist_ok=True);tmp=out.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8');tmp.replace(out)
    for key,item in result['metrics'].items():print(key,item['status'],item['value'])
if __name__=='__main__':main()
