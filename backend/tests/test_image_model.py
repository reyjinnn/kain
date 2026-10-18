import hashlib
import io
import json
import struct
import threading
import unittest
from unittest import mock

from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from backend import app as service
from backend.services import ai, image_model


def illustration(color='#35508F', narrow=False):
    picture = Image.new('RGB', (400, 400), '#f5f1e9')
    draw = ImageDraw.Draw(picture)
    left = 95 if narrow else 70
    draw.polygon([(left, 115), (330, 60), (325, 295), (left+10, 350)], fill=color)
    draw.line([(left, 115), (330, 60)], fill='#152744', width=7)
    for offset in range(145, 300, 6):
        draw.line([(left+12, offset), (315, offset-45)], fill='#4e669a', width=1)
    output = io.BytesIO(); picture.save(output, format='PNG')
    return output.getvalue()


class LocalReconstructionTests(unittest.TestCase):
    def test_colored_pouch_is_separated_from_neutral_shadow_at_frame_edge(self):
        picture = Image.new('RGB', (400, 400), '#f5f1e9')
        draw = ImageDraw.Draw(picture)
        draw.polygon([(0, 320), (400, 280), (400, 400), (0, 400)], fill='#888888')
        draw.polygon([(70, 115), (330, 60), (325, 295), (80, 350)], fill='#702B3A')
        output = io.BytesIO(); picture.save(output, format='PNG')
        model, info = image_model.reconstruct(output.getvalue(), '#8D9097')
        self.assertTrue(model.startswith(b'glTF'))
        self.assertTrue(all(60 <= point[0] <= 340 for point in info['source_quad_px']))

    def test_genuinely_cropped_colored_pouch_still_fails(self):
        picture = Image.new('RGB', (400, 400), '#f5f1e9')
        ImageDraw.Draw(picture).rectangle((0, 70, 300, 350), fill='#702B3A')
        output = io.BytesIO(); picture.save(output, format='PNG')
        with self.assertRaises(ValueError):
            image_model.reconstruct(output.getvalue(), '#8D9097')

    def test_image_pixels_and_shape_drive_self_contained_model(self):
        content = illustration()
        model, info = image_model.reconstruct(content, '#8D9097')
        self.assertEqual(struct.unpack('<III', model[:12]), (0x46546c67, 2, len(model)))
        length = struct.unpack('<I', model[12:16])[0]
        manifest = json.loads(model[20:20+length])
        self.assertEqual(info['image_sha256'], hashlib.sha256(content).hexdigest())
        self.assertFalse(info['physical_dimensions'])
        self.assertTrue(info['back_estimated'])
        self.assertEqual(len(manifest['images']), 2)
        self.assertTrue(all('bufferView' in entry and 'uri' not in entry for entry in manifest['images']))
        self.assertGreater(len(manifest['meshes'][0]['primitives']), 2)
        changed, changed_info = image_model.reconstruct(illustration('#892d20', True), '#8D9097')
        self.assertNotEqual(model, changed)
        self.assertNotEqual(info['source_quad_px'], changed_info['source_quad_px'])

    def test_blank_corrupt_or_cropped_image_is_not_a_success(self):
        for content in (b'broken', illustration()[:20]):
            with self.assertRaises((OSError, ValueError)):
                image_model.reconstruct(content, '#8D9097')
        output = io.BytesIO(); Image.new('RGB', (256, 256), '#ffffff').save(output, format='PNG')
        with self.assertRaises(ValueError):
            image_model.reconstruct(output.getvalue(), '#8D9097')


class ImageModelRoutesTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(service.app)
        self.client.post('/api/reset')
        self.client.post('/api/plan', json={})
        with mock.patch.object(ai, 'generate_image', return_value=(illustration(), {'source': 'live', 'media_type': 'image/png'})):
            self.image = self.client.post('/api/ai/image', json={'ai_consent': True}).json()

    def convert(self):
        return self.client.post('/api/ai/image-model', json={'image_id': self.image['image_id']})

    def read(self, model, client=None):
        return (client or self.client).get('/api/ai/image-model', params={'image_id': model['image_id'], 'model_id': model['model_id'], 'revision_sig': model['input_signature'], 'revision_id': model['revision_id']})

    def test_convert_cache_ownership_and_invalidation(self):
        with mock.patch.object(image_model, 'reconstruct', wraps=image_model.reconstruct) as conversion:
            result = self.convert()
            self.assertEqual(result.status_code, 200, result.text)
            model = result.json()
            self.assertEqual(self.convert().json()['model_id'], model['model_id'])
            self.assertEqual(conversion.call_count, 1)
        response = self.read(model)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'glTF'))
        self.assertEqual(self.read(model, TestClient(service.app)).status_code, 409)
        self.client.post('/api/ai/image/cancel', json={})
        self.assertEqual(self.read(model).status_code, 409)
        self.assertIsNone(self.client.get('/api/state').json()['image_model'])

    def test_late_model_cannot_attach_to_regenerated_illustration(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def slow(*args):
            entered.set(); release.wait(5)
            return b'old-model', {'method': 'test'}
        with mock.patch.object(image_model, 'reconstruct', side_effect=slow):
            worker = threading.Thread(target=lambda: results.append(self.convert()))
            worker.start(); self.assertTrue(entered.wait(5))
            with mock.patch.object(ai, 'generate_image', return_value=(illustration('#884422'), {'source': 'live', 'media_type': 'image/png'})):
                self.client.post('/api/ai/image', json={'ai_consent': True, 'regenerate': True})
            release.set(); worker.join(5)
        self.assertEqual(results[0].status_code, 409)
        self.assertIsNone(self.client.get('/api/state').json()['image_model'])

    def test_failure_and_unavailable_leave_plan_current(self):
        with mock.patch.object(image_model, 'reconstruct', side_effect=ValueError('blank')):
            self.assertEqual(self.convert().status_code, 422)
        with mock.patch.object(image_model, 'available', return_value=False):
            self.assertEqual(self.convert().status_code, 503)
        self.assertIsNotNone(self.client.get('/api/state').json()['active_revision'])
