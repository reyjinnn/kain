"""Unit test for parity against frozen fixture references/parity-fixtures-v1.json."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.design import template as t
from backend.design import engine

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "references" / "parity-fixtures-v1.json"


class ParityBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FIXTURE_PATH.exists():
            raise unittest.SkipTest(f"Frozen parity fixture not found: {FIXTURE_PATH}")
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_constants_parity(self):
        constants = self.fixture["constants"]
        self.assertEqual(constants["S"], t.S)
        self.assertEqual(constants["HEM_FOLD_1_MM"], t.HEM_FOLD_1_MM)
        self.assertEqual(constants["HEM_FOLD_2_MM"], t.HEM_FOLD_2_MM)
        self.assertEqual(constants["H"], t.H)
        self.assertEqual(constants["J"], t.J)
        self.assertEqual(tuple(constants["W_RANGE"]), t.W_RANGE)
        self.assertEqual(tuple(constants["H_RANGE"]), t.H_RANGE)

    def test_demo_cases_search_parity(self):
        for case in self.fixture["cases"]:
            data = engine.fixture_data(unavailable=case["unavailable"])
            sig = engine.signature(data)
            self.assertEqual(sig, case["signature"])
            res = engine.search_layout(data, sig)
            self.assertEqual(res["ok"], case["search_ok"])
            if case["search_ok"]:
                cand = res["candidate"]
                expected = case["candidate"]
                self.assertEqual(cand["variant"], expected["variant"])
                self.assertEqual(cand["cut_area_mm2"], expected["cut_area_mm2"])
                self.assertEqual(cand["extra_seams"], expected["extra_seams"])
                self.assertEqual(cand["extra_raw_sew_length_mm"], expected["extra_raw_sew_length_mm"])
                self.assertEqual(cand["finished_size_mm"], expected["finished_size_mm"])

    def test_all_84_sweep_variants_parity(self):
        sweep = self.fixture["sweep_84"]
        self.assertEqual(len(sweep), 84)
        for item in sweep:
            w = item["W_mm"]
            h = item["H_mm"]
            variant = item["variant"]
            var_def = t.build_variant(w, h, variant)

            self.assertEqual(var_def.raw_cut_area_mm2, item["raw_cut_area_mm2"])
            self.assertEqual(var_def.additional_seams, item["additional_seams"])
            self.assertEqual(var_def.extra_raw_sew_length_mm, item["extra_raw_sew_length_mm"])
            self.assertEqual(set(var_def.patterns.keys()), set(item["patterns"].keys()))

            for pid, ps in var_def.patterns.items():
                ref_p = item["patterns"][pid]
                self.assertEqual([list(pt) for pt in ps.cut_ring], ref_p["cut_ring"])
                self.assertEqual(ps.width_mm, ref_p["width_mm"])
                self.assertEqual(ps.height_mm, ref_p["height_mm"])
                self.assertEqual([list(pt) for pt in ps.notches], ref_p["notches"])


if __name__ == "__main__":
    unittest.main()
