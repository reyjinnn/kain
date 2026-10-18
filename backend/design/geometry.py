from __future__ import annotations

import math

from shapely.geometry import Polygon
from shapely.ops import unary_union

EPS = 1e-6
CLEARANCE = 2.0
CONFIG = {
    "version": "TOL-1",
    "resolution_mm": 0.01,
    "epsilon_mm": EPS,
    "seam_mm": 0.1,
    "edge_mm": CLEARANCE,
    "defect_mm": CLEARANCE,
    "gap_mm": CLEARANCE,
    "inter_piece_mm": CLEARANCE,
    "grid_mm": 1.0,
    "max_coordinate_mm": 10000.0,
    "max_ring_vertices": 64,
    "buffer_quadrant_segments": 16,
    "buffer_version": "EUCLIDEAN-BUFFER-1",
    "pdf_length_mm": 0.1,
    "printed_square_mm": 0.5,
    "printed_panel_mm": 1.0,
    "sewn_exterior_mm": 3.0,
    "photo_control_mm": 2.0,
    "max_checks_per_variant": 50000,
    "max_seconds_per_variant": 5.0,
    "max_part_orders": 6,
    "max_variants": 2,
    "normalization": "mm-0.01-CCW-lexicographic-origin-material-id-defect-ring-rotation-sort-v1",
}


class EngineError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details if details is not None else []


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def number(value, label="angka", rounded=False):
    if not finite(value):
        raise EngineError("INVALID_INPUT", f"{label} harus angka finite")
    return canonical_float(value) if rounded else value


def canonical_float(value):
    number(value)
    result = round(float(value), 2)
    return 0.0 if result == 0.0 else result


def fields(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise EngineError("INVALID_INPUT", f"{label}: field tidak dikenal atau field wajib hilang")


def polygon(ring):
    if not isinstance(ring, list) or not 3 <= len(ring) <= CONFIG["max_ring_vertices"] or any(
        not isinstance(p, (list, tuple)) or len(p) != 2 or any(not finite(c) for c in p) for p in ring
    ):
        raise EngineError("INVALID_INPUT", "Poligon memerlukan 3-64 titik dengan koordinat finite")
    coords = [tuple(p) for p in ring]
    if len(set(coords)) != len(coords):
        raise EngineError("INVALID_INPUT", "Poligon tidak boleh mengulang titik termasuk titik penutup")
    if any(abs(c) > CONFIG["max_coordinate_mm"] for p in coords for c in p):
        raise EngineError("INVALID_INPUT", "Koordinat poligon di luar batas aman")
    shape = Polygon(coords)
    if not shape.is_valid or shape.area <= EPS or not shape.exterior.is_simple:
        raise EngineError("INVALID_INPUT", "Poligon rusak, memotong diri, atau tanpa luas positif")
    return shape


def normalize_ring(ring):
    shape = polygon(ring)
    coords = [list(p) for p in ring]
    if not shape.exterior.is_ccw:
        coords.reverse()
    start = min(range(len(coords)), key=lambda i: tuple(coords[i]))
    return coords[start:] + coords[:start]


def rectangle(ring):
    if not isinstance(ring, list) or len(ring) != 4:
        raise EngineError("INVALID_INPUT", "Persegi panjang harus memiliki empat titik")
    shape = polygon(ring)
    x1, y1, x2, y2 = shape.bounds
    if not shape.equals(Polygon([(x1, y1), (x2, y1), (x2, y2), (x1, y2)])):
        raise EngineError("INVALID_INPUT", "Persegi panjang harus sejajar sumbu")
    return shape


def input_shape(ring):
    if not isinstance(ring, list) or len(ring) not in (4, 6):
        raise EngineError("INVALID_INPUT", "Hanya bahan persegi panjang atau L enam titik didukung")
    shape = polygon(ring)
    if len(ring) == 4:
        return rectangle(ring)
    x1, y1, x2, y2 = shape.bounds
    xs = sorted({p[0] for p in ring})
    ys = sorted({p[1] for p in ring})
    if len(xs) != 3 or len(ys) != 3:
        raise EngineError("INVALID_INPUT", "L harus memiliki tiga koordinat X dan Y berbeda")
    x_notch, y_notch = xs[1], ys[1]
    expected = Polygon([(x1, y1), (x2, y1), (x2, y_notch), (x_notch, y_notch), (x_notch, y2), (x1, y2)])
    if not shape.equals(expected):
        raise EngineError("INVALID_INPUT", "Cekungan L harus berupa persegi panjang di kanan atas")
    return shape


def polygon_ring(ring, label="polygon", shape=None, rounded=False):
    validator = rectangle if shape == "rectangle" else input_shape if shape == "material" else polygon
    validator(ring)
    coords = [[canonical_float(c) if rounded else c for c in p] for p in ring]
    poly = validator(coords)
    return normalize_ring(coords), poly


def placed_polygon(ring, rotation_deg, tx_mm, ty_mm):
    polygon(ring)
    for value in (rotation_deg, tx_mm, ty_mm):
        number(value, "placement")
    if any(abs(v) > CONFIG["max_coordinate_mm"] for v in (rotation_deg, tx_mm, ty_mm)):
        raise EngineError("INVALID_INPUT", "Penempatan di luar batas aman")
    if rotation_deg == 0:
        c, s = 1.0, 0.0
    elif rotation_deg == 90:
        c, s = 0.0, 1.0
    else:
        angle = math.radians(rotation_deg)
        c, s = math.cos(angle), math.sin(angle)
    return polygon([[x * c - y * s + tx_mm, x * s + y * c + ty_mm] for x, y in ring])


def fits(poly, outer, defects=(), others=()):
    return (
        outer.covers(poly)
        and poly.distance(outer.boundary) + CONFIG["epsilon_mm"] >= CONFIG["edge_mm"]
        and all(not poly.intersects(d) and poly.distance(d) + CONFIG["epsilon_mm"] >= CONFIG["defect_mm"] for d in defects)
        and all(not poly.intersects(p) and poly.distance(p) + CONFIG["epsilon_mm"] >= CONFIG["gap_mm"] for p in others)
    )


def raw_area(material):
    outer = polygon(material["outer_mm"])
    defects = [rectangle(d) for d in material["defects_mm"]]
    return outer.area - unary_union(defects).area
