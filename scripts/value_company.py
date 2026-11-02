#!/usr/bin/env python3
"""IIC V5: deterministic FCFF DCF; never silently fill financial inputs."""
import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec = 32
D = lambda x: Decimal(str(x))
REQUIRED = ('base_fcff_eur','net_debt_eur','minority_interests_eur','nonoperating_assets_eur','diluted_shares','wacc','terminal_growth','growth_rates')

def compute_scenario(d):
    missing = [k for k in REQUIRED if d.get(k) is None]
    if missing:
        return {'status':'BLOCKED','missing':missing,'reason':'Required input absent; no estimate computed'}
    try:
        base, debt, minority, assets, shares = (D(d[k]) for k in REQUIRED[:5])
        wacc, tg = D(d['wacc']), D(d['terminal_growth'])
        growth = [D(x) for x in d['growth_rates']]
        if not growth or len(growth)>20: raise ValueError('growth_rates must have 1–20 years')
        if shares <= 0 or base <= 0: raise ValueError('shares and base FCFF must be positive')
        if not (D('0') < wacc < D('1')) or not (D('-0.1') < tg < wacc): raise ValueError('invalid WACC/terminal growth')
        if any(g <= -1 or g > 2 for g in growth): raise ValueError('invalid FCFF growth rate')
        flows=[]; pv=D(0); fcff=base
        for year,g in enumerate(growth,1):
            fcff*=1+g
            discounted=fcff/(1+wacc)**year
            flows.append({'year':year,'growth':str(g),'fcff_eur':str(fcff),'pv_eur':str(discounted)})
            pv+=discounted
        terminal=fcff*(1+tg)/(wacc-tg)
        pv_terminal=terminal/(1+wacc)**len(growth)
        ev=pv+pv_terminal
        equity=ev-debt-minority+assets
        return {'status':'CALCULATED','enterprise_value_eur':str(ev),'equity_value_eur':str(equity),'fair_value_per_share_eur':str(equity/shares),'pv_explicit_eur':str(pv),'pv_terminal_eur':str(pv_terminal),'terminal_weight':str(pv_terminal/ev),'forecast':flows,'warning':'DCF model output; not an investment recommendation'}
    except (ValueError, ArithmeticError, TypeError) as exc:
        return {'status':'INVALID','reason':str(exc)}

def value_payload(payload):
    scenario_inputs=payload.get('scenarios',{})
    results={k:compute_scenario(scenario_inputs[k]) if k in scenario_inputs else {'status':'BLOCKED','reason':'Scenario missing'} for k in ('bear','base','bull')}
    all_ok=all(v['status']=='CALCULATED' for v in results.values())
    if all_ok:
        vals=[D(results[k]['fair_value_per_share_eur']) for k in ('bear','base','bull')]
        if vals != sorted(vals):
            results['base']['warning']='Scenario ordering inconsistent; review assumptions'
    return {'schema':1,'engine':'iic-v5-fcff-dcf','company':payload.get('company'),'input_mode':payload.get('input_mode','unverified'),'generated_at':datetime.now(timezone.utc).isoformat(),'status':'CALCULATED' if all_ok else 'INCOMPLETE','scenarios':results,'production_ready':all_ok and payload.get('input_mode')=='verified' and payload.get('source_checks_passed') is True,'notes':['A calculated scenario is not automatically a verified valuation.','Net debt must include lease treatment consistent with FCFF.','Do not equate operating cash flow minus CAPEX with FCFF.']}

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);args=p.parse_args()
    data=json.loads(Path(args.input).read_text(encoding='utf-8'))
    out=value_payload(data);path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('status:',out['status'],'production_ready:',out['production_ready'],'output:',path)
if __name__=='__main__':main()
