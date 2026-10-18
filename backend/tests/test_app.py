from __future__ import annotations

import json
import os
import threading
import time
import unittest
import uuid
from copy import deepcopy
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend import app as app_module
from backend.design import engine
from backend.services import ai


class ApiBase(unittest.TestCase):
    def setUp(self):
        self._saved_env = dict(os.environ)
        self._orig_search = engine.search_layout
        for name in ("NINEROUTER_KEY", "NINEROUTER_URL", "SISA_IMAGE_MODEL", "CFROUTER_API_KEY", "OPENAI_API_KEY", "SISA_AI_ENABLED", "SISA_AI_MODEL", "SISA_AI_ENDPOINT"):
            os.environ.pop(name, None)
        self.client = TestClient(app_module.app)
        reset = self.client.post("/api/reset")
        self.assertEqual(reset.status_code, 200, reset.text)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        engine.search_layout = self._orig_search
        os.environ.clear()
        os.environ.update(self._saved_env)

    def tearDown(self):
        from backend.sessions.sessions import registry_for
        registry = registry_for({"headers": [(b"host", b"testserver")], "client": ("testclient", 50000)})
        cookie = self.client.cookies.get("sisa_sid")
        if cookie:
            entry = registry.entries.get(registry.owner(cookie))
            if entry:
                with entry.session.lock:
                    entry.session.planning = False

    def state(self):
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def set_input(self, canonical, token=None, expect=200):
        data = token or self.state()
        payload = {"data": deepcopy(canonical)}
        if data.get("generation_token") is not None:
            payload["expected_generation_token"] = data["generation_token"]
        response = self.client.post("/api/input", json=payload)
        if expect is not None:
            self.assertEqual(response.status_code, expect, response.text)
        return response

    def plan(self, token=None, **flags):
        data = token or self.state()
        body = {"expected_generation_token": data["generation_token"], **flags}
        return self.client.post("/api/plan", json=body)

    def plan_ok(self, **flags):
        response = self.plan(**flags)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def avail(self, locked=()):
        data = deepcopy(self.state()["data"])
        for material in data["materials"]:
            material["available"] = material["id"] not in locked
            if material["id"] in locked:
                material["confirmed"] = False
        return data

    def remove_a(self, **kwargs):
        return self.set_input(self.avail(locked=("A",)), **kwargs)


class ConfigTests(ApiBase):
    def test_config_reads_repository_root(self):
        with mock.patch.object(Path, "is_file", autospec=True, return_value=False) as is_file:
            app_module.config.load_local_config()
        is_file.assert_called_once_with(Path(__file__).resolve().parents[2] / ".env.local")

    def test_config_shape(self):
        config = self.client.get("/api/config").json()
        self.assertIn("ai_available", config)
        self.assertEqual(config["construction_status"], engine.template.REVIEW_STATUS)
        self.assertEqual(config["template_id"], engine.template.TEMPLATE_ID)
        self.assertEqual(config["template_version"], engine.template.TEMPLATE_VERSION)
        self.assertEqual(config["config_version"], engine.CONFIG["version"])
        self.assertEqual(config["w_range_mm"], list(engine.template.W_RANGE))
        self.assertEqual(config["h_range_mm"], list(engine.template.H_RANGE))

    def test_config_respects_env(self):
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "k", "SISA_AI_MODEL": "image"}):
            self.assertTrue(app_module.ai.available())
        self.assertFalse(app_module.ai.available())

    def test_static_routes(self):
        for path, content_type in (("/", "text/html"), ("/static/src/app.js", "application/javascript"),
                                   ("/static/styles/tokens.css", "text/css"), ("/static/styles/base.css", "text/css"),
                                   ("/static/styles/components.css", "text/css"), ("/static/styles/pages.css", "text/css"),
                                   ("/static/styles/projects.css", "text/css"), ("/static/styles/atelier.css", "text/css")):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(content_type, response.headers["content-type"])

    def test_private_sources_not_exposed(self):
        for path in ("/ai.py", "/engine.py", "/backend/ai.py", "/references/demo-fixtures.json", "/app.py", "/demo-fixtures.json", "/backend/app.py"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_untrusted_host_rejected(self):
        for host in ("evil.example.com", "not-local:9999", "", "bad host"):
            response = self.client.get("/api/state", headers={"host": host})
            self.assertEqual(response.status_code, 400, host)

    def test_allowed_hosts(self):
        for host in ("localhost", "127.0.0.1", "testserver", "localhost:8000", "127.0.0.1:8080"):
            self.assertEqual(self.client.get("/api/state", headers={"host": host}).status_code, 200, host)

    def test_origin_same_host_allowed(self):
        self.assertEqual(self.client.get("/api/state", headers={"origin": "http://testserver"}).status_code, 200)
        self.assertEqual(self.client.get("/api/state", headers={"host": "localhost", "origin": "http://localhost"}).status_code, 200)
        self.assertEqual(self.client.get("/api/state", headers={"host": "127.0.0.1:8080", "origin": "http://127.0.0.1:8080"}).status_code, 200)

    def test_cross_origin_port_rejected(self):
        for origin in ("http://localhost:9999", "http://attacker.example.com", "http://127.0.0.1:9999"):
            self.assertEqual(self.client.get("/api/state", headers={"origin": origin}).status_code, 403, origin)

    def test_cross_site_fetch_metadata_rejected(self):
        response = self.client.get("/api/state", headers={"sec-fetch-site": "cross-site"})
        self.assertEqual(response.status_code, 403)

    def test_no_wildcard_cors(self):
        response = self.client.get("/api/state", headers={"origin": "http://testserver"})
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_body_limit(self):
        response = self.client.post("/api/input", content=b"{}", headers={"content-length": "70000", "content-type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"]["code"], "PAYLOAD_TOO_LARGE")
        response = self.client.post("/api/input", content=b"x" * 70000, headers={"content-type": "application/octet-stream"})
        self.assertEqual(response.status_code, 413)

    def test_invalid_content_length_rejected(self):
        response = self.client.post("/api/input", content=b"{}", headers={"content-length": "abc", "content-type": "application/json"})
        self.assertEqual(response.status_code, 400)

    def test_api_no_store_and_errors_sanitized(self):
        planned = self.plan_ok()
        export = self.client.get("/api/export-pdf", params={"revision_sig": planned["signature"], "mode": "paper_test"})
        self.assertEqual(export.headers["cache-control"], "no-store")
        state = self.client.get("/api/state")
        self.assertEqual(state.headers["cache-control"], "no-store")
        response = self.client.post("/api/plan", json={"expected_generation_token": 999999})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("traceback", json.dumps(response.json()).lower())

    def test_strict_input_payload(self):
        data = self.state()
        for body in ({"data": data["data"], "unknown": 1}, {"expected_generation_token": 0, "data": data["data"]}, {"expected_generation_token": "x", "data": data["data"]}):
            response = self.client.post("/api/input", json=body)
            self.assertEqual(response.status_code, 422, body)
            detail = response.json()["detail"]
            self.assertEqual(detail["code"], "INVALID_INPUT")
            self.assertIn("message", detail)

    def test_validation_error_shape(self):
        response = self.client.post("/api/cancel", json={"expected_generation_token": 0})
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "INVALID_INPUT")
        self.assertIn("fields", detail)


class InputFlowTests(ApiBase):
    def test_plan_accepts_missing_and_empty_body(self):
        self.assertEqual(self.client.post("/api/plan").status_code, 200)
        self.client.post("/api/reset")
        self.assertEqual(self.client.post("/api/plan", json={}).status_code, 200)

    def test_unknown_fields_in_data_rejected(self):
        data = self.state()
        bad = deepcopy(data["data"])
        bad["request"]["nested"] = 1
        response = self.set_input(bad, data, expect=None)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_INPUT")
        self.assertTrue(self.state()["stale"])

    def test_material_geometry_edit_clears_confirmation(self):
        data = self.state()
        self.assertTrue(data["data"]["request"]["confirmed"])
        bad = deepcopy(data["data"])
        bad["materials"][0]["outer_mm"] = [[0, 0], [185, 0], [185, 140], [0, 140]]
        result = self.set_input(bad, data).json()
        self.assertTrue(result["data"]["request"]["confirmed"])
        self.assertFalse(result["data"]["materials"][0]["confirmed"])
        self.assertTrue(all(m["confirmed"] for m in result["data"]["materials"] if m["id"] != "A"))

    def test_confirmation_cannot_be_forged_after_edit(self):
        data = self.state()
        bad = deepcopy(data["data"])
        bad["request"]["confirmed"] = True
        bad["materials"][0]["color"] = "green"
        for material in bad["materials"]:
            material["confirmed"] = True
        result = self.set_input(bad, data).json()
        self.assertFalse(result["data"]["materials"][0]["confirmed"])
        self.assertTrue(all(m["confirmed"] for m in result["data"]["materials"] if m["id"] != "A"))
        self.assertTrue(result["data"]["request"]["confirmed"])

    def test_availability_change_alone_keeps_other_confirmations(self):
        data = self.state()
        result = self.set_input(self.avail(locked=("D",)), data).json()
        self.assertTrue(result["data"]["request"]["confirmed"])
        self.assertFalse(next(m for m in result["data"]["materials"] if m["id"] == "D")["confirmed"])
        self.assertTrue(next(m for m in result["data"]["materials"] if m["id"] == "A")["confirmed"])

    def test_confirm_sets_flags_and_invalidates(self):
        planned = self.plan_ok()
        data = self.state()
        response = self.client.post("/api/confirm", json={"expected_generation_token": data["generation_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["data"]["request"]["confirmed"])
        self.assertTrue(all(m["confirmed"] for m in result["data"]["materials"] if m["available"]))
        self.assertIsNone(result["candidate"])
        self.assertIsNone(result["active_revision"])
        self.assertIsNone(result["consent"])
        self.assertTrue(result["stale"])

    def test_confirm_without_changes_same_generation_but_no_outputs(self):
        self.plan_ok()
        before = self.state()
        response = self.client.post("/api/confirm", json={"expected_generation_token": before["generation_token"]})
        result = response.json()
        self.assertEqual(result["generation_token"], before["generation_token"])
        self.assertIsNone(result["candidate"])
        self.assertIsNone(result["active_revision"])
        self.assertEqual(result["data"], before["data"])

    def test_stale_input_token_rejected(self):
        data = self.state()
        cancel = self.client.post("/api/cancel", json={"expected_generation_token": data["generation_token"]})
        self.assertEqual(cancel.status_code, 200)
        response = self.set_input(data["data"], data, expect=None)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "STALE_REVISION")

    def test_invalid_edit_invalidates_previous_output(self):
        planned = self.plan_ok()
        sig = planned["signature"]
        data = self.state()
        bad = deepcopy(data["data"])
        bad["request"]["W_mm"] = 999
        response = self.set_input(bad, data, expect=None)
        self.assertEqual(response.status_code, 422)
        after = self.state()
        self.assertIsNone(after["candidate"])
        self.assertIsNone(after["active_revision"])
        self.assertTrue(after["stale"])
        self.assertEqual(after["phase"], "stale")
        export = self.client.get("/api/export-pdf", params={"revision_sig": sig, "mode": "paper_test"})
        self.assertEqual(export.status_code, 409)

    def test_stale_without_active_revision(self):
        self.plan_ok()
        data = self.state()
        self.client.post("/api/confirm", json={"expected_generation_token": data["generation_token"]})
        result = self.state()
        self.assertTrue(result["stale"])
        self.assertIsNone(result["active_revision"])

    def test_cancel_invalidates_immediately(self):
        planned = self.plan_ok()
        data = self.state()
        response = self.client.post("/api/cancel", json={"expected_generation_token": data["generation_token"]})
        result = response.json()
        self.assertGreater(result["generation_token"], data["generation_token"])
        self.assertIsNone(result["candidate"])
        self.assertIsNone(result["active_revision"])
        self.assertTrue(result["stale"])
        self.assertNotEqual(result["job_token"], planned["job_token"])

    def test_reset_replaces_session(self):
        planned = self.plan_ok()
        old_id = planned["session_id"]
        result = self.client.post("/api/reset", json={"expected_generation_token": planned["generation_token"]}).json()
        self.assertNotEqual(result["session_id"], old_id)
        self.assertNotEqual(result["job_token"], planned["job_token"])
        self.assertFalse(result["stale"])


class RevisionLifecycleTests(ApiBase):
    def test_whole_activates_and_signature_is_dynamic(self):
        result = self.plan_ok()
        candidate = result["candidate"]
        revision = result["active_revision"]
        self.assertEqual(candidate["variant"], "whole")
        self.assertEqual(revision["design_revision_id"], candidate["design_revision_id"])
        self.assertTrue(candidate["validation_report"]["passed"])
        self.assertEqual(candidate["input_signature"], result["signature"])
        self.assertEqual(candidate["generation_token"], result["generation_token"])
        self.assertEqual(candidate["job_token"], result["job_token"])
        self.assertIsNone(revision["parent_id"])
        self.assertTrue(candidate["search"])
        self.assertTrue(candidate["validation_report"]["search"])
        self.assertNotEqual(candidate["design_revision_id"], result["signature"])
        self.assertIn(candidate["validation_report"]["input_signature"], (result["signature"],))
        for check in candidate["validation_report"]["checks"]:
            self.assertEqual(check["status"], "pass")

    def test_export_paper_200_fabric_400(self):
        result = self.plan_ok()
        sig = result["signature"]
        revision = result["active_revision"]["design_revision_id"]
        self.assertEqual(result["data"]["request"]["confirmed"], True)
        self.assertEqual(engine.t.REVIEW_STATUS, "draft")
        paper = self.client.get("/api/export-pdf", params={"revision_sig": sig, "mode": "paper_test"})
        self.assertEqual(paper.status_code, 200, paper.text)
        self.assertGreater(len(paper.content), 2000)
        self.assertEqual(paper.headers["X-SISA-Revision"], revision)
        self.assertEqual(paper.headers["X-Input-Signature"], sig)
        fabric = self.client.get("/api/export-pdf", params={"revision_sig": sig, "mode": "fabric_trial"})
        self.assertEqual(fabric.status_code, 400, fabric.text)
        self.assertEqual(fabric.json()["detail"]["code"], "CONSTRUCTION_UNREVIEWED")

    def test_export_requires_active_revision(self):
        data = self.state()
        response = self.client.get("/api/export-pdf", params={"revision_sig": data["signature"], "mode": "paper_test"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "NO_ACTIVE_REVISION")

    def test_export_mode_validated(self):
        result = self.plan_ok()
        response = self.client.get("/api/export-pdf", params={"revision_sig": result["signature"], "mode": "preview"})
        self.assertEqual(response.status_code, 422)

    def test_export_requires_current_signature(self):
        result = self.plan_ok()
        response = self.client.get("/api/export-pdf", params={"revision_sig": "0" * 64, "mode": "paper_test"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "STALE_REVISION")

    def test_material_removal_reaches_split_proposal(self):
        self.remove_a()
        data = self.state()
        result = self.plan_ok()
        candidate = result["candidate"]
        self.assertEqual(candidate["variant"], "front_split_center")
        report = candidate["validation_report"]
        self.assertTrue(report["geometry_passed"])
        self.assertFalse(report["passed"])
        status = {c["id"]: c["status"] for c in report["checks"]}
        self.assertEqual(status["VAL-08"], "pending")
        self.assertEqual(data["data"], result["data"])
        self.assertIsNone(result["active_revision"])
        self.assertEqual(result["phase"], "awaiting_change_acceptance")
        material_ids = [pl["material_id"] for pl in candidate["placements"]]
        self.assertNotIn("A", material_ids)

    def test_g2_split_accept_then_export_paper(self):
        self.remove_a()
        result = self.plan_ok()
        proposal = result["candidate"]
        accept = self.client.post("/api/accept-split", json={"accepted": True, "additional_seams": 1, "expected_generation_token": result["generation_token"], "candidate_signature": result["signature"]})
        self.assertEqual(accept.status_code, 200, accept.text)
        accepted = accept.json()
        active = accepted["active_revision"]
        self.assertEqual(active["variant"], "front_split_center")
        self.assertEqual(active["proposal_signature"], result["signature"])
        self.assertEqual(active["change_acceptance"], {"accepted": True, "additional_seams": 1, "variant": "front_split_center"})
        self.assertEqual(active["input_signature"], accepted["signature"])
        self.assertEqual(active["parent_id"], proposal["design_revision_id"])
        self.assertEqual(len(active["patterns"]), 3)
        self.assertEqual(active["cut_area_mm2"], 53200)
        self.assertEqual(active["extra_raw_sew_length_mm"], 140)
        export = self.client.get("/api/export-pdf", params={"revision_sig": accepted["signature"], "mode": "paper_test"})
        self.assertEqual(export.status_code, 200, export.text)
        statuses = {c["id"]: c["status"] for c in active["validation_report"]["checks"]}
        self.assertEqual(statuses["VAL-08"], "pass")
        front = sorted(pl["instance_id"] for pl in active["placements"] if pl["instance_id"].startswith("FRONT"))
        self.assertEqual(front, ["FRONT_L", "FRONT_R"])
        back = next(pl for pl in active["placements"] if pl["instance_id"] == "BACK")
        self.assertEqual(back["material_id"], "B")

    def test_g3_area_failure(self):
        data = self.state()
        locked = self.avail(locked=("A", "D"))
        self.set_input(locked, data)
        result = self.state()
        response = self.plan(token=result)
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "AREA_LOWER_BOUND_FAIL")
        self.assertIn("search", detail)
        state = self.state()
        self.assertEqual(state["failure"]["code"], "AREA_LOWER_BOUND_FAIL")
        self.assertEqual(state["search"]["available_area_mm2"], 48140)
        self.assertEqual(state["search"]["minimum_area_mm2"], 50400)
        self.assertEqual(state["search"]["allowed_variants"], ["whole", "front_split_center"])
        self.assertIsNone(state["candidate"])
        self.assertIsNone(state["active_revision"])
        self.assertIsInstance(uuid.UUID(state["job_token"]), uuid.UUID)

    def test_split_failure_not_global_area(self):
        data = self.state()
        locked = self.avail(locked=("A", "B", "C", "D"))
        self.set_input(locked, data)
        response = self.plan(token=self.state())
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "AREA_LOWER_BOUND_FAIL")
        state = self.state()
        self.assertEqual(state["search"]["available_area_mm2"], 0)
        self.assertEqual(state["search"]["minimum_area_mm2"], 50400)

    def test_plan_unconfirmed_blocked(self):
        data = self.state()
        canonical = deepcopy(data["data"])
        canonical["request"]["confirmed"] = False
        self.set_input(canonical, data)
        response = self.plan(token=self.state())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "UNCONFIRMED_INPUT")


class SplitAcceptanceTests(ApiBase):
    def queue_split(self):
        self.remove_a()
        return self.plan_ok()

    def test_pending_split_blocks_export(self):
        result = self.queue_split()
        response = self.client.get("/api/export-pdf", params={"revision_sig": result["signature"], "mode": "paper_test"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "NO_ACTIVE_REVISION")

    def test_accept_with_wrong_signature_rejected(self):
        result = self.queue_split()
        wrong = self.client.post("/api/accept-split", json={"accepted": True, "additional_seams": 1, "expected_generation_token": result["generation_token"], "candidate_signature": "b" * 64})
        self.assertEqual(wrong.status_code, 409)
        self.assertEqual(wrong.json()["detail"]["code"], "STALE_REVISION")

    def test_invalid_additional_seams_rejected(self):
        result = self.queue_split()
        for seams in (0, 2, "1", 1.0):
            response = self.client.post("/api/accept-split", json={"accepted": True, "additional_seams": seams, "expected_generation_token": result["generation_token"]})
            self.assertEqual(response.status_code, 422, seams)

    def test_reject_clears_without_restoring(self):
        result = self.queue_split()
        response = self.client.post("/api/accept-split", json={"accepted": False, "additional_seams": 1, "expected_generation_token": result["generation_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        rejected = response.json()
        self.assertIsNone(rejected["candidate"])
        self.assertIsNone(rejected["active_revision"])
        self.assertIsNone(rejected["consent"])
        self.assertEqual(next(m for m in rejected["data"]["materials"] if m["id"] == "A")["available"], False)
        self.assertGreater(rejected["generation_token"], result["generation_token"])
        self.assertNotEqual(rejected["job_token"], result["job_token"])

    def test_accept_without_pending_split(self):
        result = self.plan_ok()
        response = self.client.post("/api/accept-split", json={"accepted": True, "additional_seams": 1, "expected_generation_token": result["generation_token"]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "NO_PENDING_SPLIT")

    def test_accept_stale_token_rejected(self):
        result = self.queue_split()
        data = self.state()
        self.client.post("/api/cancel", json={"expected_generation_token": data["generation_token"]})
        response = self.client.post("/api/accept-split", json={"accepted": True, "additional_seams": 1, "expected_generation_token": result["generation_token"]})
        self.assertEqual(response.status_code, 409)

    def test_accept_does_not_revive_deleted_fabric(self):
        result = self.queue_split()
        accepted = self.client.post("/api/accept-split", json={"accepted": True, "additional_seams": 1, "expected_generation_token": result["generation_token"], "candidate_signature": result["signature"]}).json()
        self.assertEqual(next(m for m in accepted["data"]["materials"] if m["id"] == "A")["available"], False)
        self.assertTrue(next(m for m in accepted["data"]["materials"] if m["id"] == "A")["confirmed"] is False)


class AiBoundaryTests(ApiBase):
    def test_ai_request_requires_consent(self):
        response = self.client.post("/api/ai/request", json={"text": "depan biru", "ai_consent": False})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "AI_CONSENT_REQUIRED")

    def test_ai_request_can_draft_before_inventory_confirmation(self):
        data = self.state()
        canonical = deepcopy(data["data"])
        canonical["materials"][0]["confirmed"] = False
        self.set_input(canonical, data)
        state = self.state()
        response = self.client.post("/api/ai/request", json={"text": "buat 500 mm", "ai_consent": True, "expected_generation_token": state["generation_token"]})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["proposal"])
        self.assertEqual(response.json()["status"], "unavailable")

    def test_ai_request_offline_fallback_preserves_dimensions(self):
        data = self.state()
        response = self.client.post("/api/ai/request", json={"text": "buat 500 mm dan variasi diagonal", "ai_consent": True, "expected_generation_token": data["generation_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["trace"]["live_model_used"])
        self.assertEqual(body["trace"]["mode"], "fallback")
        self.assertEqual(body["trace"]["call_count"], 0)
        # v2: unavailable AI must not present the previous request as a proposal.
        self.assertIsNone(body["proposal"])
        self.assertEqual(body["status"], "unavailable")
        self.assertEqual(self.state()["data"]["request"], data["data"]["request"])
        self.assertEqual(self.state()["generation_token"], data["generation_token"])

    def test_ai_request_never_mutates_state(self):
        before = deepcopy(self.state()["data"])
        data = self.state()
        response = self.client.post("/api/ai/request", json={"text": "ubah ukuran", "ai_consent": True, "expected_generation_token": data["generation_token"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.state()["data"], before)

    def test_ai_propose_offline_handled(self):
        data = self.state()
        response = self.client.post("/api/ai/propose", json={"ai_consent": True, "expected_generation_token": data["generation_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["trace"]["live_model_used"])
        self.assertIsNone(body["proposal"])

    def test_text_length_bounded(self):
        response = self.client.post("/api/ai/request", json={"text": "x" * 2001, "ai_consent": True})
        self.assertEqual(response.status_code, 422)

    def test_live_ai_never_called_offline(self):
        with mock.patch.object(app_module, "ai", wraps=app_module.ai) as watcher:
            watcher.propose_request.side_effect = AssertionError
            watcher.propose_supported_change.side_effect = AssertionError
            watcher.available.side_effect = lambda: False
            self.remove_a()
            self.plan_ok(use_ai=True, ai_consent=True)
            response = self.client.post("/api/ai/request", json={"text": "depan biru", "ai_consent": True})
            self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["trace"]["live_model_used"])

    def test_split_pending_with_mocked_live_proposal(self):
        def fake_propose(data, failure=None):
            return (
                {"action": "propose_supported_change", "variant": "front_split_center", "material_ids": [m["id"] for m in data["materials"] if m["available"] and m["confirmed"]]},
                {"live_model_used": True, "call_count": 1, "input_signature": "x", "model_call_latency_s": 0.4},
            )

        with mock.patch.object(app_module.ai, "available", return_value=True), mock.patch.object(app_module.ai, "propose_supported_change", side_effect=fake_propose):
            self.remove_a()
            result = self.plan_ok(use_ai=True, ai_consent=True)
        trace = result["ai_trace"]
        self.assertEqual(trace["live_model_used"], True)
        self.assertGreaterEqual(trace["call_count"], 1)
        self.assertLessEqual(trace["call_count"], 2)
        self.assertIn("input_signature", trace)
        self.assertEqual(trace["input_signature"], result["signature"])
        self.assertLessEqual(trace["model_call_latency_s"], app_module.ACTION_DEADLINE_S)
        candidate = result["candidate"]
        self.assertEqual(candidate["variant"], "front_split_center")
        self.assertFalse(candidate["validation_report"]["passed"])
        self.assertEqual([c["status"] for c in candidate["validation_report"]["checks"] if c["id"] == "VAL-08"], ["pending"])
        self.assertEqual({pl["material_id"] for pl in candidate["placements"]}, {"B", "C", "D"})

    def test_invalid_live_proposal_falls_back(self):
        def fake_propose(data, failure=None):
            return ({"action": "propose_supported_change", "variant": "diagonal_impossible", "material_ids": ["Z"], "shrink": 1}, {"live_model_used": True, "call_count": 1})

        with mock.patch.object(app_module.ai, "available", return_value=True), mock.patch.object(app_module.ai, "propose_supported_change", side_effect=fake_propose):
            self.remove_a()
            result = self.plan_ok(use_ai=True, ai_consent=True)
        trace = result["ai_trace"]
        self.assertFalse(trace["live_model_used"])
        self.assertEqual(trace["code"], "AI_UNAVAILABLE")
        self.assertEqual(trace["call_count"], 1)
        self.assertEqual(result["candidate"]["variant"], "front_split_center")

    def test_trace_has_no_secret_or_prompt(self):
        result = self.plan_ok(use_ai=True, ai_consent=True)
        response = self.client.post("/api/ai/request", json={"text": "depan biru", "ai_consent": True})
        self.assertEqual(response.status_code, 200)
        trace = response.json()["trace"]
        self.assertIsNone(trace["model"])
        self.assertNotIn("sk-", json.dumps(response.json()))
        self.assertNotIn("OPENAI_API_KEY", json.dumps(response.json()))

    def test_9router_messages_request_format(self):
        recorded_req = []

        class DummyResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, *args):
                body = {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "action": "propose_request",
                            "request": {"W_mm": 150, "H_mm": 110, "max_front_splits": None,
                                        "allow_mixed": None, "front_color": None, "fewer_seams_first": None},
                            "reply": "Aku menangkap kebutuhan kamu.",
                            "understood": [{"field": "W_mm", "value": 150, "from": "150x110 mm"},
                                           {"field": "H_mm", "value": 110, "from": "150x110 mm"}],
                            "assumptions": [], "questions": [], "warnings": [],
                        }),
                    }],
                }
                return json.dumps({"choices": [{"message": {"content": body["content"][0]["text"]}, "finish_reason": "stop"}]}).encode("utf-8")

        class DummyOpener:
            def open(self, req, timeout=None):
                recorded_req.append(req)
                return DummyResponse()

        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test-cf-key-123", "SISA_AI_MODEL": "gpt-6-sol"}), \
             mock.patch("urllib.request.build_opener", return_value=DummyOpener()):
            data = self.state()
            proposal, trace = ai.propose_request("buat pouch 150x110 mm", data["data"])
            self.assertEqual(proposal["proposal"]["W_mm"], 150.0)
            self.assertEqual(proposal["proposal"]["H_mm"], 110.0)
            self.assertTrue(trace["live_model_used"])
            self.assertEqual(trace["model"], "gpt-6-sol")

        self.assertEqual(len(recorded_req), 1)
        req = recorded_req[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:20128/v1/chat/completions")
        self.assertEqual(req.headers.get("Authorization"), "Bearer test-cf-key-123")
        self.assertNotIn("Anthropic-version", req.headers)
        self.assertEqual(req.headers.get("Content-type"), "application/json")
        body_payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body_payload["model"], "gpt-6-sol")
        self.assertEqual(body_payload["max_tokens"], ai.MAX_TOKENS)
        self.assertIn("messages", body_payload)
        self.assertEqual(body_payload["messages"][0]["role"], "system")

    def test_9router_key_is_not_borrowed_from_other_services(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-other-service", "SISA_AI_KEY": "test-other-key"}):
            self.assertFalse(ai.available())
            self.assertIsNone(ai.api_key())
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test-router-key"}):
            self.assertTrue(ai.available())
            self.assertEqual(ai.api_key(), "test-router-key")

    def test_9router_valid_change_reaches_checked_split(self):
        self.remove_a()
        payload = {"action": "propose_supported_change", "variant": "front_split_center", "material_ids": ["B", "C", "D"]}
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test-router-key"}), mock.patch.object(ai, "_call_chat_api", return_value=json.dumps(payload)) as network:
            result = self.plan_ok(use_ai=True, ai_consent=True)
        self.assertEqual(network.call_count, 2)
        self.assertTrue(result["ai_trace"]["live_model_used"])
        self.assertEqual(result["candidate"]["variant"], "front_split_center")
        self.assertTrue(result["candidate"]["validation_report"]["geometry_passed"])
        self.assertFalse(result["candidate"]["validation_report"]["passed"])

    def test_9router_request_rejects_unknown_fields_and_coercions(self):
        data = self.state()["data"]
        valid = {"action": "propose_request", "request": {"W_mm": 150, "H_mm": 110, "max_front_splits": None,
                 "allow_mixed": None, "front_color": None, "fewer_seams_first": None}, "reply": "Usulan untuk kamu.",
                 "understood": [{"field": "W_mm", "value": 150, "from": "150x110"}, {"field": "H_mm", "value": 110, "from": "150x110"}],
                 "assumptions": [], "questions": [], "warnings": []}
        variants = [{**valid, "execute": "arbitrary"}, *[{**valid, "request": {**valid["request"], field: value}}
                     for field, value in (("allow_mixed", "false"), ("max_front_splits", 1.5), ("W_mm", "150"), ("W_mm", float("nan")))],
                    {**valid, "action": "explain_checked_result"}]
        for payload in variants:
            with self.subTest(payload=payload), mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test-router-key"}), mock.patch.object(ai, "_call_chat_api", return_value=json.dumps(payload)):
                proposal, trace = ai.propose_request("Permintaan", data)
            self.assertFalse(trace["live_model_used"])
            self.assertEqual(proposal, ai.unavailable_request())
            self.assertEqual(data, self.state()["data"])

    def test_9router_change_rejects_shrink_and_fake_material(self):
        data = engine.fixture_data(unavailable=["A"])
        valid = {"action": "propose_supported_change", "variant": "front_split_center", "material_ids": ["B", "C", "D"]}
        for payload in ({**valid, "W_mm": 150}, {**valid, "material_ids": ["Z"]}, {**valid, "material_ids": ["A"]}, {**valid, "variant": "diagonal"}):
            with self.subTest(payload=payload), mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test-router-key"}), mock.patch.object(ai, "_call_chat_api", return_value=json.dumps(payload)):
                proposal, trace = ai.propose_supported_change(data)
            self.assertFalse(trace["live_model_used"])
            self.assertEqual(proposal, valid)

    def test_9router_failures_do_not_expose_secret(self):
        key = "test-private-key-that-must-not-appear"
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": key}), mock.patch.object(ai, "_call_chat_api", side_effect=ValueError(key)), mock.patch("logging.Logger.warning") as logs:
            proposal, trace = ai.propose_request("Pouch", self.state()["data"])
        self.assertFalse(trace["live_model_used"])
        self.assertNotIn(key, json.dumps({"proposal": proposal, "trace": trace}))
        logs.assert_not_called()

    def test_9router_json_and_endpoint_are_strict(self):
        for text in ('{"action":"a","action":"b"}', '{"size": NaN}', 'prefix {"action":"a"}', '[{"action":"a"}]'):
            with self.subTest(text=text), self.assertRaises((ValueError, TypeError)):
                ai._extract_json_object(text)
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test-key", "NINEROUTER_URL": "http://other.invalid/v1"}):
            self.assertFalse(ai.available())
        with self.assertRaises(ai.ModelResponseError):
            ai.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.invalid")


class ConsentShapeTests(ApiBase):
    def test_consent_exact_shape(self):
        self.remove_a()
        result = self.plan_ok()
        response = self.client.post("/api/accept-split", json={"accepted": True, "additional_seams": 1, "expected_generation_token": result["generation_token"], "candidate_signature": result["signature"]})
        self.assertEqual(response.status_code, 200, response.text)
        state = self.state()
        self.assertEqual(state["consent"], {"accepted": True, "additional_seams": 1, "variant": "front_split_center"})
        self.assertEqual(state["active_revision"]["change_acceptance"], state["consent"])

    def test_consent_unknown_field_rejected(self):
        self.remove_a()
        result = self.plan_ok()
        response = self.client.post("/api/accept-split", json={"accepted": True, "additional_seams": 1, "variant": "front_split_center", "expected_generation_token": result["generation_token"]})
        self.assertEqual(response.status_code, 422)


class RaceConditionTests(ApiBase):
    def old_plan_locked(self):
        data = self.state()
        entered = threading.Event()
        release = threading.Event()
        original = engine.search_layout
        locked = {"done": False}

        def slow_search(*args, **kwargs):
            if not locked["done"]:
                locked["done"] = True
                entered.set()
                release.wait(10)
            return original(*args, **kwargs)

        def target():
            try:
                engine.search_layout = slow_search
                response = self.client.post("/api/plan", json={"expected_generation_token": data["generation_token"]})
            finally:
                engine.search_layout = original
        return data, entered, release, threading.Thread(target=target)

    def test_slow_plan_after_cancel_does_not_overwrite(self):
        data = self.state()
        data_job = data["job_token"]
        entered = threading.Event()
        release = threading.Event()
        original = engine.search_layout

        def slow_search(*args, **kwargs):
            entered.set()
            release.wait(10)
            return original(*args, **kwargs)

        def worker():
            engine.search_layout = slow_search
            try:
                self.client.post("/api/plan", json={"expected_generation_token": data["generation_token"]})
            finally:
                engine.search_layout = original

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(entered.wait(10), "planning did not start")
        cancel = self.client.post("/api/cancel", json={"expected_generation_token": data["generation_token"]})
        self.assertEqual(cancel.status_code, 200, cancel.text)
        release.set()
        thread.join(20)
        after = self.state()
        self.assertIsNone(after["candidate"])
        self.assertIsNone(after["active_revision"])
        self.assertEqual(after["generation_token"], data["generation_token"] + 1)
        self.assertNotEqual(after["job_token"], data_job)

    def test_late_old_plan_loses_to_new_plan(self):
        data = self.state()
        entered = threading.Event()
        release = threading.Event()
        original = engine.search_layout
        results = {}

        def slow_search(*args, **kwargs):
            entered.set()
            release.wait(10)
            return original(*args, **kwargs)

        def worker():
            engine.search_layout = slow_search
            try:
                results["old"] = self.client.post("/api/plan", json={"expected_generation_token": data["generation_token"]})
            finally:
                engine.search_layout = original

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(entered.wait(10), "planning did not start")
        cancel = self.client.post("/api/cancel", json={"expected_generation_token": data["generation_token"]})
        self.assertEqual(cancel.status_code, 200)
        release.set()
        thread.join(20)
        self.assertEqual(results["old"].status_code, 409)
        fresh = self.plan_ok()
        self.assertIsNotNone(fresh["candidate"])
        self.assertEqual(fresh["candidate"]["job_token"], fresh["job_token"])

    def test_stale_pdf_after_edit_conflicts(self):
        result = self.plan_ok()
        sig = result["signature"]
        entered = threading.Event()
        release = threading.Event()
        original = app_module.pdf_export.generate_pdf

        def slow_pdf(*args, **kwargs):
            entered.set()
            release.wait(10)
            return original(*args, **kwargs)

        with mock.patch.object(app_module.pdf_export, "generate_pdf", side_effect=slow_pdf):
            worker = threading.Thread(target=lambda: self.state() if False else None)
            def run_export():
                response = self.client.get("/api/export-pdf", params={"revision_sig": sig, "mode": "paper_test"})
                stale["status"] = response.status_code
            stale = {}
            worker = threading.Thread(target=run_export)
            worker.start()
            self.assertTrue(entered.wait(10), "pdf did not start")
            self.client.post("/api/input", json={"expected_generation_token": result["generation_token"], "data": self.avail(locked=("D",))})
            release.set()
            worker.join(20)
        self.assertEqual(stale["status"], 409, stale)
        fresh = self.state()
        self.assertEqual(fresh["data"]["materials"][3]["color"], "beige")

    def test_export_does_not_hold_lock_during_generation(self):
        result = self.plan_ok()
        sig = result["signature"]
        entered = threading.Event()
        release = threading.Event()
        original = app_module.pdf_export.generate_pdf

        def blocking_pdf(*args, **kwargs):
            entered.set()
            release.wait(10)
            return original(*args, **kwargs)

        with mock.patch.object(app_module.pdf_export, "generate_pdf", side_effect=blocking_pdf):
            def run_export():
                self.client.get("/api/export-pdf", params={"revision_sig": sig, "mode": "paper_test"})
            worker = threading.Thread(target=run_export)
            worker.start()
            self.assertTrue(entered.wait(10), "pdf did not start")
            start = time.monotonic()
            state = self.client.get("/api/state")
            elapsed = time.monotonic() - start
            release.set()
            worker.join(20)
        self.assertEqual(state.status_code, 200, state.text)
        self.assertLess(elapsed, 2.0, "state read blocked while PDF generated")

    def test_concurrent_plans_latest_wins(self):
        data = self.state()
        original = engine.search_layout
        gate = threading.Event()

        def synchronized_search(*args, **kwargs):
            gate.wait(10)
            return original(*args, **kwargs)

        outcomes = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait(10)
            engine.search_layout = synchronized_search
            try:
                response = self.client.post("/api/plan", json={"expected_generation_token": data["generation_token"]})
                outcomes.append(response.status_code)
            finally:
                engine.search_layout = original

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        gate.set()
        self.assertEqual(sorted(outcomes), [200, 409])
        result = self.state()
        self.assertIsNotNone(result["candidate"])
        self.assertEqual(result["candidate"]["job_token"], result["job_token"])

    def test_failure_does_not_clobber_current(self):
        result = self.plan_ok()
        data = self.state()
        orig = engine.search_layout
        try:
            engine.search_layout = lambda *args, **kwargs: {"ok": False, "code": "NO_LAYOUT_WITHIN_BUDGET", "message": "test", "search": {"checks_used": 0, "orders_used": 0, "variants": [], "elapsed_s": 0}}
            response = self.client.post("/api/plan", json={"expected_generation_token": data["generation_token"]})
        finally:
            engine.search_layout = orig
        self.assertEqual(response.status_code, 422)
        state = self.state()
        self.assertIsNone(state["active_revision"])
        self.assertEqual(state["data"], data["data"])

    def test_old_job_signatures_checked_at_export(self):
        result = self.plan_ok()
        stale_sig = result["active_revision"]["input_signature"]
        stale_gen = result["generation_token"]
        stale_job = result["job_token"]
        self.remove_a()
        state = self.state()
        self.assertGreater(state["generation_token"], stale_gen)
        self.assertNotEqual(state["job_token"], stale_job)
        response = self.client.get("/api/export-pdf", params={"revision_sig": stale_sig, "mode": "paper_test"})
        self.assertEqual(response.status_code, 409)


class TemplateInvalidationTests(ApiBase):
    def test_template_change_invalidates_stale_export(self):
        result = self.plan_ok()
        sig = result["signature"]
        original_version = engine.template.TEMPLATE_VERSION
        self.addCleanup(setattr, engine.template, "TEMPLATE_VERSION", original_version)
        engine.template.TEMPLATE_VERSION = original_version + "-changed"
        after = self.state()
        self.assertTrue(after["stale"])
        self.assertIsNone(after["active_revision"])
        response = self.client.get("/api/export-pdf", params={"revision_sig": sig, "mode": "paper_test"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "STALE_REVISION")

    def test_construction_status_change_invalidates(self):
        result = self.plan_ok()
        self.client.get("/api/export-pdf", params={"revision_sig": result["signature"], "mode": "paper_test"})
        original_status = engine.template.REVIEW_STATUS
        self.addCleanup(setattr, engine.template, "REVIEW_STATUS", original_status)
        engine.template.REVIEW_STATUS = "reviewed"
        before = self.state()
        after = self.client.post("/api/reset").json()
        self.assertNotEqual(before["phase"], "current_checked")

    def test_reset_clears_template_stale(self):
        result = self.plan_ok()
        original_version = engine.template.TEMPLATE_VERSION
        self.addCleanup(setattr, engine.template, "TEMPLATE_VERSION", original_version)
        engine.template.TEMPLATE_VERSION = original_version + "-x"
        self.assertTrue(self.state()["stale"])
        engine.template.TEMPLATE_VERSION = original_version
        reset = self.client.post("/api/reset").json()
        self.assertFalse(reset["stale"])


if __name__ == "__main__":
    unittest.main()
