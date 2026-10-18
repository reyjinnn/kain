"""Five bounded live color samples; opt-in, separate from ordinary tests."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend import config
from backend.design import colors, engine
from backend.services import ai


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    if not args.live:raise SystemExit('Use --live explicitly; ordinary tests never call a provider.')
    config.load_local_config()
    if not ai.image_available():raise SystemExit('Image provider is unavailable.')
    out=Path('artifacts/phase-5/color-calibration');out.mkdir(parents=True,exist_ok=True)
    records=[]
    for color in ('blue','black','beige','red','gray'):
        data=engine.fixture_data()
        for material in data['materials']:
            material.update(color=color,name='Kain '+colors.resolve(color)['name'])
        data=engine.validate_input(data);sig=engine.signature(data)
        result=engine.search_layout(data,sig,variants=['whole'])
        if not result['ok']:raise RuntimeError('Color sample geometry did not pass.')
        candidate=result['candidate']
        print('Generating '+color,flush=True);started=time.monotonic()
        try:
            content,info=ai.generate_image(data,candidate)
            (out/(color+'.png')).write_bytes(content)
            record={'sample':color,'expected_hex':colors.resolve(color)['hex'],'elapsed_s':round(time.monotonic()-started,2),**info,'visual_review':'pending'}
        except (OSError,ValueError,TypeError,KeyError) as exc:
            record={'sample':color,'status':'provider_failed','error_type':type(exc).__name__,'elapsed_s':round(time.monotonic()-started,2)}
        records.append(record)
        (out/'results.json').write_text(ai.redact_text(json.dumps({'threshold_delta_e76':18,'kind':'live_generated_color_samples','limitations':'Central-face heuristic; no panel segmentation or physical fabric calibration.','samples':records},ensure_ascii=False,indent=2)),encoding='utf-8')
        print(color+': '+str(record.get('color_status',record.get('status'))),flush=True)


if __name__=='__main__':main()
