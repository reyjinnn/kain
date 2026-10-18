import io
import unittest
from PIL import Image
from backend.services import ai
from backend.tests.test_pdf import _consented_split


class ColorCoverageTests(unittest.TestCase):
    def image(self,color):
        out=io.BytesIO();Image.new('RGB',(200,200),color).save(out,format='PNG');return out.getvalue()

    def test_one_color_cannot_verify_a_two_color_front(self):
        data,c,*_=_consented_split()
        result=ai.image_color_check(self.image('#35508F'),data,c)
        self.assertEqual(result['color_status'],'uncertain')
        self.assertIn('semua warna',result['color_warning'])

    def test_both_front_colors_have_evidence(self):
        data,c,*_=_consented_split();out=io.BytesIO();image=Image.new('RGB',(200,200),'#35508F')
        image.paste('#D9C7A5',(100,0,200,200));image.save(out,format='PNG')
        result=ai.image_color_check(out.getvalue(),data,c)
        self.assertEqual(result['color_status'],'close')
        self.assertTrue(all(v>.4 for v in result['color_coverage']))
