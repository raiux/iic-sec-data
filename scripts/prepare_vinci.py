"""Create explicit blocked input from actual normalized data; never infer FCFF from CFO."""
import argparse,json
from pathlib import Path

def build(source):
    metrics=source.get('metrics_by_year',{}).get('2025',source.get('metrics',{}))
    observed={k:{'value':v.get('value'),'status':v.get('status'),'concept':v.get('concept'),'source_fact_ids':v.get('source_fact_ids',[])} for k,v in metrics.items() if isinstance(v,dict)}
    required={'base_fcff_eur':None,'net_debt_eur':None,'minority_interests_eur':None,'nonoperating_assets_eur':None,'diluted_shares':None,'wacc':None,'terminal_growth':None,'growth_rates':None}
    return {'company':'VINCI','input_mode':'incomplete_real_data','source_checks_passed':False,'observed_financials':observed,'scenarios':{k:dict(required) for k in ('bear','base','bull')},'notes':'Fill inputs only with independently checked data and documented assumptions; operating cash flow is not FCFF.'}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    out=build(json.loads(Path(a.input).read_text(encoding='utf-8')));dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
