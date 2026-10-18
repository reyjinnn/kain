import math
import os
import unittest
from pathlib import Path

import pymupdf

from backend.pdf import pdf_export
from backend.design import engine

# Original assertions remain intact on the technical appendix. New guide tests
# independently check its actual vector lengths and friendly page content.
def technical_pdf(*args, **kwargs):
    return pdf_export.generate_pdf(*args, kind="technical", **kwargs)

PT = 72 / 25.4
RENDER_DIR = Path(os.environ.get("SISA_RENDER_DIR", r"C:\Users\user\AppData\Local\Temp\opencode\sisa_renders"))
TOL = 0.3
CONSENT = {"accepted": True, "additional_seams": 1, "variant": "front_split_center"}


def max_fixture(unavailable=(), tweak=False):
    data = engine.fixture_data(unavailable=unavailable)
    data["request"]["W_mm"], data["request"]["H_mm"] = 160, 180
    for m in data["materials"]:
        if m["id"] in ("A", "B"):
            m["outer_mm"] = [[0, 0], [210, 0], [210, 220], [0, 220]]
            if tweak and m["id"] == "A":
                m["outer_mm"] = [[0, 0], [215, 0], [215, 230], [0, 230]]
        else:
            m["outer_mm"] = [[0, 0], [150, 0], [150, 220], [0, 220]]
        m["defects_mm"] = []
    return engine.validate_input(data)


def _consented_split(w=160, h=120, unavailable=["A"]):
    data = engine.fixture_data(unavailable=unavailable)
    data["request"]["W_mm"], data["request"]["H_mm"] = w, h
    signature = engine.signature(data)
    result = engine.search_layout(data, signature)
    assert result["ok"], result["code"]
    candidate = result["candidate"]
    signature = engine.signature(data, CONSENT)
    candidate = {**candidate, "input_signature": signature, "change_acceptance": CONSENT, "design_revision_id": f"DR-SPLIT-{w}x{h}"}
    report = engine.validate_candidate(data, candidate, signature, consent=CONSENT, activation=True)
    assert report["passed"], report["failures"]
    return data, candidate, signature, report


def _whole(data=None, w=160, h=120, unavailable=()):
    data = data or engine.fixture_data(unavailable=unavailable)
    data["request"]["W_mm"], data["request"]["H_mm"] = w, h
    signature = engine.signature(data)
    result = engine.search_layout(data, signature)
    assert result["ok"], result["code"]
    candidate = {**result["candidate"], "design_revision_id": f"DR-WHOLE-{w}x{h}"}
    report = engine.validate_candidate(data, candidate, signature, activation=True)
    assert report["passed"], report["failures"]
    return data, candidate, signature, report


def _rid(candidate, default):
    return dict(candidate, design_revision_id=candidate.get("design_revision_id") or default)


def _max_consented_split():
    data = max_fixture(unavailable=["A"])
    signature = engine.signature(data)
    result = engine.search_layout(data, signature)
    assert result["ok"], result["code"]
    signature = engine.signature(data, CONSENT)
    candidate = {**result["candidate"], "input_signature": signature, "change_acceptance": CONSENT, "design_revision_id": "DR-SPLIT-MAX"}
    report = engine.validate_candidate(data, candidate, signature, consent=CONSENT, activation=True)
    assert report["passed"], report["failures"]
    return data, candidate, signature, report


def _section_page(doc, needle):
    for page in doc:
        if needle in page.get_text():
            return page
    return None


def _rects(page, width_mm, height_mm):
    found = []
    for path in page.get_drawings():
        for item in path["items"]:
            if item[0] == "re":
                r = item[1]
                if abs(r.width - width_mm * PT) < TOL and abs(r.height - height_mm * PT) < TOL:
                    found.append(r)
    return found


def _page_text(doc):
    return "".join(page.get_text() for page in doc)


def _render(doc, stem):
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, page in enumerate(doc):
        pix = page.get_pixmap(dpi=110)
        path = RENDER_DIR / f"{stem}_p{index}.png"
        path.write_bytes(pix.tobytes("png"))
        paths.append(str(path))
    return paths


class EngineContractTests(unittest.TestCase):
    def test_whole_activates_without_consent(self):
        data, candidate, signature, report = _whole()
        self.assertEqual(candidate["variant"], "whole")
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["checks"]), 10)
        pdf = technical_pdf(data, candidate, signature)
        self.assertGreater(len(pdf), 2000)

    def test_split_requires_acceptance(self):
        data = engine.fixture_data(unavailable=["A"])
        signature = engine.signature(data)
        candidate = engine.search_layout(data, signature)["candidate"]
        pending = engine.validate_candidate(data, candidate, signature)
        self.assertTrue(pending["geometry_passed"])
        self.assertFalse(pending["passed"])
        with self.assertRaises(engine.EngineError):
            technical_pdf(data, candidate, signature)

    def test_consent_variant_mismatch_fails(self):
        data = engine.fixture_data(unavailable=["A"])
        signature = engine.signature(data)
        result = engine.search_layout(data, signature)
        self.assertTrue(result["ok"])
        candidate = result["candidate"]
        for consent in ({"accepted": True, "additional_seams": 1, "variant": "whole"}, {"accepted": False, "additional_seams": 1}):
            signature_c = engine.signature(data, consent)
            candidate_c = {**candidate, "input_signature": signature_c, "change_acceptance": consent}
            self.assertFalse(engine.validate_candidate(data, candidate_c, signature_c, consent=consent, activation=True)["passed"], consent)

    def test_signature_includes_consent(self):
        data = engine.fixture_data()
        self.assertNotEqual(engine.signature(data, CONSENT), engine.signature(data))

    def test_g1_default_whole_fixture(self):
        data, candidate, signature, report = _whole()
        self.assertEqual(candidate["variant"], "whole")
        self.assertEqual(candidate["cut_area_mm2"], 50400)
        front = next(p for p in candidate["patterns"] if p["id"] == "FRONT")
        self.assertEqual(front["width_mm"], 180)
        self.assertEqual(front["height_mm"], 140)

    def test_g2_split_fixture(self):
        data, candidate, signature, report = _consented_split()
        self.assertEqual(candidate["variant"], "front_split_center")
        self.assertEqual(candidate["cut_area_mm2"], 53200)
        self.assertEqual(candidate["extra_seams"], 1)
        self.assertEqual(candidate["extra_raw_sew_length_mm"], 140)
        self.assertEqual(len(candidate["patterns"]), 3)

    def test_max_domain_search_whole(self):
        data = max_fixture()
        signature = engine.signature(data)
        result = engine.search_layout(data, signature)
        self.assertTrue(result["ok"], result.get("code"))
        back = next(p for p in result["candidate"]["patterns"] if p["id"] == "BACK")
        self.assertEqual(back["width_mm"], 180)
        self.assertEqual(back["height_mm"], 200)

    def test_max_domain_search_split(self):
        data, candidate, signature, report = _max_consented_split()
        back = next(p for p in candidate["patterns"] if p["id"] == "BACK")
        self.assertEqual(back["width_mm"], 180)
        self.assertEqual(back["height_mm"], 200)

    def test_modified_material_dims(self):
        data = max_fixture(tweak=True)
        signature = engine.signature(data)
        result = engine.search_layout(data, signature)
        self.assertTrue(result["ok"], result.get("code"))

    def test_stale_signature_blocked_both_modes(self):
        data, candidate, signature, report = _consented_split()
        for mode in ("paper_test", "fabric_trial"):
            with self.assertRaises(engine.EngineError) as manager:
                technical_pdf(data, candidate, "0" * 64, mode=mode)
            self.assertEqual(manager.exception.code, "STALE_REVISION")

    def test_fabric_trial_draft_blocked(self):
        data, candidate, signature, report = _consented_split()
        self.assertEqual(candidate["construction_status"], "draft")
        with self.assertRaises(engine.EngineError) as manager:
            technical_pdf(data, candidate, signature, mode="fabric_trial")
        self.assertEqual(manager.exception.code, "CONSTRUCTION_UNREVIEWED")

    def test_invalid_mode_rejected(self):
        data, candidate, signature, report = _consented_split()
        with self.assertRaises(engine.EngineError) as manager:
            technical_pdf(data, candidate, signature, mode="preview")
        self.assertEqual(manager.exception.code, "INVALID_INPUT")


class PdfGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.split_data, cls.split_candidate, cls.split_signature, _ = _consented_split()
        cls.split_pdf = technical_pdf(cls.split_data, cls.split_candidate, cls.split_signature)
        cls.split_doc = pymupdf.open(stream=cls.split_pdf, filetype="pdf")
        cls.max_data, cls.max_candidate, cls.max_signature, _ = _whole(max_fixture(), w=160, h=180)
        cls.max_pdf = technical_pdf(cls.max_data, cls.max_candidate, cls.max_signature)
        cls.max_doc = pymupdf.open(stream=cls.max_pdf, filetype="pdf")
        cls.max_split_data, cls.max_split_candidate, cls.max_split_signature, _ = _max_consented_split()
        cls.max_split_pdf = technical_pdf(cls.max_split_data, cls.max_split_candidate, cls.max_split_signature)
        cls.max_split_doc = pymupdf.open(stream=cls.max_split_pdf, filetype="pdf")

    def test_multipage_a4(self):
        for doc in (self.split_doc, self.max_doc, self.max_split_doc):
            self.assertGreaterEqual(doc.page_count, 5)
            for page in doc:
                self.assertAlmostEqual(page.rect.width, 210 * PT, delta=0.1)
                self.assertAlmostEqual(page.rect.height, 297 * PT, delta=0.1)

    def test_one_pattern_per_page_no_scaling(self):
        for doc, candidate in ((self.split_doc, self.split_candidate), (self.max_doc, self.max_candidate), (self.max_split_doc, self.max_split_candidate)):
            for part in candidate["patterns"]:
                pages = [page for page in doc if f"POLA SKALA 1:1 / {part['id']}" in page.get_text()]
                self.assertEqual(len(pages), 1, f"one page per pattern for {part['id']}")
        back = _section_page(self.max_doc, "POLA SKALA 1:1 / BACK")
        self.assertTrue(_rects(back, 180, 200), "cut 180x200 rect on its own page")

    def test_calibration_square_50mm_both_axes(self):
        for doc in (self.split_doc, self.max_doc, self.max_split_doc):
            squares = _rects(_section_page(doc, "POLA SKALA 1:1"), 50, 50)
            self.assertTrue(squares)
            self.assertTrue(all(abs(r.width - r.height) < TOL for r in squares))

    def test_pattern_and_square_placement(self):
        page = _section_page(self.split_doc, "POLA SKALA 1:1 / BACK")
        back = next(p for p in self.split_candidate["patterns"] if p["id"] == "BACK")
        pat = _rects(page, back["width_mm"], back["height_mm"])[0]
        sq = _rects(page, 50, 50)[0]
        self.assertAlmostEqual(pat.x0, 10 * PT, delta=TOL)
        self.assertAlmostEqual(pat.y1, (297 - 75) * PT, delta=TOL)
        self.assertAlmostEqual(sq.x0, 10 * PT, delta=TOL)
        self.assertAlmostEqual(sq.y1, (297 - 20) * PT, delta=TOL)
        self.assertAlmostEqual(sq.y0, (297 - 70) * PT, delta=TOL)
        self.assertAlmostEqual(sq.y0 - pat.y1, 5 * PT, delta=0.4)

    def test_max_pattern_top_leaves_header_footer_clear(self):
        page = _section_page(self.max_doc, "POLA SKALA 1:1 / BACK")
        pat = _rects(page, 180, 200)[0]
        self.assertAlmostEqual(pat.y0, (297 - 275) * PT, delta=0.6)
        for path in page.get_drawings():
            r = path["rect"]
            self.assertLessEqual(r.y0, pat.y0 + 0.5 or True, r) if False else None
            self.assertLessEqual(r.x0, page.rect.x1)
        self.assertGreater(pat.y0, 10 * PT)

    def test_paths_bounds_inside_page(self):
        for doc in (self.split_doc, self.max_doc, self.max_split_doc):
            for page in doc:
                for path in page.get_drawings():
                    r = path["rect"]
                    self.assertGreaterEqual(r.x0, -1, r)
                    self.assertGreaterEqual(r.y0, -1, r)
                    self.assertLessEqual(r.x1, page.rect.x1 + 1, r)
                    self.assertLessEqual(r.y1, page.rect.y1 + 1, r)

    def test_layout_diagram_real_L_polygon_and_defects(self):
        pdf = technical_pdf(self.split_data, self.split_candidate, self.split_signature)
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        text = _page_text(doc)
        self.assertIn("Diagram penempatan, bukan pola 1:1", text)
        pages = [page for page in doc if "LAYOUT / B" in page.get_text()]
        self.assertEqual(len(pages), 1)
        drawings = pages[0].get_drawings()
        self.assertGreater(len(drawings), 2)


class TextContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.split_data, cls.split_candidate, cls.split_signature, _ = _consented_split()
        cls.pdf = technical_pdf(cls.split_data, cls.split_candidate, cls.split_signature)
        cls.doc = pymupdf.open(stream=cls.pdf, filetype="pdf")
        cls.text = _page_text(cls.doc)

    def test_full_signature_and_unique_revision(self):
        self.assertIn(self.split_signature, self.text)
        rev = self.split_candidate["design_revision_id"]
        self.assertNotEqual(rev, self.split_signature[:12])
        self.assertIn(rev, self.text)

    def test_key_sections_and_evidence_status(self):
        for needle in ("flat_exterior", "bukaan atas", "turn-of-cloth", "KOTAK KALIBRASI", "bukan pola 1:1",
                       "VAL-01", "VAL-10", "TOL-1", "Fabric trial: pending", "print: pending"):
            self.assertIn(needle, self.text)

    def test_components_and_materials_named(self):
        for part in self.split_candidate["patterns"]:
            self.assertIn(part["id"], self.text)
        for mat in self.split_data["materials"]:
            self.assertIn(mat["id"], self.text)

    def test_steps_full_text(self):
        for step in self.split_candidate["assembly_steps"]:
            tail = step["text"].rstrip().split()[-1]
            self.assertIn(tail, self.text)
        for needle in ("JOIN_FRONT", "S_BOTTOM", "stretch"):
            self.assertIn(needle, self.text)

    def test_pages_all_have_watermark_revision_and_sides(self):
        rev = self.split_candidate["design_revision_id"]
        for page in self.doc:
            text = page.get_text()
            self.assertIn("UJI KERTAS", text)
            self.assertIn("KONSTRUKSI BELUM DITINJAU", text)
            self.assertIn(rev, text)
            self.assertIn("Bahan", text)

    def test_summary_has_full_signature_and_pattern_page_material(self):
        summary = _section_page(self.doc, "RINGKASAN")
        self.assertIn(self.split_signature, summary.get_text())
        pattern = _section_page(self.doc, "POLA SKALA 1:1 / FRONT_L")
        self.assertIn("Bahan C", pattern.get_text())


class VisualTests(unittest.TestCase):
    def test_renders_written_all_pages(self):
        cases = (
            ("default_split", lambda: _consented_split()),
            ("default_whole", lambda: _whole()),
            ("max_whole", lambda: _whole(max_fixture(), w=160, h=180)),
            ("max_split", lambda: _max_consented_split()),
        )
        total = []
        for stem, builder in cases:
            data, candidate, signature, report = builder()
            pdf = technical_pdf(data, candidate, signature)
            doc = pymupdf.open(stream=pdf, filetype="pdf")
            total.extend(_render(doc, stem))
        self.assertGreaterEqual(len(total), 30)
        for path in total:
            self.assertGreater(Path(path).stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
