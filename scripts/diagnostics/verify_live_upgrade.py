"""Opt-in, bounded live smoke in its own process/session, separate from tests."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.services import ai
from fastapi.testclient import TestClient
from backend import app as service

def events(response):
    rows=[]
    for block in response.text.split('\n\n'):
        lines=block.splitlines()
        if len(lines)>=2:rows.append({'event':lines[0].removeprefix('event: '),'data':json.loads(lines[1].removeprefix('data: '))})
    return rows

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--image',action='store_true');parser.add_argument('--persistence',action='store_true');parser.add_argument('--output',type=Path,default=Path('artifacts/live-upgrade'));args=parser.parse_args()
    if not args.live:raise SystemExit('Live smoke needs explicit --live; default test suite never calls providers.')
    out=args.output;out.mkdir(parents=True,exist_ok=True);records={}
    def record(name,value):
        records[name]=value;(out/'results.json').write_text(ai.redact_text(json.dumps(records,ensure_ascii=False,indent=2)),encoding='utf-8');print(name+': '+str(value.get('status',value.get('mode','recorded'))),flush=True)
    with TestClient(service.app) as client:
        started=time.monotonic();reply=client.post('/api/ai/chat',json={'text':'Pouch 14x10 cm, depan biru','ai_consent':True});rows=events(reply);brief=next((r['data'] for r in rows if r['event']=='brief'),{})
        record('initial_chat',{'http':reply.status_code,'status':brief.get('status'),'elapsed_s':round(time.monotonic()-started,2),'events':rows})
        if not brief.get('proposal'):return
        state=client.get('/api/state').json();state['data']['request']=brief['proposal'];client.post('/api/input',json={'data':state['data']});client.post('/api/confirm',json={});started=time.monotonic();plan=client.post('/api/plan',json={'use_ai':True,'ai_consent':True});planned=plan.json()
        record('plan',{'http':plan.status_code,'mode':planned.get('ai_trace',{}).get('mode'),'elapsed_s':round(time.monotonic()-started,2),'state':planned})
        if not planned.get('active_revision'):return
        started=time.monotonic();reply=client.post('/api/ai/chat',json={'text':'lebih tinggi 2 cm','ai_consent':True,'replan':True});rows=events(reply);state=client.get('/api/state').json()
        record('revision_chat',{'http':reply.status_code,'status':'changed' if state['data']['request']['H_mm']==120 else 'unchanged','elapsed_s':round(time.monotonic()-started,2),'events':rows,'request':state['data']['request']})
        if args.image and state.get('active_revision'):
            started=time.monotonic();image=client.post('/api/ai/image',json={'ai_consent':True});info=image.json();record('image',{'http':image.status_code,'elapsed_s':round(time.monotonic()-started,2),'info':info})
            if image.status_code==200:
                response=client.get('/api/ai/image',params={'revision_sig':info['input_signature'],'revision_id':info['revision_id'],'image_id':info['image_id']});(out/'illustration.png').write_bytes(response.content)
        if args.persistence:
            state=client.get('/api/state').json()
            saved=client.post('/api/projects/save',json={'name':'Smoke live - pouch 14x12','expected_generation_token':state['generation_token']})
            if saved.status_code!=200:
                record('persistence',{'status':'failed','save_http':saved.status_code});return
            project=saved.json()['project'];client.post('/api/reset',json={'expected_generation_token':state['generation_token']})
            reset=client.get('/api/state').json()
            opened=client.post('/api/projects/open',json={'project_id':project['id'],'expected_generation_token':reset['generation_token']})
            restored=opened.json()
            record('persistence',{'status':'restored' if opened.status_code==200 and restored['data']['request']==state['data']['request'] and restored.get('active_revision',{}).get('validation_report',{}).get('passed') else 'failed','open_http':opened.status_code,'image_preserved':bool(restored.get('image')),'model_calls_for_open':0})
            explanation=client.post('/api/ai/chat',json={'text':'jelaskan langkah 4','ai_consent':True})
            record('catalog_explanation',{'http':explanation.status_code,'status':'grounded','events':events(explanation),'model_calls':0})

if __name__=='__main__':main()
