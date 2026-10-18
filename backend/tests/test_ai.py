from __future__ import annotations

import base64
import asyncio
import io
import json
import os
import threading
import unittest
from copy import deepcopy
from unittest import mock

from fastapi.testclient import TestClient
from backend import app as service
from backend.design import engine
from backend.services import ai

# Concurrent TestClient requests need a selector loop on Python 3.10/Windows.
if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class PlanContractTests(unittest.TestCase):
    def setUp(self):
        self.data = engine.fixture_data()
        self.sig = engine.signature(self.data)
        self.reference = engine.search_layout(self.data, self.sig, variants=["whole"])["candidate"]
        self.proposal = {
            "action": "generate_pouch_plan", "variant": "whole",
            "patterns": [{"id": p["id"], "width_mm": p["width_mm"], "height_mm": p["height_mm"]} for p in self.reference["patterns"]],
            "placements": deepcopy(self.reference["placements"]),
            "assembly_step_ids": [s["id"] for s in self.reference["assembly_steps"]],
            "notes": {field: "Penjelasan berdasarkan rencana terukur." for field in ai.PACKAGE_FIELDS},
        }

    def test_live_plan_retains_construction_and_passes_independent_validator(self):
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test-key"}), mock.patch.object(ai, "_call_chat_api", return_value=json.dumps(self.proposal)):
            result, trace = ai.generate_plan(self.data, self.reference, self.sig)
        self.assertTrue(trace["live_model_used"])
        self.assertTrue(engine.validate_candidate(self.data, result, self.sig, activation=True)["passed"])
        self.assertEqual(result["assembly_steps"], self.reference["assembly_steps"])
        self.assertEqual(result["ai_package"]["input_signature"], self.sig)

    def test_shrink_extra_fields_and_reordered_construction_rejected(self):
        for change in ("shrink", "extra", "order", "coercion"):
            proposal = deepcopy(self.proposal)
            if change == "shrink":
                proposal["patterns"][0]["width_mm"] = 160
            elif change == "extra":
                proposal["execute"] = "code"
            elif change == "order":
                proposal["assembly_step_ids"].reverse()
            else:
                proposal["patterns"][0]["width_mm"] = "180"
            with self.subTest(change=change), self.assertRaises(ai.ModelResponseError):
                ai.validate_plan(proposal, self.data, self.reference, self.sig)

    def test_ai_coordinates_are_checked_for_edge_defect_overlap_and_fake_material(self):
        for change in ("edge", "defect", "overlap", "fake"):
            proposal = deepcopy(self.proposal)
            if change == "edge":
                proposal["placements"][0]["tx_mm"] = 0
            elif change == "defect":
                proposal["placements"][0].update(material_id="B", tx_mm=28, ty_mm=2)
            elif change == "overlap":
                proposal["placements"][1].update({k: v for k, v in proposal["placements"][0].items() if k != "instance_id"})
            else:
                proposal["placements"][0]["material_id"] = "NONEXISTENT"
            with self.subTest(change=change), self.assertRaises(ai.ModelResponseError):
                ai.validate_plan(proposal, self.data, self.reference, self.sig)

    def test_invalid_ai_plan_preserves_valid_local_fallback(self):
        self.proposal["placements"][0]["material_id"] = "BAD"
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test-key"}), mock.patch.object(ai, "_call_chat_api", return_value=json.dumps(self.proposal)):
            result, trace = ai.generate_plan(self.data, self.reference, self.sig)
        self.assertFalse(trace["live_model_used"])
        self.assertEqual(result, self.reference)
        self.assertNotIn("ai_package", result)

    def test_only_explicit_9router_key_is_used(self):
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "", "CFROUTER_API_KEY": "legacy", "OPENAI_API_KEY": "unrelated"}):
            self.assertFalse(ai.available())
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "text-key", "NINEROUTER_IMAGE_KEY": "image-key"}):
            self.assertEqual(ai.api_key(), "text-key")
            self.assertEqual(ai.image_api_key(), "image-key")
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "fallback-key", "NINEROUTER_IMAGE_KEY": ""}):
            self.assertEqual(ai.image_api_key(), "fallback-key")

    def test_image_url_is_not_fetched_and_corrupt_raster_is_rejected(self):
        for body in ({"data": [{"url": "http://untrusted.example/image.png"}]}, {"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]}):
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(body).encode()
            opener = mock.MagicMock()
            opener.open.return_value = response
            with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test", "SISA_IMAGE_MODEL": "discovered-image-model"}), mock.patch("urllib.request.build_opener", return_value=opener), self.assertRaises((ValueError, OSError)):
                ai.generate_image(self.data, self.reference)
            self.assertEqual(opener.open.call_count, 1)


class SeparateCredentialsTests(unittest.TestCase):
    def test_text_and_image_requests_use_their_own_credentials_and_models(self):
        from PIL import Image

        content = io.BytesIO()
        Image.new("RGB", (1, 1)).save(content, format="PNG")
        text_response, image_response = mock.MagicMock(), mock.MagicMock()
        text_response.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        }).encode()
        image_response.__enter__.return_value.read.return_value = json.dumps({
            "data": [{"b64_json": base64.b64encode(content.getvalue()).decode()}],
        }).encode()
        opener = mock.MagicMock()
        opener.open.side_effect = [text_response, image_response]
        settings = {
            "NINEROUTER_URL": "http://127.0.0.1:20128/v1",
            "NINEROUTER_KEY": "text-test-key", "NINEROUTER_IMAGE_KEY": "image-test-key",
            "SISA_TEXT_MODEL": "combo-ai", "SISA_IMAGE_MODEL": "image",
        }
        with mock.patch.dict(os.environ, settings), mock.patch("urllib.request.build_opener", return_value=opener):
            self.assertEqual(ai._call_chat_api("story", "rules"), "{}")
            self.assertEqual(ai._request_image("pouch", 5)[0], content.getvalue())
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(len(requests), 2)
        for request, route, key, model in zip(requests,
                ["/chat/completions", "/images/generations"],
                ["text-test-key", "image-test-key"], ["combo-ai", "image"]):
            self.assertTrue(request.full_url.endswith(route))
            self.assertEqual(request.get_header("Authorization"), "Bearer " + key)
            self.assertEqual(json.loads(request.data)["model"], model)

    def test_image_configuration_does_not_require_a_text_key(self):
        settings = {
            "NINEROUTER_URL": "http://127.0.0.1:20128/v1", "NINEROUTER_KEY": "",
            "NINEROUTER_IMAGE_KEY": "image-test-key", "SISA_IMAGE_MODEL": "image",
        }
        with mock.patch.dict(os.environ, settings):
            self.assertFalse(ai.available())
            self.assertTrue(ai.image_available())
            with mock.patch.dict(os.environ, {"NINEROUTER_URL": "http://untrusted.example/v1"}):
                self.assertFalse(ai.image_available())
            with mock.patch.dict(os.environ, {"NINEROUTER_IMAGE_KEY": ""}):
                self.assertFalse(ai.image_available())


class ImageRevisionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(service.app)
        self.client.post("/api/reset")
        self.client.post("/api/plan", json={"use_ai": False})

    def state(self):
        return self.client.get("/api/state").json()

    def generate(self):
        return self.client.post("/api/ai/image", json={"ai_consent": True, "expected_generation_token": self.state()["generation_token"]})

    def test_consent_and_checked_plan_required(self):
        self.assertEqual(self.client.post("/api/ai/image", json={}).status_code, 400)
        self.client.post("/api/reset")
        with mock.patch.object(ai, "generate_image") as network:
            self.assertEqual(self.generate().status_code, 400)
            network.assert_not_called()

    def test_image_matches_revision_and_is_inaccessible_after_edit(self):
        info = {"source": "live", "model": "test-image", "media_type": "image/png"}
        with mock.patch.object(ai, "generate_image", return_value=(b"PNG fixture", info)):
            response = self.generate()
        self.assertEqual(response.status_code, 200)
        image = response.json()
        params = {"revision_sig": image["input_signature"], "revision_id": image["revision_id"], "image_id": image["image_id"]}
        self.assertEqual(self.client.get("/api/ai/image", params=params).content, b"PNG fixture")
        self.assertNotIn("PNG fixture", json.dumps(self.state()))
        self.client.post("/api/cancel", json={})
        self.assertEqual(self.client.get("/api/ai/image", params=params).status_code, 409)

    def test_late_image_cannot_restore_result_after_reset(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def slow_image(*args):
            entered.set()
            release.wait(5)
            return b"old", {"source": "live", "model": "test-image", "media_type": "image/png"}
        with mock.patch.object(ai, "generate_image", side_effect=slow_image):
            worker = threading.Thread(target=lambda: results.append(self.generate()))
            worker.start()
            self.assertTrue(entered.wait(5))
            self.client.post("/api/reset")
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].status_code, 409)
        self.assertIsNone(self.state()["image"])

    def test_image_failure_keeps_checked_plan_and_redacts_error(self):
        with mock.patch.object(ai, "generate_image", side_effect=ValueError("sk-private-test-value")):
            response = self.generate()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private", response.text)
        self.assertIsNotNone(self.state()["active_revision"])


if __name__ == "__main__":
    unittest.main()
