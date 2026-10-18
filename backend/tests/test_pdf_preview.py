import io
import threading
import unittest
from unittest import mock
from PIL import Image
from fastapi.testclient import TestClient
from backend import app as service
import pymupdf

class PdfPreviewTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(service.app);self.client.post('/api/reset');self.client.post('/api/plan',json={})
        self.state=self.client.get('/api/state').json()
    def test_first_three_png_pages_and_bounds(self):
        for page in (1,2,3):
            response=self.client.get('/api/export-preview',params={'revision_sig':self.state['signature'],'page':page})
            self.assertEqual(response.status_code,200);self.assertIn('image/png',response.headers['content-type'])
            with Image.open(io.BytesIO(response.content)) as image:self.assertGreater(image.width,700);self.assertGreater(image.height,1000)
        self.assertEqual(self.client.get('/api/export-preview',params={'revision_sig':self.state['signature'],'page':4}).status_code,400)
    def test_stale_preview_is_blocked(self):
        self.client.post('/api/cancel',json={})
        self.assertEqual(self.client.get('/api/export-preview',params={'revision_sig':self.state['signature']}).status_code,409)
    def test_edit_during_raster_render_rejects_late_preview(self):
        entered,release=threading.Event(),threading.Event();responses=[];original=pymupdf.open
        def slow(*args,**kwargs):entered.set();release.wait(5);return original(*args,**kwargs)
        with mock.patch.object(pymupdf,'open',side_effect=slow):
            worker=threading.Thread(target=lambda:responses.append(self.client.get('/api/export-preview',params={'revision_sig':self.state['signature']})));worker.start();self.assertTrue(entered.wait(5));self.client.post('/api/cancel',json={});release.set();worker.join(5)
        self.assertEqual(responses[0].status_code,409)
        self.assertIsNone(self.client.get('/api/state').json()['active_revision'])
