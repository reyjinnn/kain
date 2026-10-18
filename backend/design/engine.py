from __future__ import annotations

import hashlib
import itertools
import json
import math
import time
from copy import deepcopy
from pathlib import Path

from shapely.geometry import Polygon
from shapely.ops import unary_union

from backend.design import template as t
from backend.design.colors import PALETTE, resolve, same_family
template = t
from backend.design.geometry import (
    CONFIG,
    EPS,
    CLEARANCE,
    EngineError,
    canonical_float,
    finite,
    fits,
    input_shape,
    normalize_ring,
    placed_polygon,
    polygon,
    raw_area,
    rectangle,
)

FIXTURE = Path(__file__).resolve().parents[2] / "references" / "demo-fixtures.json"


def validate_input(data):
    if not isinstance(data, dict) or set(data) != {"materials", "request"}:
        raise EngineError("INVALID_INPUT", "Diperlukan materials dan request tanpa field tambahan")
    materials, request = data["materials"], data["request"]
    if not isinstance(materials, list) or not 1 <= len(materials) <= 4 or not isinstance(request, dict):
        raise EngineError("INVALID_INPUT", "Gunakan 1-4 kain dan satu request")
    required_request = {"W_mm", "H_mm", "dimension_semantics", "max_front_splits", "allow_mixed", "preferences", "confirmed"}
    if set(request) != required_request:
        raise EngineError("INVALID_INPUT", "Field request tidak lengkap atau tidak dikenal")
    if request["dimension_semantics"] != "flat_exterior":
        raise EngineError("UNSUPPORTED_CONSTRUCTION", "Hanya dimension_semantics flat_exterior yang didukung")
    w = canonical_float(request["W_mm"])
    h = canonical_float(request["H_mm"])
    if not (100.0 <= w <= 160.0 and 80.0 <= h <= 180.0):
        raise EngineError("UNSUPPORTED_CONSTRUCTION", "Ukuran luar harus W 100-160 mm, H 80-180 mm")
    if type(request["max_front_splits"]) is not int or request["max_front_splits"] not in (0, 1):
        raise EngineError("INVALID_INPUT", "max_front_splits harus 0 atau 1")
    if type(request["allow_mixed"]) is not bool or type(request["confirmed"]) is not bool:
        raise EngineError("INVALID_INPUT", "allow_mixed dan confirmed harus boolean")
    prefs = request["preferences"]
    if not isinstance(prefs, dict) or set(prefs) != {"front_color", "fewer_seams_first"}:
        raise EngineError("INVALID_INPUT", "Field preferensi tidak lengkap atau tidak dikenal")
    if prefs["front_color"] is not None and (
        not isinstance(prefs["front_color"], str)
        or not (1 <= len(prefs["front_color"]) <= 64)
        or not prefs["front_color"].isprintable()
    ):
        raise EngineError("INVALID_INPUT", "Warna preferensi harus teks atau null")
    if type(prefs["fewer_seams_first"]) is not bool:
        raise EngineError("INVALID_INPUT", "fewer_seams_first harus boolean")

    allowed_material = {
        "id",
        "outer_mm",
        "defects_mm",
        "material_type",
        "compatible_group",
        "color",
        "allowed_rotations_deg",
        "face",
        "confirmed",
        "available",
        "measurement_source",
    }
    ids = set()
    result = deepcopy(data)
    result["request"]["W_mm"] = w
    result["request"]["H_mm"] = h

    for mat in result["materials"]:
        if not isinstance(mat, dict) or not allowed_material <= set(mat) <= allowed_material | {"name"}:
            raise EngineError("INVALID_INPUT", "Field bahan tidak lengkap, tidak dikenal, atau tidak valid")
        mid = mat["id"]
        if "name" in mat and (not isinstance(mat["name"], str) or not 1 <= len(mat["name"]) <= 40 or not mat["name"].isprintable()):
            raise EngineError("INVALID_INPUT", "Nama kain harus 1-40 karakter")
        if not isinstance(mid, str) or not (1 <= len(mid) <= 32) or not mid.isprintable() or mid in ids:
            raise EngineError("INVALID_INPUT", "ID bahan tidak valid atau duplikat")
        ids.add(mid)
        if mat["material_type"] != "woven_non_stretch" or mat["face"] != "right_side_up" or mat["measurement_source"] not in ("manual_mm", "synthetic_fixture"):
            raise EngineError("INVALID_INPUT", "Hanya kain tenun non-stretch sisi muka yang didukung")
        if not isinstance(mat["compatible_group"], str) or not (1 <= len(mat["compatible_group"]) <= 64) or not mat["compatible_group"].isprintable():
            raise EngineError("INVALID_INPUT", "compatible_group tidak valid")
        if not isinstance(mat["color"], str) or not (1 <= len(mat["color"]) <= 64) or not mat["color"].isprintable():
            raise EngineError("INVALID_INPUT", "color tidak valid")
        if type(mat["confirmed"]) is not bool or type(mat["available"]) is not bool:
            raise EngineError("INVALID_INPUT", "confirmed dan available harus boolean")
        rots = mat["allowed_rotations_deg"]
        if not isinstance(rots, list) or not rots or len(set(rots)) != len(rots) or any(type(r) is not int or r not in (0, 90) for r in rots):
            raise EngineError("INVALID_INPUT", "allowed_rotations_deg hanya 0/90 derajat tanpa duplikat")

        if not isinstance(mat["outer_mm"], list) or len(mat["outer_mm"]) not in (4, 6):
            raise EngineError("INVALID_INPUT", "Bahan hanya persegi panjang atau L enam titik")
        outer = input_shape(mat["outer_mm"])
        if any(c < 0 or c > CONFIG["max_coordinate_mm"] for p in mat["outer_mm"] for c in p):
            raise EngineError("INVALID_INPUT", "Koordinat bahan di luar batas aman")
        defects = mat["defects_mm"]
        if not isinstance(defects, list) or len(defects) > 2:
            raise EngineError("INVALID_INPUT", "Maksimal dua cacat per kain")
        defect_polys = []
        for defect in defects:
            d_poly = rectangle(defect)
            if not outer.contains(d_poly) or outer.boundary.intersects(d_poly):
                raise EngineError("INVALID_INPUT", "Cacat harus sepenuhnya di dalam bahan tanpa menyentuh tepi")
            defect_polys.append(d_poly)
        if len(defect_polys) > 1 and unary_union(defect_polys).area < sum(d.area for d in defect_polys) - EPS:
            raise EngineError("INVALID_INPUT", "Cacat yang tumpang tindih tidak didukung")

        mat["outer_mm"] = normalize_ring([[canonical_float(c) for c in p] for p in mat["outer_mm"]])
        mat["defects_mm"] = [normalize_ring([[canonical_float(c) for c in p] for p in d]) for d in defects]
        norm_outer = input_shape(mat["outer_mm"])
        for d in mat["defects_mm"]:
            d_poly = rectangle(d)
            if not norm_outer.contains(d_poly) or norm_outer.boundary.intersects(d_poly):
                raise EngineError("INVALID_INPUT", "Pembulatan membuat cacat tidak valid")

        mat["defects_mm"].sort(key=lambda d: (min(p[0] for p in d), min(p[1] for p in d), max(p[0] for p in d), max(p[1] for p in d)))

    result["materials"].sort(key=lambda m: m["id"])
    color_counts = {}
    for material in result["materials"]:
        family = resolve(material["color"])["family"]
        color_counts[family] = color_counts.get(family, 0) + 1
        material.setdefault("name", f"Kain {resolve(material['color'])['name']} {color_counts[family]}")
    return result


def fixture_data(unavailable=()):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    unav = set(unavailable)
    materials = [{**fixture["material_defaults"], **m, "available": m["id"] not in unav} for m in fixture["materials"]]
    return validate_input({"materials": materials, "request": fixture["request"]})


def signature(data, consent=None):
    valid_data = validate_input(data)
    valid_consent = None
    if consent is not None:
        if not isinstance(consent, dict):
            raise EngineError("INVALID_INPUT", "Format persetujuan perubahan tidak valid")
        allowed = {"accepted", "additional_seams", "variant"}
        if not set(consent).issubset(allowed) or "accepted" not in consent:
            raise EngineError("INVALID_INPUT", "Field persetujuan perubahan tidak dikenal atau tidak lengkap")
        if type(consent["accepted"]) is not bool:
            raise EngineError("INVALID_INPUT", "consent.accepted harus boolean")
        valid_consent = {"accepted": consent["accepted"]}
        if "additional_seams" in consent:
            if type(consent["additional_seams"]) is not int:
                raise EngineError("INVALID_INPUT", "consent.additional_seams harus integer")
            valid_consent["additional_seams"] = consent["additional_seams"]
        if "variant" in consent:
            if not isinstance(consent["variant"], str):
                raise EngineError("INVALID_INPUT", "consent.variant harus string")
            valid_consent["variant"] = consent["variant"]

    payload = {
        "color_palette": PALETTE,
        "config": CONFIG,
        "consent": valid_consent,
        "input": valid_data,
        "template_content": Path(t.__file__).read_text(encoding="utf-8"),
        "template_id": t.TEMPLATE_ID,
        "template_version": t.TEMPLATE_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def generate_patterns(request, variant):
    try:
        w = float(request["W_mm"])
        h = float(request["H_mm"])
        spec = t.build_variant(w, h, variant)
    except (KeyError, ValueError, TypeError) as exc:
        raise EngineError("UNSUPPORTED_CONSTRUCTION", str(exc)) from exc
    patterns = []
    for pattern in spec.patterns.values():
        paths = lambda items: [{"id": p.id, "points_mm": [list(x) for x in p.points], "length_mm": p.length_mm} for p in items]
        patterns.append({
            "id": pattern.id,
            "role": pattern.role,
            "width_mm": pattern.width_mm,
            "height_mm": pattern.height_mm,
            "cut_mm": [list(x) for x in pattern.cut_ring],
            "sew_paths": paths(pattern.sew_paths),
            "fold_paths": paths([p for p in pattern.fold_paths if p.kind == "fold"]),
            "hem_paths": paths([p for p in pattern.fold_paths if p.kind == "hem"]),
            "notches_mm": [list(x) for x in pattern.notches],
            "grain": pattern.grain,
        })
    steps = [{"id": item["step"], "step": item["step"], "text": item["text"]} for item in t.instruction_skeleton(variant, w, h)]
    return {
        "variant": variant,
        "patterns": patterns,
        "placements": [],
        "seam_connections": spec.seam_connections,
        "assembly_steps": steps,
        "cut_area_mm2": spec.raw_cut_area_mm2,
        "extra_seams": spec.additional_seams,
        "extra_raw_sew_length_mm": spec.extra_raw_sew_length_mm,
        "template_id": t.TEMPLATE_ID,
        "template_version": t.TEMPLATE_VERSION,
        "construction_status": t.REVIEW_STATUS,
        "finished_size_mm": [w, h],
    }


def _path_ok(actual, points, limit=0.1):
    if not isinstance(actual, dict) or not isinstance(actual.get("points_mm"), list) or len(actual["points_mm"]) != len(points):
        return False
    try:
        pts = actual["points_mm"]
        if not all(isinstance(p, (list, tuple)) and len(p) == 2 and all(finite(c) for c in p) for p in pts):
            return False
        if any(math.dist(a, b) > limit + EPS for a, b in zip(pts, points)):
            return False
        computed_len = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        expected_len = sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
        actual_len = actual.get("length_mm")
        if not finite(actual_len):
            return False
        if abs(computed_len - expected_len) > limit + EPS or abs(actual_len - expected_len) > limit + EPS:
            return False
        return True
    except (TypeError, ValueError, KeyError):
        return False


def _seams(candidate, w, h):
    p, q, half = w + 20.0, h + 20.0, w / 2.0
    split = candidate["variant"] == "front_split_center"
    expected = {
        "BACK": {
            "LEFT_SEW": [[10.0, 10.0], [10.0, q - 10.0]],
            "RIGHT_SEW": [[p - 10.0, 10.0], [p - 10.0, q - 10.0]],
            "BOTTOM_SEW": [[10.0, 10.0], [p - 10.0, 10.0]],
        }
    }
    if split:
        expected.update({
            "FRONT_L": {
                "OUTER_LEFT_SEW": [[10.0, 10.0], [10.0, q - 10.0]],
                "BOTTOM_LEFT": [[10.0, 10.0], [half + 10.0, 10.0]],
                "JOIN_FRONT": [[half + 10.0, 0.0], [half + 10.0, q]],
            },
            "FRONT_R": {
                "OUTER_RIGHT_SEW": [[half + 10.0, 10.0], [half + 10.0, q - 10.0]],
                "BOTTOM_RIGHT": [[10.0, 10.0], [half + 10.0, 10.0]],
                "JOIN_FRONT": [[10.0, 0.0], [10.0, q]],
            },
        })
    else:
        expected["FRONT"] = expected["BACK"]

    pieces = {part["id"]: part for part in candidate["patterns"]}
    if set(pieces) != set(expected):
        return False

    for part_id, paths in expected.items():
        part = pieces[part_id]
        found = {path.get("id"): path for path in part.get("sew_paths", [])}
        if set(found) != set(paths) or not all(_path_ok(found[key], pts) for key, pts in paths.items()):
            return False
        fold_expected = {
            "TOP_FOLD_1": [[0.0, q - 5.0], [part["width_mm"], q - 5.0]],
            "TOP_FOLD_2": [[0.0, q - 10.0], [part["width_mm"], q - 10.0]],
        }
        folds = {path.get("id"): path for path in part.get("fold_paths", [])}
        hems = {path.get("id"): path for path in part.get("hem_paths", [])}
        if set(folds) != set(fold_expected) or any(not _path_ok(folds[key], pts) for key, pts in fold_expected.items()):
            return False
        if set(hems) != {"HEM_STITCH"} or not _path_ok(hems["HEM_STITCH"], [[0.0, q - 14.0], [part["width_mm"], q - 14.0]]):
            return False
        expected_notches = (
            {"FRONT_L": [[half + 10.0, 0.0], [half + 10.0, q], [10.0, 0.0]], "FRONT_R": [[10.0, 0.0], [10.0, q], [half + 10.0, 0.0]]}.get(part_id)
            if split
            else None
        )
        expected_notches = expected_notches or [[10.0, 0.0], [p / 2.0, 0.0], [p - 10.0, 0.0], [10.0, q], [p - 10.0, q]]
        actual_notches = part.get("notches_mm", [])
        if len(actual_notches) != len(expected_notches) or any(math.dist(a, b) > 0.1 + EPS for a, b in zip(actual_notches, expected_notches)):
            return False
        if part.get("grain") != "along_long_edge":
            return False

    connections = candidate.get("seam_connections")
    expected_pairs = {
        "S_LEFT": ("FRONT_L.OUTER_LEFT_SEW" if split else "FRONT.LEFT_SEW", "BACK.RIGHT_SEW"),
        "S_RIGHT": ("FRONT_R.OUTER_RIGHT_SEW" if split else "FRONT.RIGHT_SEW", "BACK.LEFT_SEW"),
    }
    if split:
        expected_pairs.update({
            "JOIN_FRONT": ("FRONT_L.JOIN_FRONT", "FRONT_R.JOIN_FRONT"),
            "S_BOTTOM_LEFT": ("FRONT_L.BOTTOM_LEFT", "BACK.BOTTOM_SEW"),
            "S_BOTTOM_RIGHT": ("FRONT_R.BOTTOM_RIGHT", "BACK.BOTTOM_SEW"),
        })
    else:
        expected_pairs["S_BOTTOM"] = ("FRONT.BOTTOM_SEW", "BACK.BOTTOM_SEW")

    if not isinstance(connections, list) or {c.get("seam_id") for c in connections} != set(expected_pairs):
        return False
    for conn in connections:
        sid = conn.get("seam_id")
        if (conn.get("front"), conn.get("back")) != expected_pairs[sid]:
            return False
        if sid == "JOIN_FRONT":
            if conn.get("before") != "hem" or abs(conn.get("raw_length_mm", 0) - q) > 0.1:
                return False
        elif sid == "S_BOTTOM":
            if conn.get("orientation") != "reversed":
                return False
        elif sid.startswith("S_BOTTOM"):
            mp = conn.get("midpoint", [0.0, 0.0])
            if math.dist(mp, [p / 2.0, 10.0]) > 0.1:
                return False
    return True


def validate_candidate(data, candidate, current_signature, consent=None, activation=False):
    report = {
        "config_version": CONFIG["version"],
        "input_signature": current_signature,
        "checks": [],
        "failures": [],
        "passed": False,
        "geometry_passed": False,
    }

    def record(num, good, msg):
        entry = {"id": f"VAL-{num:02d}", "status": "pass" if good else "fail", "message": msg}
        report["checks"].append(entry)
        if not good:
            report["failures"].append(entry)

    try:
        data = validate_input(data)
        if not isinstance(candidate, dict):
            raise EngineError("INVALID_INPUT", "Kandidat harus berupa objek")
        variant = candidate.get("variant")
        if variant not in t.SUPPORTED_VARIANTS:
            raise EngineError("INVALID_INPUT", f"Varian tidak dikenal: {variant}")
        pieces = candidate.get("patterns")
        placements = candidate.get("placements")
        if not isinstance(pieces, list) or not isinstance(placements, list):
            raise EngineError("INVALID_INPUT", "Pola atau penempatan harus berupa list")
        pattern_ids = [p.get("id") for p in pieces if isinstance(p, dict)]
        if len(pattern_ids) != len(pieces) or len(set(pattern_ids)) != len(pieces):
            raise EngineError("INVALID_INPUT", "Pola memiliki ID duplikat atau tidak valid")
        placement_instances = [pl.get("instance_id") for pl in placements if isinstance(pl, dict)]
        if len(placement_instances) != len(placements) or len(set(placement_instances)) != len(placements):
            raise EngineError("INVALID_INPUT", "Penempatan memiliki instance_id duplikat atau tidak valid")

        w = data["request"]["W_mm"]
        h = data["request"]["H_mm"]
        dims = {"BACK": (w + 20.0, h + 20.0)}
        if variant == "front_split_center":
            dims.update({"FRONT_L": (w / 2.0 + 20.0, h + 20.0), "FRONT_R": (w / 2.0 + 20.0, h + 20.0)})
        else:
            dims["FRONT"] = (w + 20.0, h + 20.0)

        if set(pattern_ids) != set(dims):
            raise EngineError("INVALID_INPUT", "Komponen pola tidak sesuai kebutuhan varian")

        for part in pieces:
            pid = part["id"]
            ew, eh = dims[pid]
            if not finite(part.get("width_mm")) or not finite(part.get("height_mm")):
                raise EngineError("INVALID_INPUT", "Dimensi pola harus angka terbatas")
            if abs(part["width_mm"] - ew) > EPS or abs(part["height_mm"] - eh) > EPS:
                raise EngineError("INVALID_INPUT", "Dimensi pola berbeda dari spesifikasi")
            cut_ring = part.get("cut_mm")
            cut_poly = polygon(cut_ring)
            if len(cut_ring) != 4 or abs(cut_poly.area - ew * eh) > EPS:
                raise EngineError("INVALID_INPUT", "Cincin potong tidak valid")
            expected_ring = [[0.0, 0.0], [ew, 0.0], [ew, eh], [0.0, eh]]
            if any(math.dist(a, b) > EPS for a, b in zip(cut_ring, expected_ring)):
                raise EngineError("INVALID_INPUT", "Titik cincin potong tidak cocok dengan sudut baku")

        allowed_place_keys = {"instance_id", "material_id", "rotation_deg", "tx_mm", "ty_mm"}
        for pl in placements:
            if not isinstance(pl, dict) or set(pl) != allowed_place_keys:
                raise EngineError("INVALID_INPUT", "Field penempatan tidak valid atau ada field tambahan")
            for k in ("rotation_deg", "tx_mm", "ty_mm"):
                if not finite(pl[k]):
                    raise EngineError("INVALID_INPUT", f"Nilai penempatan {k} harus terbatas")

        record(1, True, "Bentuk, angka dan parameter domain valid")

        materials = {m["id"]: m for m in data["materials"]}
        parts = {p["id"]: p for p in pieces}

        geo = []
        for pl in placements:
            if pl["instance_id"] in parts and pl["material_id"] in materials:
                poly = placed_polygon(parts[pl["instance_id"]]["cut_mm"], pl["rotation_deg"], pl["tx_mm"], pl["ty_mm"])
                geo.append((pl, poly))

        val_02_ok = len(geo) == len(placements) and all(
            polygon(materials[pl["material_id"]]["outer_mm"]).covers(poly)
            and poly.distance(polygon(materials[pl["material_id"]]["outer_mm"]).boundary) >= CLEARANCE - EPS
            for pl, poly in geo
        )
        record(2, val_02_ok, "Footprint di dalam kain, margin 2 mm")

        val_03_ok = all(
            not poly.intersects(polygon(defect)) and poly.distance(polygon(defect)) >= CLEARANCE - EPS
            for pl, poly in geo
            for defect in materials[pl["material_id"]]["defects_mm"]
        )
        record(3, val_03_ok, "Jarak dari cacat minimal 2 mm")

        val_04_ok = all(
            not poly_a.intersects(poly_b) and poly_a.distance(poly_b) >= CLEARANCE - EPS
            for (pl_a, poly_a), (pl_b, poly_b) in itertools.combinations(geo, 2)
            if pl_a["material_id"] == pl_b["material_id"]
        )
        record(4, val_04_ok, "Jarak antar pola minimal 2 mm")

        groups = {materials[pl["material_id"]]["compatible_group"] for pl, _ in geo}
        colors = {resolve(materials[pl["material_id"]]["color"])["family"] for pl, _ in geo}
        rot_ok = all(pl["rotation_deg"] in materials[pl["material_id"]]["allowed_rotations_deg"] for pl, _ in geo)
        face_ok = all(materials[pl["material_id"]]["face"] == "right_side_up" for pl, _ in geo)
        type_ok = all(materials[pl["material_id"]]["material_type"] == "woven_non_stretch" for pl, _ in geo)
        color_ok = data["request"]["allow_mixed"] or len(colors) <= 1
        val_05_ok = len(geo) == len(placements) and len(groups) <= 1 and rot_ok and face_ok and type_ok and color_ok
        record(5, val_05_ok, "Rotasi, bahan dan warna sesuai")

        all_instances = sorted(pl["instance_id"] for pl in placements) == sorted(dims)
        all_avail = all(materials[pl["material_id"]]["available"] for pl, _ in geo)
        val_06_ok = len(geo) == len(placements) and all_instances and all_avail
        record(6, val_06_ok, "Komponen lengkap, bahan tersedia")

        expected_area = sum(w_i * h_i for w_i, h_i in dims.values())
        seams_ok = _seams(candidate, w, h)
        area_ok = abs(candidate.get("cut_area_mm2", -1) - expected_area) <= 0.1
        extra_seams_ok = candidate.get("extra_seams") == (1 if variant == "front_split_center" else 0)
        extra_len_ok = abs(candidate.get("extra_raw_sew_length_mm", 0) - (h + 20.0 if variant == "front_split_center" else 0)) <= 0.1
        steps_ok = isinstance(candidate.get("assembly_steps"), list) and len(candidate.get("assembly_steps", [])) >= 5
        val_07_ok = seams_ok and area_ok and extra_seams_ok and extra_len_ok and steps_ok
        record(7, val_07_ok, "Jalur jahit, lipatan, pairing dan ukuran jadi cocok")

        permitted = variant != "front_split_center" or data["request"]["max_front_splits"] == 1
        if variant == "front_split_center":
            consented = (
                isinstance(consent, dict)
                and consent.get("accepted") is True
                and consent.get("additional_seams") == 1
                and consent.get("variant") in (None, "front_split_center")
                and candidate.get("change_acceptance") == consent
            )
            if permitted and consented:
                record(8, True, "Ukuran wajib dan persetujuan perubahan diterima")
            elif permitted and not activation:
                report["checks"].append({"id": "VAL-08", "status": "pending", "message": "Menunggu persetujuan satu sambungan tambahan"})
            else:
                record(8, False, "Persetujuan satu sambungan tambahan belum diberikan atau tidak valid")
        else:
            if candidate.get("change_acceptance") is not None:
                record(8, False, "Varian utuh tidak memerlukan atau mengizinkan change_acceptance")
            elif consent is not None and (not isinstance(consent, dict) or consent.get("accepted") is not True or consent.get("additional_seams", 0) != 0 or consent.get("variant") not in (None, "whole")):
                record(8, False, "Persetujuan perubahan tidak cocok dengan varian utuh")
            else:
                record(8, permitted, "Ukuran wajib terpenuhi")

        expected_sig = signature(data, consent)
        sig_ok = candidate.get("input_signature") == current_signature == expected_sig
        tmpl_ok = candidate.get("template_id") == t.TEMPLATE_ID and candidate.get("template_version") == t.TEMPLATE_VERSION
        status_ok = candidate.get("construction_status") == t.REVIEW_STATUS
        record(9, sig_ok and tmpl_ok and status_ok, "Signature input, template dan konfigurasi cocok")

        req_confirmed = data["request"]["confirmed"] is True
        mat_confirmed = all(materials[pl["material_id"]]["confirmed"] is True for pl, _ in geo)
        record(10, req_confirmed and mat_confirmed, "Bahan terpakai dan request terkonfirmasi")

    except (EngineError, KeyError, TypeError, ValueError, OverflowError) as exc:
        if not any(c["id"] == "VAL-01" for c in report["checks"]):
            record(1, False, str(exc))
        for number in range(2, 11):
            if not any(c["id"] == f"VAL-{number:02d}" for c in report["checks"]):
                record(number, False, "Kandidat tidak dapat diperiksa")

    report["checks"].sort(key=lambda check: check["id"])
    recorded = {c["id"] for c in report["checks"]}
    for number in range(1, 11):
        cid = f"VAL-{number:02d}"
        if cid not in recorded:
            report["checks"].append({"id": cid, "status": "fail", "message": "Pemeriksaan tidak lengkap"})
    report["checks"].sort(key=lambda check: check["id"])

    report["passed"] = len(report["checks"]) == 10 and all(c["status"] == "pass" for c in report["checks"])
    val_08 = next((c for c in report["checks"] if c["id"] == "VAL-08"), None)
    other_pass = len(report["checks"]) == 10 and all(c["status"] == "pass" for c in report["checks"] if c["id"] != "VAL-08")
    report["geometry_passed"] = other_pass and (val_08 is not None and val_08["status"] in ("pass", "pending"))
    report["failures"] = [c for c in report["checks"] if c["status"] != "pass"]
    return report


def search_layout(data, input_signature, variants=None, max_checks=50000, timeout_s=5):
    try:
        data = validate_input(data)
    except EngineError as exc:
        return {"ok": False, "code": exc.code, "message": exc.message, "search": {"checks_used": 0}}

    started = time.monotonic()
    meta = {
        "checks_used": 0,
        "orders_used": 0,
        "variants": [],
        "grid_mm": 1,
        "limit_checks_per_variant": max_checks,
        "limit_seconds_per_variant": timeout_s,
    }

    if not data["request"]["confirmed"] or any(not m["confirmed"] for m in data["materials"] if m["available"]):
        return {"ok": False, "code": "UNCONFIRMED_INPUT", "message": "Konfirmasikan pengukuran dan kebutuhan", "search": meta}

    order = variants if variants is not None else ["whole"] + (["front_split_center"] if data["request"]["max_front_splits"] else [])
    if not order or len(order) > 2 or any(v not in t.SUPPORTED_VARIANTS for v in order) or ("front_split_center" in order and not data["request"]["max_front_splits"]):
        return {"ok": False, "code": "UNSUPPORTED_CONSTRUCTION", "message": "Varian tidak diizinkan", "search": meta}

    available = [m for m in data["materials"] if m["available"]]
    avail_area = sum(raw_area(m) for m in available)
    min_area = min(generate_patterns(data["request"], v)["cut_area_mm2"] for v in order)
    if avail_area < min_area - EPS:
        return {
            "ok": False,
            "code": "AREA_LOWER_BOUND_FAIL",
            "message": f"Luas mentah tersedia {avail_area:.0f} mm² < minimum {min_area:.0f} mm²",
            "search": {**meta, "available_area_mm2": avail_area, "minimum_area_mm2": min_area},
        }

    mat_polys = {m["id"]: polygon(m["outer_mm"]) for m in available}
    mat_bounds = {m["id"]: mat_polys[m["id"]].bounds for m in available}
    mat_defects = {m["id"]: [rectangle(d) for d in m["defects_mm"]] for m in available}
    mat_raw_areas = {m["id"]: raw_area(m) for m in available}

    for variant in order:
        tick = time.monotonic()
        count = 0
        generated = generate_patterns(data["request"], variant)
        patterns = generated["patterns"]

        can_fit = {}
        for part in patterns:
            pw, ph = part["width_mm"], part["height_mm"]
            can_fit[part["id"]] = set()
            for m in available:
                if mat_raw_areas[m["id"]] < pw * ph - EPS:
                    continue
                mb = mat_bounds[m["id"]]
                mw, mh = mb[2] - mb[0], mb[3] - mb[1]
                for rot in m["allowed_rotations_deg"]:
                    rw, rh = (pw, ph) if rot == 0 else (ph, pw)
                    if rw <= mw - 4.0 + EPS and rh <= mh - 4.0 + EPS:
                        can_fit[part["id"]].add(m["id"])
                        break

        if any(len(can_fit[p["id"]]) == 0 for p in patterns):
            meta["variants"].append({"variant": variant, "checks": 0, "elapsed_s": round(time.monotonic() - tick, 3), "stop_reason": "pruned_unfit"})
            continue

        part_areas = {p["id"]: p["width_mm"] * p["height_mm"] for p in patterns}
        all_candidate_mats = set.union(*can_fit.values())
        if sum(mat_raw_areas[mid] for mid in all_candidate_mats) < sum(part_areas.values()) - EPS:
            meta["variants"].append({"variant": variant, "checks": 0, "elapsed_s": round(time.monotonic() - tick, 3), "stop_reason": "pruned_area"})
            continue

        if variant == "whole" and len(patterns) == 2 and patterns[0]["id"] in ("FRONT", "BACK"):
            fit_both = [m for m in available if m["id"] in can_fit["FRONT"] and m["id"] in can_fit["BACK"]]
            if not fit_both or (len(fit_both) == 1 and mat_raw_areas[fit_both[0]["id"]] < 2 * part_areas["FRONT"] - EPS and len(available) <= 1):
                meta["variants"].append({"variant": variant, "checks": 0, "elapsed_s": round(time.monotonic() - tick, 3), "stop_reason": "pruned_capacity"})
                continue
            if len(available) > 1 and len(fit_both) == 1 and mat_raw_areas[fit_both[0]["id"]] < 2 * part_areas["FRONT"] - EPS:
                other_fit = [m for m in available if m["id"] != fit_both[0]["id"] and (m["id"] in can_fit["FRONT"] or m["id"] in can_fit["BACK"])]
                if not other_fit:
                    meta["variants"].append({"variant": variant, "checks": 0, "elapsed_s": round(time.monotonic() - tick, 3), "stop_reason": "pruned_capacity"})
                    continue

        basic = sorted(patterns, key=lambda p: (-p["width_mm"] * p["height_mm"], 0 if p["role"].startswith("front") else 1, p["id"]))
        orders = list(itertools.islice(itertools.permutations(basic), 6))
        meta["orders_used"] += len(orders)
        front_color = data["request"]["preferences"]["front_color"]

        for ordered in orders:
            chosen = []
            chosen_geo = []

            def visit(index):
                nonlocal count
                if index == len(ordered):
                    return True
                part = ordered[index]
                eligible = [m for m in available if m["id"] in can_fit[part["id"]]]
                sorted_materials = sorted(eligible, key=lambda m: (0 if part["role"].startswith("front") and same_family(m["color"], front_color) else 1, m["id"]))

                for mat in sorted_materials:
                    placed_here = [geo for pl, geo in chosen_geo if pl["material_id"] == mat["id"]]
                    used_area_here = sum(part_areas[pl["instance_id"]] for pl, _ in chosen_geo if pl["material_id"] == mat["id"])
                    if used_area_here + part_areas[part["id"]] > mat_raw_areas[mat["id"]] + EPS:
                        continue

                    bounds = mat_bounds[mat["id"]]
                    outer = mat_polys[mat["id"]]
                    defects = mat_defects[mat["id"]]

                    for rotation in mat["allowed_rotations_deg"]:
                        rotated = placed_polygon(part["cut_mm"], rotation, 0, 0)
                        xmin, ymin, xmax, ymax = rotated.bounds
                        min_tx = math.ceil(bounds[0] + CLEARANCE - xmin)
                        max_tx = math.floor(bounds[2] - CLEARANCE - xmax)
                        min_ty = math.ceil(bounds[1] + CLEARANCE - ymin)
                        max_ty = math.floor(bounds[3] - CLEARANCE - ymax)

                        for tx in range(min_tx, max_tx + 1):
                            for ty in range(min_ty, max_ty + 1):
                                if count >= max_checks or time.monotonic() - tick >= timeout_s:
                                    return False
                                count += 1
                                footprint = placed_polygon(part["cut_mm"], rotation, tx, ty)
                                if not outer.covers(footprint) or footprint.distance(outer.boundary) < CLEARANCE - EPS:
                                    continue
                                if any(footprint.intersects(d) or footprint.distance(d) < CLEARANCE - EPS for d in defects):
                                    continue
                                if any(footprint.intersects(other) or footprint.distance(other) < CLEARANCE - EPS for other in placed_here):
                                    continue

                                pl_dict = {"instance_id": part["id"], "material_id": mat["id"], "rotation_deg": rotation, "tx_mm": tx, "ty_mm": ty}
                                chosen.append(pl_dict)
                                chosen_geo.append((pl_dict, footprint))

                                if visit(index + 1):
                                    return True

                                chosen.pop()
                                chosen_geo.pop()
                return False

            if visit(0):
                candidate = {
                    **generated,
                    "placements": sorted(chosen, key=lambda p: p["instance_id"]),
                    "input_signature": input_signature,
                    "change_acceptance": None,
                    "finished_size_mm": [data["request"]["W_mm"], data["request"]["H_mm"]],
                }
                report = validate_candidate(data, candidate, input_signature)
                if report["geometry_passed"]:
                    meta["variants"].append({"variant": variant, "checks": count, "elapsed_s": round(time.monotonic() - tick, 3), "stop_reason": "first_feasible"})
                    meta["checks_used"] += count
                    meta["elapsed_s"] = round(time.monotonic() - started, 3)
                    return {"ok": True, "candidate": candidate, "search": meta}

            if count >= max_checks or time.monotonic() - tick >= timeout_s:
                break

        meta["variants"].append({
            "variant": variant,
            "checks": count,
            "elapsed_s": round(time.monotonic() - tick, 3),
            "stop_reason": "bounded_exhaustion" if count >= max_checks or time.monotonic() - tick >= timeout_s else "grid_exhausted",
        })
        meta["checks_used"] += count

    meta["elapsed_s"] = round(time.monotonic() - started, 3)
    return {"ok": False, "code": "NO_LAYOUT_WITHIN_BUDGET", "message": "Tidak ditemukan tata letak dalam batas pencarian; bukan bukti mustahil", "search": meta}


def metrics(data, candidate):
    config = CONFIG
    segments = int(config.get("buffer_quadrant_segments", 16))
    selected = {pl["material_id"] for pl in candidate["placements"]}
    rows = []
    for material in data["materials"]:
        outer = Polygon(material["outer_mm"])
        defects = [Polygon(d) for d in material["defects_mm"]]
        forbidden = unary_union([d.buffer(config.get("defect_mm", 2.0), quad_segs=segments) for d in defects])
        usable = outer.buffer(-config.get("edge_mm", 2.0), quad_segs=segments, join_style=1).difference(forbidden).area
        raw = outer.area - unary_union(defects).area
        rows.append({
            "id": material["id"],
            "raw_mm2": raw,
            "usable_mm2": usable,
            "selected": material["id"] in selected,
            "available": material["available"],
        })
    cut = sum(Polygon(p["cut_mm"]).area for p in candidate["patterns"])
    usable = sum(row["usable_mm2"] for row in rows if row["selected"])
    return {
        "materials": rows,
        "cut_mm2": cut,
        "selected_usable_mm2": usable,
        "utilization_percent": 100 * cut / usable if usable else 0,
        "available_raw_mm2": sum(row["raw_mm2"] for row in rows if row["available"]),
        "inventory_raw_mm2": sum(row["raw_mm2"] for row in rows),
        "buffer_quadrant_segments": segments,
    }
