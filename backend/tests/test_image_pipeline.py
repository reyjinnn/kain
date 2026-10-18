import io
import threading
import unittest
from unittest import mock
from PIL import Image
from fastapi.testclient import TestClient
from backend import app as service
from backend.design import colors, engine
from backend.services import ai

class ImagePipelineTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(service.app);self.client.post('/api/reset');self.client.post('/api/plan',json={})
        self.state=self.client.get('/api/state').json()
    def image(self,**values):return self.client.post('/api/ai/image',json={'ai_consent':True,**values})
    def test_palette_prompt_color_check_retry_budget(self):
        data,c=self.state['data'],self.state['active_revision'];prompt=ai.image_prompt(data,c)
        self.assertIn('minus 35 degrees',prompt);self.assertIn('elevation 20',prompt)
        target=colors.resolve(next(m['color'] for m in data['materials'] if m['id']==next(p['material_id'] for p in c['placements'] if p['instance_id']=='FRONT')))['hex']
        self.assertIn(target,prompt)
        def png(color):b=io.BytesIO();Image.new('RGB',(200,200),color).save(b,format='PNG');return b.getvalue()
        info={'media_type':'image/png','source':'live'}
        with mock.patch.object(ai,'image_available',return_value=True),mock.patch.object(ai,'_request_image',side_effect=[(png('#A4442F'),info),(png(target),info)]) as network:
            content,details=ai.generate_image(data,c)
        self.assertEqual(network.call_count,2);self.assertEqual(details['color_status'],'close')
        self.assertLessEqual(network.call_args_list[0].args[1]+network.call_args_list[1].args[1],270)
    def test_cache_and_three_regenerations(self):
        with mock.patch.object(ai,'generate_image',return_value=(b'fixture',{'source':'live','media_type':'image/png'})) as model:
            self.assertEqual(self.image().status_code,200);cached=self.image();self.assertEqual(cached.json()['source'],'cached');self.assertEqual(model.call_count,1)
            for _ in range(3):self.assertEqual(self.image(regenerate=True).status_code,200)
            self.assertEqual(self.image(regenerate=True).status_code,429);self.assertEqual(model.call_count,4)
    def test_image_cancel_preserves_plan_and_blocks_late_bytes(self):
        entered,release=threading.Event(),threading.Event();responses=[]
        def slow(*args):entered.set();release.wait(5);return b'old',{'source':'live','media_type':'image/png'}
        with mock.patch.object(ai,'generate_image',side_effect=slow):
            worker=threading.Thread(target=lambda:responses.append(self.image()));worker.start();self.assertTrue(entered.wait(5));self.client.post('/api/ai/image/cancel',json={});release.set();worker.join(5)
        self.assertEqual(responses[0].status_code,409);s=self.client.get('/api/state').json();self.assertIsNotNone(s['active_revision']);self.assertIsNone(s['image'])
