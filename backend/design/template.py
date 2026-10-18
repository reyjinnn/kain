from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, List, Tuple

TEMPLATE_ID = "flat-open-v1"
TEMPLATE_VERSION = "0.2.1"
WHOLE = "whole"
SPLIT_CENTER = "front_split_center"
SUPPORTED_VARIANTS = (WHOLE, SPLIT_CENTER)
REVIEW_STATUS = "draft"
S = 10.0
HEM_FOLD_1_MM = 5.0
HEM_FOLD_2_MM = 5.0
H = HEM_FOLD_1_MM + HEM_FOLD_2_MM
J = 10.0
W_RANGE = (100.0, 160.0)
H_RANGE = (80.0, 180.0)
DEFAULT_W = 160.0
DEFAULT_H = 120.0


@dataclass(frozen=True)
class SeamPath:
    id: str
    kind: str
    points: Tuple[Tuple[float, float], ...]
    length_mm: float
    label: str = ""


@dataclass
class PatternSpec:
    id: str
    role: str
    cut_ring: Tuple[Tuple[float, float], ...]
    sew_paths: List[SeamPath]
    fold_paths: List[SeamPath]
    notches: List[Tuple[float, float]]
    grain: str = "along_long_edge"
    quantity: int = 1
    width_mm: float = 0.0
    height_mm: float = 0.0

    @property
    def dimensions(self) -> Tuple[float, float]:
        return self.width_mm, self.height_mm


@dataclass
class VariantDefinition:
    name: str
    patterns: Dict[str, PatternSpec]
    seam_connections: List[Dict[str, object]]
    raw_cut_area_mm2: float
    additional_seams: int = 0
    extra_raw_sew_length_mm: float = 0.0


def Q_minus_H(q: float, h: float) -> float:
    return q - h


def _side_paths(p: float, q: float) -> Tuple[List[SeamPath], List[SeamPath]]:
    sew = [
        SeamPath("LEFT_SEW", "sew", ((S, S), (S, q - H)), q - S - H),
        SeamPath("RIGHT_SEW", "sew", ((p - S, S), (p - S, q - H)), q - S - H),
        SeamPath("BOTTOM_SEW", "sew", ((S, S), (p - S, S)), p - 2 * S),
    ]
    folds = [
        SeamPath("TOP_FOLD_1", "fold", ((0.0, q - 5.0), (p, q - 5.0)), p),
        SeamPath("TOP_FOLD_2", "fold", ((0.0, q - H), (p, q - H)), p),
        SeamPath("HEM_STITCH", "hem", ((0.0, q - 14.0), (p, q - 14.0)), p),
    ]
    return sew, folds


def _panel(pid, role, p, q, sew=None, notches=None):
    sides, folds = _side_paths(p, q)
    return PatternSpec(
        id=pid,
        role=role,
        cut_ring=((0.0, 0.0), (p, 0.0), (p, q), (0.0, q)),
        sew_paths=sides if sew is None else sew,
        fold_paths=folds,
        notches=notches if notches is not None else [(S, 0.0), (p / 2.0, 0.0), (p - S, 0.0), (S, q), (p - S, q)],
        width_mm=p,
        height_mm=q,
    )


def build_variant(w_mm: float, h_mm: float, variant: str = WHOLE) -> VariantDefinition:
    if type(w_mm) not in (int, float) or not math.isfinite(w_mm) or not W_RANGE[0] <= w_mm <= W_RANGE[1]:
        raise ValueError("W outside domain 100-160 mm")
    if type(h_mm) not in (int, float) or not math.isfinite(h_mm) or not H_RANGE[0] <= h_mm <= H_RANGE[1]:
        raise ValueError("H outside domain 80-180 mm")
    if variant not in SUPPORTED_VARIANTS:
        raise ValueError("unsupported construction variant")
    p, q = float(w_mm) + 2 * S, float(h_mm) + S + H
    back = _panel("BACK", "back", p, q)
    if variant == WHOLE:
        patterns = {"FRONT": _panel("FRONT", "front", p, q), "BACK": back}
        connections = [
            {"seam_id": "S_LEFT", "front": "FRONT.LEFT_SEW", "back": "BACK.RIGHT_SEW", "orientation": "same_direction"},
            {"seam_id": "S_RIGHT", "front": "FRONT.RIGHT_SEW", "back": "BACK.LEFT_SEW", "orientation": "same_direction"},
            {"seam_id": "S_BOTTOM", "front": "FRONT.BOTTOM_SEW", "back": "BACK.BOTTOM_SEW", "orientation": "reversed",
             "front_span_mm": [[S, S], [p - S, S]], "back_span_mm": [[p - S, S], [S, S]], "midpoint": [p / 2.0, S]},
        ]
        return VariantDefinition(WHOLE, patterns, connections, 2 * p * q)
    lp = float(w_mm) / 2.0 + S + J
    left_sew = [
        SeamPath("OUTER_LEFT_SEW", "sew", ((S, S), (S, q - H)), q - S - H),
        SeamPath("BOTTOM_LEFT", "sew", ((S, S), (lp - J, S)), lp - J - S),
        SeamPath("JOIN_FRONT", "sew", ((lp - J, 0.0), (lp - J, q)), q),
    ]
    right_sew = [
        SeamPath("OUTER_RIGHT_SEW", "sew", ((lp - S, S), (lp - S, q - H)), q - S - H),
        SeamPath("BOTTOM_RIGHT", "sew", ((J, S), (lp - S, S)), lp - S - J),
        SeamPath("JOIN_FRONT", "sew", ((J, 0.0), (J, q)), q),
    ]
    patterns = {
        "FRONT_L": _panel("FRONT_L", "front_left", lp, q, left_sew, [(lp - J, 0.0), (lp - J, q), (S, 0.0)]),
        "FRONT_R": _panel("FRONT_R", "front_right", lp, q, right_sew, [(J, 0.0), (J, q), (lp - S, 0.0)]),
        "BACK": back,
    }
    connections = [
        {"seam_id": "JOIN_FRONT", "front": "FRONT_L.JOIN_FRONT", "back": "FRONT_R.JOIN_FRONT", "orientation": "same_direction", "before": "hem", "raw_length_mm": q},
        {"seam_id": "S_LEFT", "front": "FRONT_L.OUTER_LEFT_SEW", "back": "BACK.RIGHT_SEW", "orientation": "same_direction"},
        {"seam_id": "S_RIGHT", "front": "FRONT_R.OUTER_RIGHT_SEW", "back": "BACK.LEFT_SEW", "orientation": "same_direction"},
        {"seam_id": "S_BOTTOM_LEFT", "front": "FRONT_L.BOTTOM_LEFT", "back": "BACK.BOTTOM_SEW", "orientation": "span_left", "midpoint": [p / 2.0, S],
         "front_span_mm": [[S, S], [lp - J, S]], "back_span_mm": [[p - S, S], [p / 2.0, S]]},
        {"seam_id": "S_BOTTOM_RIGHT", "front": "FRONT_R.BOTTOM_RIGHT", "back": "BACK.BOTTOM_SEW", "orientation": "span_right", "midpoint": [p / 2.0, S],
         "front_span_mm": [[J, S], [lp - S, S]], "back_span_mm": [[p / 2.0, S], [S, S]]},
    ]
    return VariantDefinition(SPLIT_CENTER, patterns, connections, 2 * lp * q + p * q, 1, q)


def instruction_skeleton(variant: str, w_mm: float, h_mm: float) -> List[Dict[str, object]]:
    build_variant(w_mm, h_mm, variant)
    p, q = w_mm + 20.0, h_mm + 20.0
    front = "FRONT" if variant == WHOLE else "depan gabungan FRONT_L/FRONT_R"
    common = [
        {"step": 1, "template": f"{TEMPLATE_ID} v{TEMPLATE_VERSION}", "text": f"Periksa revisi, signature dan template {TEMPLATE_ID} v{TEMPLATE_VERSION}, varian {variant}, ukuran luar nominal W={w_mm:g} dan H={h_mm:g} mm. Cocokkan setiap pola dengan bahan dan penempatan yang tercatat sebelum memotong. Cetak 100%/actual size; ukur kedua sumbu kotak 50 mm (selisih maksimal 0.5 mm) dan dimensi potong panel (maksimal 1 mm). Hentikan jika gagal. Konstruksi masih draft; review, cetak, uji kertas dan uji kain tetap pending sampai ada bukti nyata."},
        {"step": 3, "text": f"Pada {front} dan BACK, lipat atas 5 mm ke sisi buruk pada TOP_FOLD_1 y={q - 5:g}, lalu 5 mm lagi pada TOP_FOLD_2 y={q - 10:g} mm. Jahit HEM_STITCH y={q - 14:g} mm mengikuti panduan draft setelah kedua lipatan; periksa posisi tepi atas dan keselarasan. Hem bukan sambungan antarpanel; untuk split, JOIN_FRONT wajib selesai sebelum hem dan kedua half tidak dihem terpisah."},
        {"step": 4, "text": f"Hadapkan sisi muka {front} dan BACK; balik orientasi BACK terhadap sumbu vertikal untuk perakitan, bukan mirror saat potong. Pasangkan {'FRONT.LEFT_SEW' if variant == WHOLE else 'FRONT_L.OUTER_LEFT_SEW'} dengan BACK.RIGHT_SEW (S_LEFT), dan {'FRONT.RIGHT_SEW' if variant == WHOLE else 'FRONT_R.OUTER_RIGHT_SEW'} dengan BACK.LEFT_SEW (S_RIGHT), masing-masing y=10 sampai {q - 10:g} mm, panjang {h_mm:g} mm. Cocokkan tanda sudut dan midpoint BACK ({p / 2:g},10). Jahit S_LEFT, S_BOTTOM dan S_RIGHT pada garis 10 mm dari tepi; panjang struktural total {2 * h_mm + w_mm:g} mm. Ikuti ordered spans S_BOTTOM di tabel pairing; bukaan atas tetap terbuka dan tidak dijahit tertutup."},
        {"step": 5, "text": "Gunakan hanya finishing kampuh, perlakuan sudut dan arah pressing yang telah disetujui reviewer untuk varian serta bahan nyata. Keputusan tersebut masih pending; tidak ada kedalaman clipping, jarum, tegangan benang, pengaturan mesin atau suhu setrika yang dikarang. Uji kertas memakai lipatan/tape sebagai pengganti jahitan hanya untuk memeriksa topologi, bukan bukti perilaku kain."},
        {"step": 6, "text": f"Balik sisi muka keluar; periksa bukaan atas, sudut dan pertemuan jahitan. Letakkan datar tanpa meregangkan (stretching), ukur luar nominal W={w_mm:g}, H={h_mm:g} mm; target trial prototipe selisih maksimal 3 mm tiap dimensi, bukan kapasitas ruang dalam. Catat revisi, tanggal/operator, pengukuran cetak, alignment, perubahan dan hasil nyata. Trimming atau allowance yang berubah tanpa rencana harus dicatat sebagai koreksi/gagal; status fisik tetap pending sampai benar-benar diuji."},
    ]
    if variant == SPLIT_CENTER:
        lp = w_mm / 2.0 + 20.0
        common.insert(1, {"step": 2, "text": f"Hadapkan sisi muka FRONT_L dan FRONT_R; cocokkan notch bawah/atas JOIN_FRONT pada FRONT_L x={lp - 10:g} dan FRONT_R x=10, y=0 sampai {q:g} mm. Jahit garis JOIN_FRONT sepanjang {q:g} mm dengan kampuh 10 mm tiap sisi sebelum hem. Finishing dan arah pressing menunggu reviewer. Periksa blank depan gabungan {p:g} x {q:g} mm tanpa trimming tak tercatat. S_BOTTOM_LEFT memasangkan FRONT_L x=10 sampai {lp - 10:g} dengan BACK x={p - 10:g} sampai {p / 2:g}; S_BOTTOM_RIGHT memasangkan FRONT_R x=10 sampai {lp - 10:g} dengan BACK x={p / 2:g} sampai 10, semua y=10 mm. Tiap span {w_mm / 2:g} mm; pertemuan JOIN_FRONT harus tepat di midpoint BACK ({p / 2:g},10). Ini dua span dari satu S_BOTTOM, bukan dua seam tambahan; tambahan hanya satu JOIN_FRONT."})
    return common
