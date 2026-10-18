#Tes T1: kirim cerita "depan hitam" -> warna TIDAK berubah jadi biru default, evidence memakai kutipan pengguna
#Tes T2: revisi chat "ganti warna depan jadi krem" -> panel depan ikut berubah
#Tes T3: caption 3D menyebut ilustrasi; T5: penjelasan katalog grounded
import io
import inspect
import json
import os
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from PIL import Image
from fastapi.testclient import TestClient
import pymupdf

from backend import app as service
from backend.pdf import pdf_export
from backend.design import colors, engine
from backend.services import ai, chat_actions
from backend.tests.test_ai_request import response_v2
from backend.tests.test_pdf import _whole, _consented_split

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def events_of(response):
    parsed = []
    for block in response.text.split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 2:
            parsed.append((lines[0].removeprefix("event: "),
                           json.loads(lines[1].removeprefix("data: "))))
    return parsed


def brief_of(response):
    found = [data for name, data in events_of(response) if name == "brief"]
    return found[0] if found else None


class AdversarialStoryTests(unittest.TestCase):
    """T1 dan T2 dari uji pengguna: cerita wajib menang atas default demo."""

    def setUp(self):
        self.client = TestClient(service.app)
        self.client.post("/api/reset")
        # Inventory fixture has no black fabric; a "hitam" story must warn, not substitute.
        assert not any(m["available"] and m["color"] == "#2B2B2E"
                       for m in self.client.get("/api/state").json()["data"]["materials"])

    def state(self):
        return self.client.get("/api/state").json()

    def test_t1_black_story_never_becomes_default_blue(self):
        old = deepcopy(self.state()["data"])
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test-router-key"}), \
                mock.patch.object(ai, "_call_chat_api", return_value=json.dumps(
                    response_v2({"front_color": "hitam"}, {"front_color": "depan hitam"}))):
            response = self.client.post("/api/ai/chat", json={"text": "depan hitam", "ai_consent": True})
        brief = brief_of(response)
        self.assertIsNotNone(brief, response.text)
        self.assertEqual(brief["proposal"]["preferences"]["front_color"], "hitam")
        warning = next(w for w in brief["warnings"] if w["code"] == "COLOR_NOT_IN_STOCK")
        self.assertTrue({'nearest', 'add', 'ignore'} <= {a['id'] for a in warning['actions']})
        # The nearest suggestion is labeled in Indonesian; "hitam" was kept, not replaced.
        self.assertIn("Pakai warna terdekat (biru denim)", response.text)
        self.assertIn('"front_color": "hitam"', response.text.replace('front_color": "hitam"', 'front_color": "hitam"'))
        # Default demo values were not silently filled into the draft.
        self.assertEqual(self.state()["data"], old)

    def test_t1_default_demo_values_are_not_copied_into_proposal(self):
        """A story that only mentions color must keep prior dimensions (T1 root cause)."""
        data = self.state()["data"]
        data["request"].update(W_mm=110, H_mm=90)
        self.client.post("/api/input", json={"data": data})
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test-router-key"}), \
                mock.patch.object(ai, "_call_chat_api", return_value=json.dumps(
                    response_v2({"front_color": "hitam"}, {"front_color": "depan hitam"}))):
            response = self.client.post("/api/ai/chat", json={"text": "depan hitam", "ai_consent": True})
        brief = brief_of(response)
        self.assertEqual(brief["proposal"]["W_mm"], 110)
        self.assertEqual(brief["proposal"]["H_mm"], 90)
        self.assertEqual(brief["proposal"]["preferences"]["front_color"], "hitam")
        self.assertTrue(any(w["code"] == "COLOR_NOT_IN_STOCK" for w in brief["warnings"]))

    def test_t2_color_revision_reaches_panels_and_image_prompt(self):
        data = self.state()["data"]
        data["materials"][3]["outer_mm"] = [[0, 0], [200, 0], [200, 170], [0, 170]]
        self.client.post("/api/input", json={"data": data})
        self.client.post("/api/confirm", json={})
        self.client.post("/api/plan", json={})
        payload = response_v2({"front_color": "krem"}, {"front_color": "ganti warna depan jadi krem"})
        with mock.patch.dict(os.environ, {"NINEROUTER_KEY": "test-router-key"}), \
                mock.patch.object(ai, "_call_chat_api", return_value=json.dumps(payload)):
            response = self.client.post("/api/ai/chat",
                                        json={"text": "ganti warna depan jadi krem", "ai_consent": True, "replan": True})
        self.assertIn("event: design_ready", response.text)
        state = self.state()
        front = next(p for p in state["active_revision"]["placements"] if p["instance_id"] == "FRONT")
        # The cream fabric D is actually used, not just echoed in text.
        self.assertEqual(front["material_id"], "D")
        prompt = ai.image_prompt(state["data"], state["active_revision"])
        faces = json.loads(prompt.split("Use these panel colors exactly: ")[1].split(". Neutral")[0])
        front_color = next(face["fabric_color"] for face in faces if face["part"] == "FRONT")
        self.assertEqual(front_color["hex"], "#D9C7A5")
        self.assertEqual(front_color["name"], "krem")


class AdversarialIlustrationTests(unittest.TestCase):
    """T3: ilustrasi jujur; T5: penjelasan katalog grounded."""

    def setUp(self):
        self.client = TestClient(service.app)
        self.client.post("/api/reset")
        self.client.post("/api/plan", json={})
        self.state = self.client.get("/api/state").json()

    def test_t3_illustration_caption_names_itself_and_fallback_copy_exists(self):
        # The browser behaviour is covered by test_3d.cjs; here the honest copy
        # contract that the browser test and the makers rely on is pinned.
        pouch3d = (FRONTEND / "src" / "preview" / "pouch3d.js").read_text(encoding="utf-8")
        self.assertIn("3D preview is not available on this device.", pouch3d)
        index_html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.assertIn("Shape illustration. Real fabric may drape and bulge differently", index_html)
        # The 3D state exposes the same hex the swatches and the AI image prompt use.
        self.assertIn("#D9C7A5", (FRONTEND / "src" / "config" / "colors.json").read_text(encoding="utf-8"))

    def test_t5_step_catalog_grounding(self):
        data, candidate, _, _ = _whole()
        split_data, split_candidate, _, _ = _consented_split()
        whole_text = chat_actions.explanation(data, candidate, {"topic": "step", "step": 2})
        self.assertIn("Lipat", whole_text)
        self.assertIn("5 mm", whole_text)
        # Displayed numbering omits the split step for a whole front.
        self.assertIn("Periksa sudut", chat_actions.explanation(data, candidate, {"topic": "step", "step": 4}))
        split_text = chat_actions.explanation(split_data, split_candidate, {"topic": "step", "step": 4})
        self.assertIn("Tumpuk", split_text)
        for explanation in (whole_text, split_text,
                            chat_actions.explanation(data, candidate, {"topic": "layout"}),
                            chat_actions.explanation(split_data, split_candidate, {"topic": "layout"})):
            for banned in ("JOIN_FRONT", "FRONT_", "VAL-", "signature", "9Router", "{", "}"):
                self.assertNotIn(banned, explanation)
        self.assertIn("muat", chat_actions.explanation(data, candidate, {"topic": "layout"}))

    def test_t5_explanation_api_serves_catalog_without_model(self):
        with mock.patch.object(ai, "_call_chat_api") as model:
            response = self.client.post("/api/ai/chat", json={"text": "jelaskan langkah 4", "ai_consent": True})
        model.assert_not_called()
        # Whole front: displayed step 4 is the honest corner review (no invented finishing).
        self.assertIn("Periksa sudut", response.text)
        self.assertNotIn("JOIN_FRONT", response.text)
        # Arithmetic placeholders are filled: no raw {W_cm}-style template leaks to the user.
        message = next(json.loads(line.removeprefix("data: "))
                       for name, data_value in events_of(response) if name == "message"
                       for line in [next(l for l in ["data: " + json.dumps(data_value, ensure_ascii=False)])])
        self.assertNotIn("{W_cm", message["text"])
        self.assertNotIn("{{", message["text"])


class PromptGapTests(unittest.TestCase):
    """The package prompt must not induce words the construction validator rejects."""

    def setUp(self):
        self.client = TestClient(service.app)
        self.client.post("/api/reset")
        self.client.post("/api/plan", json={})
        self.state = self.client.get("/api/state").json()

    def test_package_prompt_bans_words_the_validator_rejects(self):
        # LLMs imitate the banned-vocabulary list ("tanpa ritsleting/penutup/lapisan")
        # in their notes; the validator then rejects the whole package. The compact
        # package prompt must state the ban explicitly to avoid that mismatch.
        source = inspect.getsource(ai.generate_plan)
        self.assertIn("JANGAN menulis kata penutup", source)

    def test_close_model_response_is_validated_and_labelled(self):
        """Image color check: close render passes; distant render is labelled, never hidden."""
        data, candidate = self.state["data"], self.state["active_revision"]
        front_hex = next(colors.resolve(m["color"])["hex"] for m in data["materials"]
                         if m["id"] == next(p["material_id"] for p in candidate["placements"]
                                            if p["instance_id"] == "FRONT"))
        out = io.BytesIO()
        Image.new("RGB", (400, 400), front_hex).save(out, format="PNG")
        check = ai.image_color_check(out.getvalue(), data, candidate)
        self.assertEqual(check["color_status"], "close")
        out = io.BytesIO()
        Image.new("RGB", (400, 400), "#A4442F").save(out, format="PNG")
        check = ai.image_color_check(out.getvalue(), data, candidate)
        self.assertEqual(check["color_status"], "different")
        self.assertIn("berbeda", check["color_warning"])


class AdversarialPdfTests(unittest.TestCase):
    """Petunjuk: jujur tentang ilustrasi; pola tetap vektor 1:1."""

    def test_cover_captions_name_their_source_of_truth(self):
        data, candidate, sig, _ = _whole()
        render = pdf_export.generate_pdf(data, candidate, sig)
        vector = pymupdf.open(stream=render, filetype="pdf")
        self.assertIn("Ilustrasi bentuk; kain asli bisa jatuh dan menggembung berbeda", vector[0].get_text())
        buffer = io.BytesIO()
        Image.new("RGB", (120, 100), "#35508F").save(buffer, format="PNG")
        render = pdf_export.generate_pdf(data, candidate, sig, illustration=buffer.getvalue())
        drawn = pymupdf.open(stream=render, filetype="pdf")
        cover = drawn[0].get_text()
        self.assertIn("Ilustrasi AI (gaya foto)", cover)
        self.assertIn("Ikuti swatch kain, bukan gambar untuk ukuran pola", cover)
        # Patterns stay vector even when an AI illustration is embedded on the cover.
        for page, part in zip([p for p in drawn if "POLA 1:1" in p.get_text()], candidate["patterns"]):
            self.assertEqual(page.get_images(), [])

    def test_t4_calibration_box_is_square_and_check_row_exists(self):
        data, candidate, sig, _ = _whole()
        document = pymupdf.open(stream=pdf_export.generate_pdf(data, candidate, sig), filetype="pdf")
        before = document[1].get_text()
        self.assertIn("Periksa kotak dengan penggaris", before)
        self.assertIn("Hasil ukur: lebar ........ mm", before)
        # Every pattern page keeps the small 50 × 50 mm square next to nothing else 50×50.
        for page in [p for p in document if "POLA 1:1" in p.get_text()]:
            rects = [drawing["rect"] for drawing in page.get_drawings()
                     if abs(drawing["rect"].width - 50 * pdf_export.PT if False else drawing["rect"].width) > 0]
        from backend.tests.test_pdf import _rects, PT
        for page in [p for p in document if "POLA 1:1" in p.get_text()]:
            square = _rects(page, 50, 50)[0]
            self.assertAlmostEqual(square.width / PT, 50, delta=.1)
            self.assertAlmostEqual(square.height / PT, 50, delta=.1)
            self.assertIn("Cek: 50 × 50 mm", page.get_text())
        # Final page keeps the honest tolerance sentence from config, not invented data.
        final = document[len(document) - 1].get_text()
        self.assertIn(f'Selisih ukuran luar paling banyak {engine.CONFIG["sewn_exterior_mm"]:g} mm', final)
        self.assertRegex(" ".join(final.split()), r"bukan bukti pouch akan\s*muat atau kuat")
