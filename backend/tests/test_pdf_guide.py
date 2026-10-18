import unittest
import re
import io
from PIL import Image
import pymupdf
from backend.pdf import pdf_export
from backend.design import engine
from backend.design import presentation
from backend.design import step_catalog
from backend.tests.test_pdf import _whole, _consented_split, max_fixture, _max_consented_split, _rects, PT

class GuideTests(unittest.TestCase):
    def test_illustration_on_cover_does_not_rasterize_patterns(self):
        data,candidate,sig,_=_whole();buffer=io.BytesIO();Image.new('RGB',(120,100),'#35508F').save(buffer,format='PNG')
        document=pymupdf.open(stream=pdf_export.generate_pdf(data,candidate,sig,illustration=buffer.getvalue()),filetype='pdf')
        self.assertTrue(document[0].get_images())
        self.assertIn('Ilustrasi AI',document[0].get_text())
        for page,part in zip([p for p in document if 'POLA 1:1' in p.get_text()],candidate['patterns']):
            self.assertEqual(page.get_images(),[])
            self.assertTrue(_rects(page,part['width_mm'],part['height_mm']))
    def test_pages_fonts_human_text_and_vectors_at_extremes(self):
        cases=[_whole(),_consented_split(),_whole(max_fixture(),w=160,h=180),_max_consented_split()]
        for data,candidate,sig,_ in cases:
            document=pymupdf.open(stream=pdf_export.generate_pdf(data,candidate,sig),filetype='pdf')
            self.assertEqual(len(document),8 if candidate['variant']=='whole' else 9)
            text='\n'.join(page.get_text() for page in document)
            self.assertIsNone(re.search(r'[{}_]|VAL-|signature|JSON|EUCLIDEAN',text,re.I))
            self.assertIn('×',text)
            self.assertIn('Sebelum mulai',text)
            self.assertEqual(document.metadata['author'],'SISA')
            pages=[page for page in document if 'POLA 1:1' in page.get_text()]
            self.assertEqual(len(pages),len(candidate['patterns']))
            for page,part in zip(pages,candidate['patterns']):
                rect=_rects(page,part['width_mm'],part['height_mm'])[0]
                self.assertAlmostEqual(rect.width/PT,part['width_mm'],delta=.1)
                self.assertAlmostEqual(rect.height/PT,part['height_mm'],delta=.1)
                square=_rects(page,50,50)[0]
                self.assertAlmostEqual(square.width/PT,50,delta=.1)
                self.assertAlmostEqual(square.height/PT,50,delta=.1)
                self.assertLess(rect.y1,square.y0)
            for i,page in enumerate(document,1):
                self.assertIn(f'Hal {i} / {len(document)}',page.get_text())
                self.assertIn('Versi uji coba',page.get_text())
                self.assertAlmostEqual(page.rect.width/PT,210,delta=.1)
                self.assertAlmostEqual(page.rect.height/PT,297,delta=.1)
                self.assertTrue(any('Jakarta' in font[3] for font in page.get_fonts()))
                for drawing in page.get_drawings():
                    self.assertGreaterEqual(drawing['rect'].x0,14*PT)
                    self.assertLessEqual(drawing['rect'].x1,196*PT)
                for block in page.get_text('dict')['blocks']:
                    if 'lines' not in block:continue
                    for line in block['lines']:
                        for span in line['spans']:
                            self.assertGreaterEqual(span['size'],8)
                            self.assertLessEqual(span['bbox'][2],196*PT)
                            self.assertGreaterEqual(span['bbox'][1],0)
            self.assertGreater(sum(len(page.get_drawings()) for page in document if 'Langkah demi langkah' in page.get_text()),15)

    def test_both_kinds_block_stale_and_unreviewed_fabric(self):
        data,candidate,sig,_=_whole()
        for kind in ('guide','technical'):
            for kwargs,code in [({'signature_val':'0'*64},'STALE_REVISION'),({'mode':'fabric_trial'},'CONSTRUCTION_UNREVIEWED')]:
                with self.assertRaises(engine.EngineError) as caught:
                    pdf_export.generate_pdf(data,candidate,kwargs.get('signature_val',sig),mode=kwargs.get('mode','paper_test'),kind=kind)
                self.assertEqual(caught.exception.code,code)

    def test_catalog_keeps_original_operations_and_template_numbers(self):
        for data,c,sig,_ in (_whole(),_consented_split()):
            steps=step_catalog.steps(data,c)
            self.assertEqual([s['id'] for s in steps],[s['id'] for s in c['assembly_steps']])
            self.assertIn('10 mm',next(s['text'] for s in steps if s['id']==4))
            self.assertIn('5 mm',next(s['text'] for s in steps if s['id']==3))
            self.assertIsNone(re.search('[{}]',str(steps[0]['text'])))
