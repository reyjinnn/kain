"""Generate reproducible examples, never physical sewing/print evidence."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.pdf import pdf_export
from backend.tests.test_pdf import _whole,_consented_split
out=Path(__file__).resolve().parents[2]/'output'/'pdf';out.mkdir(parents=True,exist_ok=True)
for name,builder in [('pouch-utuh',_whole),('pouch-sambungan',_consented_split)]:
    data,candidate,sig,_=builder()
    for kind in ('guide','technical'):
        destination=out if kind=='guide' else out.parent.parent/'artifacts'/'phase-4'/'examples'
        destination.mkdir(parents=True,exist_ok=True)
        path=destination/f'SISA-{name}-{kind}.pdf'
        path.write_bytes(pdf_export.generate_pdf(data,candidate,sig,kind=kind))
        print(path.name)
