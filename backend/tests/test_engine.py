import copy
import math
import unittest

from backend.design import engine
from backend.design.geometry import EngineError, input_shape, placed_polygon

CONSENT = {"accepted": True, "additional_seams": 1, "variant": "front_split_center"}


def _with_split_candidate(data=None, w=160, h=120, unavailable=("A",)):
    data = data or engine.fixture_data(unavailable=unavailable)
    data["request"]["W_mm"], data["request"]["H_mm"] = w, h
    sig = engine.signature(data)
    result = engine.search_layout(data, sig)
    assert result["ok"], result["code"]
    candidate = result["candidate"]
    sig = engine.signature(data, CONSENT)
    candidate = {**candidate, "input_signature": sig, "change_acceptance": CONSENT}
    report = engine.validate_candidate(data, candidate, sig, consent=CONSENT, activation=True)
    assert report["passed"], report["failures"]
    return data, candidate, sig, report


class OracleTests(unittest.TestCase):
    def test_u_convex_region_not_covered_by_full_polygon(self):
        u_ring = [[0, 0], [100, 0], [100, 100], [70, 100], [70, 30], [30, 30], [30, 100], [0, 100]]
        triangle = [[10, 10], [90, 10], [10, 90]]
        self.assertFalse(engine.polygon(u_ring).covers(engine.polygon(triangle)))

    def test_edge_and_defect_and_gap_thresholds(self):
        data = engine.fixture_data()
        sig = engine.signature(data)
        base = engine.search_layout(data, sig)["candidate"]

        edge = copy.deepcopy(base)
        edge["placements"][0]["tx_mm"] = 1.9
        self.assertFalse(engine.validate_candidate(data, edge, sig)["passed"])
        edge["placements"][0]["tx_mm"] = 2.0
        self.assertTrue(engine.validate_candidate(data, edge, sig)["passed"])

        candidate = copy.deepcopy(base)
        candidate["placements"] = [pl for pl in candidate["placements"] if pl["instance_id"] == "FRONT"]
        report = engine.validate_candidate(data, candidate, sig)
        self.assertEqual(report["checks"][5]["id"], "VAL-06")
        self.assertEqual(report["checks"][5]["status"], "fail")

    def test_path_mutation_one_mm_fails_val07(self):
        data, candidate, sig, _ = _with_split_candidate()
        broken = copy.deepcopy(candidate)
        front = next(p for p in broken["patterns"] if p["id"] == "FRONT_L")
        path = next(p for p in front["sew_paths"] if p["id"] == "OUTER_LEFT_SEW")
        path["points_mm"][0][1] += 1.0
        report = engine.validate_candidate(data, broken, sig, consent=CONSENT, activation=True)
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "VAL-07")["status"], "fail")

    def test_reordered_pairing_fails_val07(self):
        data, candidate, sig, _ = _with_split_candidate()
        broken = copy.deepcopy(candidate)
        for conn in broken["seam_connections"]:
            if conn["seam_id"] == "S_LEFT":
                conn["front"], conn["back"] = conn["back"], conn["front"]
        report = engine.validate_candidate(data, broken, sig, consent=CONSENT, activation=True)
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "VAL-07")["status"], "fail")

    def test_notch_corruption_fails_val07(self):
        data, candidate, sig, _ = _with_split_candidate()
        broken = copy.deepcopy(candidate)
        broken["patterns"][0]["notches_mm"] = [[0, 0]]
        report = engine.validate_candidate(data, broken, sig, consent=CONSENT, activation=True)
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "VAL-07")["status"], "fail")

    def test_duplicated_metadata_fails(self):
        data = engine.fixture_data()
        sig = engine.signature(data)
        candidate = engine.search_layout(data, sig)["candidate"]
        broken = copy.deepcopy(candidate)
        broken["patterns"].append(copy.deepcopy(broken["patterns"][0]))
        report = engine.validate_candidate(data, broken, sig)
        self.assertFalse(report["passed"])
        self.assertEqual(len(report["checks"]), 10)

    def test_unknown_placement_field_rejected(self):
        data = engine.fixture_data()
        sig = engine.signature(data)
        candidate = engine.search_layout(data, sig)["candidate"]
        broken = copy.deepcopy(candidate)
        broken["placements"][0]["mirror"] = True
        report = engine.validate_candidate(data, broken, sig)
        self.assertFalse(report["passed"])

    def test_illegal_rotation_rejected(self):
        data = engine.fixture_data()
        sig = engine.signature(data)
        candidate = engine.search_layout(data, sig)["candidate"]
        for rotation in (90, 150, 190):
            broken = copy.deepcopy(candidate)
            broken["placements"][0]["rotation_deg"] = rotation
            report = engine.validate_candidate(data, broken, sig)
            self.assertEqual(next(c for c in report["checks"] if c["id"] == "VAL-05")["status"], "fail", rotation)

    def test_interpiece_gap_1_9_fails_2_passes(self):
        data = {
            "materials": [{
                "id": "WIDE",
                "outer_mm": [[0, 0], [260, 0], [260, 110], [0, 110]],
                "defects_mm": [],
                "material_type": "woven_non_stretch",
                "compatible_group": "g",
                "color": "blue",
                "allowed_rotations_deg": [0],
                "face": "right_side_up",
                "confirmed": True,
                "available": True,
                "measurement_source": "synthetic_fixture",
            }],
            "request": {"W_mm": 100, "H_mm": 80, "dimension_semantics": "flat_exterior", "max_front_splits": 0,
                        "allow_mixed": True, "preferences": {"front_color": None, "fewer_seams_first": True}, "confirmed": True},
        }
        data = engine.validate_input(data)
        sig = engine.signature(data)
        base = engine.generate_patterns(data["request"], "whole")
        for tx2, expected in ((123.9, False), (124.0, True)):
            candidate = {**base, "placements": [
                {"instance_id": "FRONT", "material_id": "WIDE", "rotation_deg": 0, "tx_mm": 2, "ty_mm": 2},
                {"instance_id": "BACK", "material_id": "WIDE", "rotation_deg": 0, "tx_mm": tx2, "ty_mm": 2},
            ], "input_signature": sig, "change_acceptance": None}
            report = engine.validate_candidate(data, candidate, sig, activation=True)
            val04 = next(c for c in report["checks"] if c["id"] == "VAL-04")
            self.assertEqual(val04["status"], "pass" if expected else "fail", tx2)

    def test_duplicate_and_missing_instances_fail(self):
        data = engine.fixture_data()
        sig = engine.signature(data)
        candidate = engine.search_layout(data, sig)["candidate"]
        missing = copy.deepcopy(candidate)
        missing["patterns"] = missing["patterns"][:1]
        missing["placements"] = missing["placements"][:1]
        self.assertEqual(engine.validate_candidate(data, missing, sig)["checks"][5]["status"], "fail")

    def test_unavailable_material_rejected(self):
        data = engine.fixture_data(unavailable=("B",))
        sig = engine.signature(data)
        candidate = engine.search_layout(engine.fixture_data(), engine.signature(engine.fixture_data()))["candidate"]
        report = engine.validate_candidate(data, candidate, sig)
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "VAL-06")["status"], "fail")


class InputTests(unittest.TestCase):
    def _bad(self, mutate, code="INVALID_INPUT"):
        data = copy.deepcopy(engine.fixture_data())
        mutate(data)
        with self.assertRaises(EngineError) as manager:
            engine.validate_input(data)
        self.assertEqual(manager.exception.code, code)

    def test_strict_field_sets(self):
        self._bad(lambda d: d.update({"extra": 1}))
        self._bad(lambda d: d["materials"][0].update({"scale": 2}))
        self._bad(lambda d: d["materials"][0].update({"mirror": True}))
        self._bad(lambda d: d["request"].update({"nested": {}}))
        self._bad(lambda d: d["request"]["preferences"].update({"unknown": 1}))

    def test_nonfinite_and_negative(self):
        self._bad(lambda d: d["request"].update({"W_mm": float("nan")}))
        self._bad(lambda d: d["materials"][0].update({"outer_mm": [[0, 0], [float("inf"), 0], [190, 150], [0, 150]]}))
        self._bad(lambda d: d["materials"][0].update({"outer_mm": [[0, 0], [-10, 0], [190, 150], [0, 150]]}))

    def test_self_intersection_and_repeated_point(self):
        self._bad(lambda d: d["materials"][0].update({"outer_mm": [[0, 0], [190, 150], [190, 0], [0, 150]]}))
        self._bad(lambda d: d["materials"][0].update({"outer_mm": [[0, 0], [190, 0], [190, 150], [0, 150], [0, 0]]}))

    def test_outside_overlapping_and_oversized_defects(self):
        self._bad(lambda d: d["materials"][0].update({"defects_mm": [[[-5, -5], [5, 5], [5, 10], [-5, 10]]]}))
        overlap = [[[10, 10], [30, 10], [30, 30], [10, 30]], [[20, 20], [40, 20], [40, 40], [20, 40]]]
        self._bad(lambda d: d["materials"][0].update({"defects_mm": overlap}))
        self._bad(lambda d: d["materials"][0].update({"defects_mm": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}))

    def test_bad_domain_and_types(self):
        self._bad(lambda d: d["request"].update({"W_mm": 99}), code="UNSUPPORTED_CONSTRUCTION")
        self._bad(lambda d: d["request"].update({"max_front_splits": 2}))
        self._bad(lambda d: d["request"].update({"allow_mixed": 1}))
        self._bad(lambda d: d["materials"][0].update({"allowed_rotations_deg": [45]}))
        self._bad(lambda d: d["materials"][0].update({"id": "A" * 40}))
        self._bad(lambda d: d["materials"][0].update({"material_type": "stretch"}))
        self._bad(lambda d: d["materials"].append(copy.deepcopy(d["materials"][0])))

    def test_rotation_allowlist_090(self):
        data = copy.deepcopy(engine.fixture_data())
        data["materials"][0]["allowed_rotations_deg"] = [0, 90]
        result = engine.validate_input(data)
        self.assertEqual(result["materials"][0]["allowed_rotations_deg"], [0, 90])

    def test_supported_dimension_corners(self):
        for w, h in ((100, 80), (160, 180)):
            data = copy.deepcopy(engine.fixture_data())
            data["request"]["W_mm"], data["request"]["H_mm"] = w, h
            normalized = engine.validate_input(data)
            self.assertEqual(normalized["request"]["W_mm"], w)
            self.assertEqual(normalized["request"]["H_mm"], h)

    def test_input_not_mutated(self):
        data = engine.fixture_data()
        snapshot = copy.deepcopy(data)
        engine.validate_input(data)
        self.assertEqual(data, snapshot)


class SignatureTests(unittest.TestCase):
    def test_signature_components(self):
        data = engine.fixture_data()
        base = engine.signature(data)
        self.assertNotEqual(base, engine.signature(engine.fixture_data(unavailable=("C",))))
        self.assertNotEqual(base, engine.signature(data, CONSENT))
        changed = copy.deepcopy(data)
        changed["request"]["W_mm"] = 150
        self.assertNotEqual(base, engine.signature(changed))
        self.assertNotEqual(base, engine.signature(changed))
        modified = copy.deepcopy(data)
        modified["materials"][0]["color"] = "green"
        self.assertNotEqual(base, engine.signature(modified))

    def test_signature_roundtrip_stable(self):
        data = engine.fixture_data()
        once = engine.signature(data)
        twice = engine.signature(engine.validate_input(copy.deepcopy(data)))
        self.assertEqual(once, twice)

    def test_invalid_consent_rejected(self):
        data = engine.fixture_data()
        for consent in ({"accepted": True, "variant": "whole", "extra": 1}, {"variant": "whole"}, {"accepted": "yes"}, 5):
            with self.assertRaises(EngineError):
                engine.signature(data, consent)


class ConsentAndReportTests(unittest.TestCase):
    def test_whole_no_consent_required(self):
        data = engine.fixture_data()
        sig = engine.signature(data)
        candidate = engine.search_layout(data, sig)["candidate"]
        report = engine.validate_candidate(data, candidate, sig, activation=True)
        self.assertTrue(report["passed"])
        self.assertTrue(report["geometry_passed"])

    def test_split_pending_then_activation(self):
        data = engine.fixture_data(unavailable=("A",))
        sig = engine.signature(data)
        candidate = engine.search_layout(data, sig)["candidate"]
        pending = engine.validate_candidate(data, candidate, sig)
        self.assertTrue(pending["geometry_passed"])
        self.assertFalse(pending["passed"])
        val8 = next(c for c in pending["checks"] if c["id"] == "VAL-08")
        self.assertEqual(val8["status"], "pending")
        wrong = engine.validate_candidate(data, candidate, sig, activation=True)
        self.assertFalse(wrong["passed"])
        self.assertEqual(next(c for c in wrong["checks"] if c["id"] == "VAL-08")["status"], "fail")

    def test_extra_invalid_consent_rejected(self):
        data = engine.fixture_data(unavailable=("A",))
        sig = engine.signature(data)
        candidate = engine.search_layout(data, sig)["candidate"]
        for consent in ({"accepted": True, "additional_seams": 1, "variant": "whole"}, {"accepted": False, "additional_seams": 1}):
            sig_c = engine.signature(data, consent)
            candidate_c = {**candidate, "input_signature": sig_c, "change_acceptance": consent}
            report = engine.validate_candidate(data, candidate_c, sig_c, consent=consent, activation=True)
            self.assertFalse(report["passed"])

    def test_report_shape_always_ten_sorted(self):
        data = engine.fixture_data()
        sig = engine.signature(data)
        for candidate in ({}, {"variant": "whole"}, "nonsense", 3):
            report = engine.validate_candidate(data, candidate, sig)
            self.assertEqual(len(report["checks"]), 10)
            self.assertEqual([c["id"] for c in report["checks"]], [f"VAL-{i:02d}" for i in range(1, 11)])
            self.assertTrue(all(c["status"] in ("pass", "fail", "pending") for c in report["checks"]))
        report = engine.validate_candidate(None, {}, sig)
        self.assertEqual(len(report["checks"]), 10)


class SearchTests(unittest.TestCase):
    def test_g1_whole_and_g2_split(self):
        data = engine.fixture_data()
        sig = engine.signature(data)
        whole = engine.search_layout(data, sig)
        self.assertTrue(whole["ok"])
        self.assertEqual(whole["candidate"]["variant"], "whole")
        self.assertEqual(whole["candidate"]["cut_area_mm2"], 50400)

        data2 = engine.fixture_data(unavailable=("A",))
        sig2 = engine.signature(data2)
        split = engine.search_layout(data2, sig2)
        self.assertTrue(split["ok"])
        self.assertEqual(split["candidate"]["variant"], "front_split_center")
        self.assertTrue(split["search"]["elapsed_s"] < 1.0)

    def test_g3_area_exact(self):
        data = engine.fixture_data(unavailable=("A", "D"))
        sig = engine.signature(data)
        result = engine.search_layout(data, sig)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "AREA_LOWER_BOUND_FAIL")
        self.assertEqual(result["search"]["available_area_mm2"], 48140)

    def test_budget_exhaustion_not_area_failure(self):
        data = engine.fixture_data()
        sig = engine.signature(data)
        result = engine.search_layout(data, sig, max_checks=1, timeout_s=0.002)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "NO_LAYOUT_WITHIN_BUDGET")
        self.assertGreaterEqual(result["search"]["checks_used"], 1)

    def test_area_sufficient_two_big_panels_one_scrap(self):
        data = {
            "materials": [{
                "id": "BIG",
                "outer_mm": [[0, 0], [400, 0], [400, 300], [0, 300]],
                "defects_mm": [],
                "material_type": "woven_non_stretch",
                "compatible_group": "g",
                "color": "blue",
                "allowed_rotations_deg": [0],
                "face": "right_side_up",
                "confirmed": True,
                "available": True,
                "measurement_source": "synthetic_fixture",
            }],
            "request": {"W_mm": 160, "H_mm": 120, "dimension_semantics": "flat_exterior", "max_front_splits": 0,
                        "allow_mixed": True, "preferences": {"front_color": "blue", "fewer_seams_first": True}, "confirmed": True},
        }
        data = engine.validate_input(data)
        sig = engine.signature(data)
        result = engine.search_layout(data, sig)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["candidate"]["placements"]), 2)

    def test_unconfirmed_request_blocks(self):
        data = engine.fixture_data()
        data["request"]["confirmed"] = False
        sig = engine.signature(data)
        result = engine.search_layout(data, sig)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "UNCONFIRMED_INPUT")

    def test_split_not_permitted(self):
        data = engine.fixture_data(unavailable=("A",))
        data["request"]["max_front_splits"] = 0
        sig = engine.signature(data)
        result = engine.search_layout(data, sig)
        self.assertFalse(result["ok"])
        self.assertIn(result["code"], ("AREA_LOWER_BOUND_FAIL", "NO_LAYOUT_WITHIN_BUDGET", "UNSUPPORTED_CONSTRUCTION"))


class GeometryHelperTests(unittest.TestCase):
    def test_placed_polygon_90_rotation_negative_local(self):
        ring = [[0, 0], [4, 0], [4, 2], [0, 2]]
        poly = placed_polygon(ring, 90, 10, 10)
        self.assertAlmostEqual(poly.bounds[0], 10 - 2)
        self.assertAlmostEqual(poly.bounds[2], 10)
        self.assertAlmostEqual(poly.bounds[1], 10)
        self.assertAlmostEqual(poly.bounds[3], 14)

    def test_polygon_generic_accepts_triangle_u(self):
        triangle = [[0, 0], [10, 0], [0, 10]]
        self.assertGreater(engine.polygon(triangle).area, 0)

    def test_draft_never_claims_physical(self):
        data = engine.fixture_data()
        sig = engine.signature(data)
        candidate = engine.search_layout(data, sig)["candidate"]
        report = engine.validate_candidate(data, candidate, sig, activation=True)
        self.assertTrue(report["passed"])
        self.assertNotIn("physical", report)
        self.assertEqual(candidate["construction_status"], "draft")


if __name__ == "__main__":
    unittest.main()
