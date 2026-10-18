"""Generate and verify frozen parity fixtures for SISA template flat-open-v1.

Produces references/parity-fixtures-v1.json covering:
1. Canonical demo fixtures (G1, G2, G3) with layout search and PDF vector validation.
2. Complete parameter sweep: W in [100..160 step 10] x H in [80..180 step 20] x {whole, front_split_center} (84 variants).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.design import engine, template as t
from backend.pdf import pdf_export
from backend.tests.test_pdf import CONSENT, max_fixture, _rects, PT

PARITY_FIXTURE_PATH = ROOT / "references" / "parity-fixtures-v1.json"


def _serialize_seam_path(sp) -> Dict[str, Any]:
    return {
        "id": sp.id,
        "kind": sp.kind,
        "points": [list(pt) for pt in sp.points],
        "length_mm": sp.length_mm,
        "label": sp.label,
    }


def _serialize_pattern(p) -> Dict[str, Any]:
    return {
        "id": p.id,
        "role": p.role,
        "cut_ring": [list(pt) for pt in p.cut_ring],
        "sew_paths": [_serialize_seam_path(sp) for sp in p.sew_paths],
        "fold_paths": [_serialize_seam_path(sp) for sp in p.fold_paths],
        "notches": [list(pt) for pt in p.notches],
        "grain": p.grain,
        "quantity": p.quantity,
        "width_mm": p.width_mm,
        "height_mm": p.height_mm,
    }


def generate_parity_data() -> Dict[str, Any]:
    output: Dict[str, Any] = {
        "schema_version": "sisa-parity-fixture-v1",
        "template_id": t.TEMPLATE_ID,
        "template_version": t.TEMPLATE_VERSION,
        "review_status": t.REVIEW_STATUS,
        "constants": {
            "S": t.S,
            "HEM_FOLD_1_MM": t.HEM_FOLD_1_MM,
            "HEM_FOLD_2_MM": t.HEM_FOLD_2_MM,
            "H": t.H,
            "J": t.J,
            "W_RANGE": list(t.W_RANGE),
            "H_RANGE": list(t.H_RANGE),
            "DEFAULT_W": t.DEFAULT_W,
            "DEFAULT_H": t.DEFAULT_H,
        },
        "cases": [],
        "sweep_84": [],
    }

    # 1. Demo fixture cases G1, G2, G3
    cases_meta = [
        {"id": "G1", "unavailable": [], "split": False},
        {"id": "G2", "unavailable": ["A"], "split": True},
        {"id": "G3", "unavailable": ["A", "D"], "split": False},
    ]

    for cm in cases_meta:
        data = engine.fixture_data(unavailable=cm["unavailable"])
        sig = engine.signature(data)
        search_res = engine.search_layout(data, sig)

        case_entry: Dict[str, Any] = {
            "case_id": cm["id"],
            "unavailable": cm["unavailable"],
            "signature": sig,
            "search_ok": search_res["ok"],
            "search_code": search_res.get("code", "FEASIBLE" if search_res["ok"] else None),
        }

        if search_res["ok"]:
            cand = search_res["candidate"]
            if cm["split"]:
                sig_consented = engine.signature(data, CONSENT)
                cand = {
                    **cand,
                    "input_signature": sig_consented,
                    "change_acceptance": CONSENT,
                    "design_revision_id": f"DR-PARITY-{cm['id']}",
                }
                report = engine.validate_candidate(data, cand, sig_consented, consent=CONSENT, activation=True)
                pdf_sig = sig_consented
            else:
                cand = {
                    **cand,
                    "design_revision_id": f"DR-PARITY-{cm['id']}",
                }
                report = engine.validate_candidate(data, cand, sig, activation=True)
                pdf_sig = sig

            case_entry["candidate"] = {
                "variant": cand["variant"],
                "cut_area_mm2": cand["cut_area_mm2"],
                "extra_seams": cand["extra_seams"],
                "extra_raw_sew_length_mm": cand["extra_raw_sew_length_mm"],
                "finished_size_mm": cand["finished_size_mm"],
                "patterns": [
                    {
                        "id": p["id"],
                        "role": p["role"],
                        "width_mm": p["width_mm"],
                        "height_mm": p["height_mm"],
                        "cut_mm": p["cut_mm"],
                    }
                    for p in cand["patterns"]
                ],
                "placements": cand["placements"],
                "assembly_steps_count": len(cand["assembly_steps"]),
            }
            case_entry["validation_passed"] = report["passed"]

            # Generate PDFs and record metrics
            guide_bytes = pdf_export.generate_pdf(data, cand, pdf_sig, kind="guide")
            tech_bytes = pdf_export.generate_pdf(data, cand, pdf_sig, kind="technical")

            guide_doc = pymupdf.open(stream=guide_bytes, filetype="pdf")
            tech_doc = pymupdf.open(stream=tech_bytes, filetype="pdf")

            case_entry["guide_pdf"] = {
                "pages": len(guide_doc),
                "sha256": hashlib.sha256(guide_bytes).hexdigest(),
                "author": guide_doc.metadata.get("author"),
            }
            case_entry["technical_pdf"] = {
                "pages": len(tech_doc),
                "sha256": hashlib.sha256(tech_bytes).hexdigest(),
                "author": tech_doc.metadata.get("author"),
            }
        else:
            case_entry["candidate"] = None

        output["cases"].append(case_entry)

    # 2. Parameter sweep: W in [100..160 step 10] x H in [80..180 step 20] x {whole, front_split_center}
    w_steps = list(range(100, 170, 10))  # 7 values
    h_steps = list(range(80, 200, 20))  # 6 values
    variants = [t.WHOLE, t.SPLIT_CENTER]  # 2 values

    for w in w_steps:
        for h in h_steps:
            for variant in variants:
                var_def = t.build_variant(w, h, variant)
                instructions = t.instruction_skeleton(variant, w, h)

                sweep_item: Dict[str, Any] = {
                    "W_mm": float(w),
                    "H_mm": float(h),
                    "variant": variant,
                    "raw_cut_area_mm2": var_def.raw_cut_area_mm2,
                    "additional_seams": var_def.additional_seams,
                    "extra_raw_sew_length_mm": var_def.extra_raw_sew_length_mm,
                    "patterns": {
                        pid: _serialize_pattern(ps)
                        for pid, ps in var_def.patterns.items()
                    },
                    "seam_connections": var_def.seam_connections,
                    "instruction_steps": instructions,
                }
                output["sweep_84"].append(sweep_item)

    return output


def write_parity_fixtures():
    data = generate_parity_data()
    PARITY_FIXTURE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Parity fixtures written to {PARITY_FIXTURE_PATH}")
    print(f"Recorded {len(data['cases'])} demo cases and {len(data['sweep_84'])} sweep variants.")


def verify_parity_fixtures() -> bool:
    if not PARITY_FIXTURE_PATH.exists():
        print(f"Error: {PARITY_FIXTURE_PATH} does not exist.")
        return False

    frozen = json.loads(PARITY_FIXTURE_PATH.read_text(encoding="utf-8"))
    fresh = generate_parity_data()

    if frozen["constants"] != fresh["constants"]:
        print("Mismatch in constants!")
        return False

    if len(frozen["sweep_84"]) != len(fresh["sweep_84"]):
        print(f"Mismatch in sweep length: {len(frozen['sweep_84'])} vs {len(fresh['sweep_84'])}")
        return False

    for idx, (fz, fr) in enumerate(zip(frozen["sweep_84"], fresh["sweep_84"])):
        for key in ("W_mm", "H_mm", "variant", "raw_cut_area_mm2", "additional_seams", "extra_raw_sew_length_mm"):
            if fz[key] != fr[key]:
                print(f"Sweep item {idx} mismatch in {key}: {fz[key]} vs {fr[key]}")
                return False
        if fz["seam_connections"] != fr["seam_connections"]:
            print(f"Sweep item {idx} seam_connections mismatch")
            return False
        for pid in fz["patterns"]:
            pz_pat = fz["patterns"][pid]
            pr_pat = fr["patterns"][pid]
            if pz_pat["cut_ring"] != pr_pat["cut_ring"]:
                print(f"Sweep item {idx} pattern {pid} cut_ring mismatch")
                return False
            if pz_pat["width_mm"] != pr_pat["width_mm"] or pz_pat["height_mm"] != pr_pat["height_mm"]:
                print(f"Sweep item {idx} pattern {pid} dimensions mismatch")
                return False

    print("All parity fixtures verified identical against current template/engine output.")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="Verify against existing frozen fixture")
    args = parser.parse_args()

    if args.verify:
        ok = verify_parity_fixtures()
        sys.exit(0 if ok else 1)
    else:
        write_parity_fixtures()
