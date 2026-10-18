from __future__ import annotations

import io
import json
import math
import os
import unicodedata
from copy import deepcopy
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from shapely.geometry import Polygon
from shapely.ops import unary_union

from backend.design import template as t
from backend.design import engine

PT = 72 / 25.4
PAGE_W, PAGE_H = A4
MARGIN = 10 * PT
CONTENT_W = PAGE_W - 2 * MARGIN
BUFFER_VERSION = "EUCLIDEAN-BUFFER-1"
STYLES = {
    "cut": (colors.black, (), 0.8),
    "sew": (colors.HexColor("#1767AC"), (4, 2), 0.7),
    "fold": (colors.HexColor("#AD5700"), (6, 2, 1, 2), 0.7),
    "hem": (colors.HexColor("#863D89"), (1, 2), 0.7),
}


def _fonts():
    root = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    for name, filename in (("SISA-Jakarta", "Jakarta.ttf"), ("SISA-Jakarta-Bold", "Jakarta-Bold.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(root / filename)))
    return "SISA-Jakarta", "SISA-Jakarta-Bold"


def _safe_text(value, font):
    text = str(value)
    if font.startswith("SISA-"):
        glyphs = pdfmetrics.getFont(font).face.charToGlyph
        return "".join(c if c in "\n\t" or ord(c) in glyphs else "?" for c in text)
    replacements = {"—": "-", "–": "-", "×": "x", "²": "2", "≤": "<=", "≥": ">=", "±": "+/-", "↔": "<->", "→": "->", "°": " deg", "•": "-"}
    for old, new in replacements.items():
        text = text.replace(old, new)
    return unicodedata.normalize("NFKD", text).encode("ascii", "replace").decode("ascii")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _error(code, message, details=None):
    error = engine.EngineError(code, message)
    error.details = details or []
    return error


def _ring(pdf, points, stroke=1, fill=0):
    path = pdf.beginPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    path.close()
    pdf.drawPath(path, stroke=stroke, fill=fill)


def _transform(ring, placement):
    angle = math.radians(placement["rotation_deg"])
    c, s = math.cos(angle), math.sin(angle)
    return [(x * c - y * s + placement["tx_mm"], x * s + y * c + placement["ty_mm"]) for x, y in ring]


def _areas(data, candidate):
    config = engine.CONFIG
    segments = int(config.get("buffer_quadrant_segments", 16))
    selected = {pl["material_id"] for pl in candidate["placements"]}
    rows = []
    for material in data["materials"]:
        outer = Polygon(material["outer_mm"])
        defects = [Polygon(d) for d in material["defects_mm"]]
        forbidden = unary_union([d.buffer(config.get("defect_mm", 2), quad_segs=segments) for d in defects])
        usable = outer.buffer(-config.get("edge_mm", 2), quad_segs=segments, join_style=1).difference(forbidden).area
        raw = outer.area - unary_union(defects).area
        rows.append({"id": material["id"], "raw_mm2": raw, "usable_mm2": usable, "selected": material["id"] in selected, "available": material["available"]})
    cut = sum(Polygon(p["cut_mm"]).area for p in candidate["patterns"])
    usable = sum(row["usable_mm2"] for row in rows if row["selected"])
    return {"materials": rows, "cut_mm2": cut, "selected_usable_mm2": usable, "utilization_percent": 100 * cut / usable if usable else 0, "available_raw_mm2": sum(row["raw_mm2"] for row in rows if row["available"]), "inventory_raw_mm2": sum(row["raw_mm2"] for row in rows), "buffer_quadrant_segments": segments}


class _Document:
    def __init__(self, pdf, data, candidate, signature, mode):
        self.pdf = pdf
        self.data = data
        self.candidate = candidate
        self.signature = signature
        self.mode = mode
        self.font, self.bold = _fonts()
        self.page = 0
        self.title = ""
        self.materials = ""
        self.y = 270 * PT
        self.bottom = 22 * PT

    def line(self, text, x, y, size=8, bold=False, width=None, center=False):
        font = self.bold if bold else self.font
        text = _safe_text(text, font)
        if width is not None:
            measured = pdfmetrics.stringWidth(text, font, size)
            if measured > width:
                size *= width / measured
                if size < 5:
                    raise _error("PDF_CONTENT_TOO_WIDE", "Identitas terlalu panjang untuk margin A4; tidak dipotong")
        self.pdf.setFont(font, size)
        self.pdf.setFillColor(colors.black)
        if center:
            self.pdf.drawCentredString(x, y, text)
        else:
            self.pdf.drawString(x, y, text)

    def new_page(self, title, materials=None):
        if self.page:
            self.pdf.showPage()
        self.page += 1
        self.title = title
        self.materials = materials or ",".join(sorted({pl["material_id"] for pl in self.candidate["placements"]}))
        label = "UJI KERTAS — KONSTRUKSI BELUM DITINJAU" if self.mode == "paper_test" and self.candidate["construction_status"] == "draft" else "SISA — UJI KERTAS" if self.mode == "paper_test" else "SISA — UJI KAIN TERAWASI"
        self.line(label, MARGIN, 282.8 * PT, 10.5, True, CONTENT_W)
        self.line(f"{title} | Revisi {self.candidate['design_revision_id']} | {self.candidate['template_id']} v{self.candidate['template_version']}", MARGIN, 279.1 * PT, 6.2, width=CONTENT_W)
        self.line(f"Signature {self.signature[:16]} (lengkap di ringkasan) | Bahan {self.materials} | Mode {self.mode}", MARGIN, 276.1 * PT, 6.2, width=CONTENT_W)
        self.line(f"Cetak 100% / actual size, bukan fit-to-page. Ukur kotak dan panel; STOP jika gagal. Cek revisi terkini. Hal {self.page}", MARGIN, 12.5 * PT, 6.5, width=CONTENT_W)
        self.y = 270 * PT

    def style(self, bold=False, size=9):
        return ParagraphStyle("SISA", fontName=self.bold if bold else self.font, fontSize=size, leading=size * 1.35, splitLongWords=True, spaceAfter=0)

    def paragraph(self, text, bold=False, size=9):
        font = self.bold if bold else self.font
        pending = [Paragraph(escape(_safe_text(text, font)).replace("\n", "<br/>"), self.style(bold, size))]
        while pending:
            paragraph = pending.pop(0)
            _, height = paragraph.wrap(CONTENT_W, PAGE_H)
            available = self.y - self.bottom
            if height <= available:
                paragraph.drawOn(self.pdf, MARGIN, self.y - height)
                self.y -= height + 4
                continue
            fragments = paragraph.split(CONTENT_W, available)
            if fragments:
                first = fragments.pop(0)
                _, first_height = first.wrap(CONTENT_W, available)
                first.drawOn(self.pdf, MARGIN, self.y - first_height)
                pending = fragments + pending
            else:
                pending.insert(0, paragraph)
            self.new_page(self.title, self.materials)

    def heading(self, text):
        if self.y - self.bottom < 35:
            self.new_page(self.title, self.materials)
        self.paragraph(text, bold=True, size=10)

    def box_text(self, text, x, top, width, height, size=8):
        paragraph = Paragraph(escape(_safe_text(text, self.font)).replace("\n", "<br/>"), self.style(size=size))
        _, required = paragraph.wrap(width, height)
        if required > height:
            raise _error("PDF_CONTENT_TOO_TALL", "Teks halaman pola tidak muat; tidak dipotong")
        paragraph.drawOn(self.pdf, x, top - required)


def _summary(doc, report):
    candidate, data = doc.candidate, doc.data
    doc.new_page("RINGKASAN / BOM")
    doc.heading("Identitas dan spesifikasi")
    doc.paragraph(f"Design revision: {candidate['design_revision_id']}; parent_id: {candidate.get('parent_id') or '-'}; template: {candidate['template_id']} v{candidate['template_version']}; variant: {candidate['variant']}.")
    doc.paragraph(f"Input signature lengkap: {doc.signature}")
    w, h = data["request"]["W_mm"], data["request"]["H_mm"]
    doc.paragraph(f"W={w:g} mm, H={h:g} mm: ukuran luar nominal datar (flat_exterior), bukan ruang dalam atau jaminan barang muat. Pouch bukaan atas terbuka tanpa penutup, lining, flap, gusset atau volume terdefinisi. Ketebalan dan turn-of-cloth tidak dimodelkan. Kain tenun non-stretch kompatibel saja.")
    doc.paragraph(f"Konstruksi: {candidate['construction_status']}. Geometri: all-pass pada validasi ulang backend activation=True; bukan bukti cetak atau jahit. Print measurement: pending. Paper assembly: pending. Fabric trial: pending. STOP potong kain selama review/finishing atau pemeriksaan cetak belum lengkap.")
    doc.paragraph(f"Persetujuan perubahan: {_json(candidate.get('change_acceptance'))}. Tambahan seam: {candidate.get('extra_seams', 0)}; tambahan jalur mentah JOIN_FRONT: {candidate.get('extra_raw_sew_length_mm', 0):g} mm. Panjang struktural samping/bawah {2 * h + w:g} mm tidak termasuk hem, finishing atau join; bukan seluruh tenaga jahit.")
    doc.heading("Bill of materials: satu tiap instance")
    materials = {m["id"]: m for m in data["materials"]}
    placements = {pl["instance_id"]: pl for pl in candidate["placements"]}
    for part in candidate["patterns"]:
        pl = placements[part["id"]]
        mat = materials[pl["material_id"]]
        doc.paragraph(f"{part['id']} / {part['role']}: qty 1; potong {part['width_mm']:g} x {part['height_mm']:g} mm; bahan {mat['id']}; warna {mat['color']}; tipe {mat['material_type']}; rotasi {pl['rotation_deg']:g} deg; tx={pl['tx_mm']:g}, ty={pl['ty_mm']:g} mm; muka {mat['face']}; grain {part['grain']}.")
    doc.heading("Bahan dan pemakaian luas")
    metrics = _areas(data, candidate)
    for mat, row in zip(data["materials"], metrics["materials"]):
        doc.paragraph(f"{mat['id']}: warna {mat['color']}; available={mat['available']}; selected={row['selected']}; confirmed={mat['confirmed']}; sumber {mat['measurement_source']}; luas mentah sesudah union cacat {row['raw_mm2']:.2f} mm2; usable {row['usable_mm2']:.2f} mm2. Outer_mm={_json(mat['outer_mm'])}; defects_mm={_json(mat['defects_mm'])}; allowed_rotations_deg={_json(mat['allowed_rotations_deg'])}.", size=8)
    doc.paragraph(f"Luas mentah seluruh inventaris {metrics['inventory_raw_mm2']:.2f} mm2; inventaris tersedia {metrics['available_raw_mm2']:.2f} mm2; cut footprint {metrics['cut_mm2']:.2f} mm2 / usable bahan terpilih {metrics['selected_usable_mm2']:.2f} mm2 = {metrics['utilization_percent']:.2f}%. Penyebut hanya bahan terpilih, bukan bounding box bentuk L. Margin tepi diterapkan sekali; union cacat yang dibuffer dikurangkan sekali. Kebijakan jarak Euclidean {BUFFER_VERSION}, round buffer quad_segs={metrics['buffer_quadrant_segments']}; pendekatan busur polygon, bukan akurasi pengukuran. Tidak menghitung pemakaian atau emisi karbon.", size=8)
    doc.new_page("LAPORAN VALIDASI")
    doc.heading("Validasi ulang backend: VAL-01 sampai VAL-10")
    doc.paragraph(f"Report signature lengkap: {report['input_signature']}; validator version: {report.get('validator_version', 'tidak direkam')}; config version: {report.get('config_version', engine.CONFIG['version'])}; passed={report['passed']}.")
    for check in report["checks"]:
        doc.paragraph(f"[{check['status'].upper()}] {check['id']}: {check.get('message', '')}", size=9)
        extra = {k: v for k, v in check.items() if k not in ("id", "status", "message")}
        if extra:
            doc.paragraph(_json(extra), size=8)
    doc.paragraph("Failure details lengkap: " + _json(report.get("failures", [])), size=8)
    doc.heading("Konfigurasi, toleransi dan batas pencarian")
    doc.paragraph("Konfigurasi backend lengkap: " + _json(engine.CONFIG), size=8)
    doc.paragraph("TOL-1: resolusi simpan 0.01 mm (bukan akurasi); epsilon 0.000001 mm; pasangan seam/mark <=0.1 mm tanpa ease; clearance tepi/cacat/antarpola >=2 mm; error PDF digital <=0.1 mm; kotak cetak 50 mm <=0.5 mm tiap sumbu; panel cetak <=1 mm tiap dimensi; nominal sampel kain <=3 mm tiap W/H. Toleransi prototipe, bukan bukti kemampuan produksi.")
    doc.paragraph("Kontrak pencarian: whole lalu center-split jika diizinkan; maksimal dua varian, enam urutan komponen, grid 1 mm, 50,000 placement checks dan 5 detik per varian; orientasi irisan allowlist template/bahan 0/90 deg; tanpa mirroring. Berhenti pada kandidat feasible pertama, bukan optimum global. Exhaustion bukan bukti mustahil; wall-time dapat mengubah hasil antar mesin.")
    doc.paragraph("Search metadata aktual lengkap: " + _json(candidate.get("search", {"status": "tidak direkam"})), size=8)
    doc.paragraph("Review konstruksi: draft bila belum ada review; print, paper dan fabric tetap pending sampai pengukuran/perakitan nyata dicatat. Rendering PDF dan kelulusan digital tidak mengisi catatan bukti fisik. Berkas unduhan lama tidak membuktikan snapshot masih current.")


def _pattern(doc, part, placement, material):
    pdf = doc.pdf
    doc.new_page(f"POLA SKALA 1:1 / {part['id']}", material["id"])
    ox, oy = MARGIN, 75 * PT
    w, h = part["width_mm"], part["height_mm"]
    pdf.saveState()
    color, dash, thickness = STYLES["cut"]
    pdf.setStrokeColor(color)
    pdf.setLineWidth(thickness)
    pdf.setDash(dash)
    if len(part["cut_mm"]) == 4 and all(x in (0, w) and y in (0, h) for x, y in part["cut_mm"]):
        pdf.rect(ox, oy, w * PT, h * PT)
    else:
        _ring(pdf, [(ox + x * PT, oy + y * PT) for x, y in part["cut_mm"]])
    for kind, key in (("sew", "sew_paths"), ("fold", "fold_paths"), ("hem", "hem_paths")):
        color, dash, thickness = STYLES[kind]
        pdf.setStrokeColor(color)
        pdf.setDash(dash)
        pdf.setLineWidth(thickness)
        for path in part[key]:
            points = path["points_mm"]
            vector = pdf.beginPath()
            vector.moveTo(ox + points[0][0] * PT, oy + points[0][1] * PT)
            for x, y in points[1:]:
                vector.lineTo(ox + x * PT, oy + y * PT)
            pdf.drawPath(vector)
    pdf.setDash()
    pdf.setStrokeColor(colors.black)
    pdf.setLineWidth(0.7)
    for x, y in part["notches_mm"]:
        direction = 1 if y == 0 else -1
        pdf.line(ox + (x - 1.4) * PT, oy + y * PT, ox + x * PT, oy + (y + direction * 2.5) * PT)
        pdf.line(ox + x * PT, oy + (y + direction * 2.5) * PT, ox + (x + 1.4) * PT, oy + y * PT)
    doc.line(part["id"], ox + w * PT / 2, oy + h * PT * 0.75, 11, True, center=True)
    doc.line(f"Lebar potong {w:g} mm", ox + w * PT / 2, oy + h * PT * 0.60, 8, center=True)
    pdf.saveState()
    pdf.translate(ox + 17 * PT, oy + h * PT / 2)
    pdf.rotate(90)
    doc.line(f"Tinggi potong {h:g} mm", 0, 0, 8, center=True)
    pdf.restoreState()
    cx, cy = ox + w * PT / 2, oy + h * PT * 0.35
    dx, dy = (15 * PT, 0) if w >= h else (0, 15 * PT)
    pdf.setStrokeColor(colors.HexColor("#28603F"))
    pdf.setLineWidth(0.9)
    pdf.line(cx - dx, cy - dy, cx + dx, cy + dy)
    for sign in (-1, 1):
        ex, ey = cx + sign * dx, cy + sign * dy
        ux, uy = sign * dx / (15 * PT), sign * dy / (15 * PT)
        for side in (-1, 1):
            pdf.line(ex, ey, ex - 3 * PT * ux + side * 1.5 * PT * uy, ey - 3 * PT * uy - side * 1.5 * PT * ux)
    doc.line("ARAH SERAT", cx, cy - abs(dy) - 4 * PT, 7, center=True)
    pdf.setStrokeColor(colors.black)
    pdf.setLineWidth(0.8)
    pdf.rect(MARGIN, 20 * PT, 50 * PT, 50 * PT)
    doc.box_text("KOTAK KALIBRASI\n50 x 50 mm\nUkur kedua sumbu fisik.\nTOL-1: +/-0.5 mm.\nPanel: +/-1 mm.\nSTOP jika gagal.", MARGIN + 3 * PT, 62 * PT, 44 * PT, 39 * PT, 8)
    doc.box_text(f"{part['id']} / {part['role']} | qty 1 | {w:g} x {h:g} mm\nBahan {material['id']} ({material['color']}); rotasi layout {placement['rotation_deg']:g} deg. Potong muka menghadap atas; tidak mirror.", 65 * PT, 68 * PT, 135 * PT, 16 * PT, 8)
    for index, (kind, text) in enumerate((("cut", "POTONG: solid"), ("sew", "JAHIT STRUKTURAL: dashed"), ("fold", "LIPAT: dash-dot"), ("hem", "HEM STITCH: dotted, guide draft"))):
        y = (46 - index * 5) * PT
        color, dash, thickness = STYLES[kind]
        pdf.setStrokeColor(color)
        pdf.setDash(dash)
        pdf.setLineWidth(thickness)
        pdf.line(65 * PT, y, 79 * PT, y)
        doc.line(text, 82 * PT, y - 2, 7.5)
    pdf.setDash()
    doc.line("V = notch/tanda; panah dua arah = grain (along_long_edge).", 65 * PT, 24 * PT, 7, width=135 * PT)
    pdf.restoreState()


def _instructions(doc):
    candidate = doc.candidate
    w, h = doc.data["request"]["W_mm"], doc.data["request"]["H_mm"]
    p, q = w + 20, h + 20
    doc.new_page("INSTRUKSI KONSTRUKSI")
    doc.heading("STOP: review, finishing dan cetak")
    doc.paragraph("Template ini rancangan konstruksi, bukan panduan jahit yang telah ditinjau. Reviewer finishing/corner treatment: pending. Jangan potong/jahit kain sampai varian dan bahan ditinjau serta skala cetak diperiksa. Uji kertas memakai lipatan/tape sebagai pengganti jahitan untuk memeriksa topologi; tidak membuktikan perilaku kain, kekuatan atau kapasitas.")
    doc.paragraph("Cetak 100% / actual size, nonaktifkan fit-to-page. Ukur kedua sumbu kotak 50 x 50 mm (masing-masing error <=0.5 mm) dan W/H potong panel (masing-masing <=1 mm). STOP jika gagal; jangan diam-diam mengubah skala. Cek identitas revisi/signature terhadap sesi terkini dan setiap pola terhadap sisa kain nyata.")
    doc.heading("Seluruh langkah dari template")
    for step in candidate["assembly_steps"]:
        doc.paragraph(f"Langkah {step['id']}: {step['text']}")
    doc.heading("Pairing, ordered spans dan checkpoint")
    doc.paragraph("Koordinat lokal mm: origin kiri bawah, +x kanan, +y atas; gambar potong dilihat dari sisi muka. FRONT muka ke atas; putar BACK terhadap sumbu vertikal saat perakitan agar sisi muka saling berhadapan. Ini operasi jahit, bukan izin mirror layout potong.")
    split = candidate["variant"] == "front_split_center"
    doc.paragraph(f"S_LEFT: {'FRONT_L.OUTER_LEFT_SEW' if split else 'FRONT.LEFT_SEW'} <-> BACK.RIGHT_SEW; S_RIGHT: {'FRONT_R.OUTER_RIGHT_SEW' if split else 'FRONT.RIGHT_SEW'} <-> BACK.LEFT_SEW. Tiap span y=10 sampai {q - 10:g}; panjang {h:g} mm. Cocokkan arah dan tanda, tanpa ease; selisih <=0.1 mm digital.")
    if split:
        l = w / 2 + 20
        doc.paragraph(f"JOIN_FRONT: FRONT_L x={l - 10:g}, y=0 sampai {q:g} <-> FRONT_R x=10, y=0 sampai {q:g}; raw length {q:g} mm. Match notch bawah/atas dan sisi muka; join sebelum hem, tidak hem kedua half terpisah. Raw front sesudah join {p:g} x {q:g} mm; lebar jadi {w:g} mm. Split tampak {h:g} mm, berbeda dari raw path {q:g} mm. Finishing dan arah pressing wajib keputusan reviewer, masih pending.")
        doc.paragraph(f"S_BOTTOM satu sambungan struktural, dua ordered spans: FRONT_L.BOTTOM_LEFT x=10 sampai {l - 10:g} <-> BACK.BOTTOM_SEW x={p - 10:g} sampai {p / 2:g}; FRONT_R.BOTTOM_RIGHT x=10 sampai {l - 10:g} <-> BACK.BOTTOM_SEW x={p / 2:g} sampai 10; seluruh y=10, tiap span {w / 2:g} mm. Match midpoint BACK ({p / 2:g},10) dengan pertemuan JOIN_FRONT/bawah; bukan dua tambahan seam.")
    else:
        doc.paragraph(f"S_BOTTOM: FRONT.BOTTOM_SEW x=10 sampai {p - 10:g} <-> BACK.BOTTOM_SEW reversed x={p - 10:g} sampai 10, y=10; panjang {w:g} mm. Match midpoint dan sudut seam.")
    doc.paragraph(f"TOP_FOLD_1 y={q - 5:g}, TOP_FOLD_2/finished top y={q - 10:g}; lipat 5 mm ke sisi buruk lalu 5 mm lagi. HEM_STITCH y={q - 14:g} adalah guide draft, terpisah dari paired structural seam. Periksa posisi top dan alignment; hem bukan sambungan antarpanel. Jahit S_LEFT, S_BOTTOM, S_RIGHT; bukaan atas tetap terbuka, jangan dijahit tertutup.")
    doc.paragraph("Seam connections snapshot lengkap: " + _json(candidate["seam_connections"]), size=8)
    doc.paragraph(f"Tidak mengarang kedalaman clipping, jarum, setting mesin, tegangan benang atau suhu setrika. Finishing/corner dan handling ketebalan tetap pending reviewer. Balik, inspeksi opening/intersections, letakkan datar tanpa stretching dan ukur nominal exterior W={w:g}, H={h:g} mm. Catat tanggal/operator/revisi, skala cetak, pairing, koreksi dan hasil nyata; perubahan allowance/trimming tak tercatat adalah correction/failure, bukan pass. Fabric trial: pending; print: pending; paper assembly: pending.")


def _layout(doc, material, placements, parts):
    pdf = doc.pdf
    doc.new_page(f"LAYOUT / {material['id']}", material["id"])
    doc.heading("Diagram penempatan, bukan pola 1:1")
    doc.paragraph(f"Bahan {material['id']}, warna {material['color']}; bidang asli termasuk cekungan L dan cacat. Transform mm: rotasi CCW terhadap origin pola, kemudian translasi; tanpa scaling/mirror pada penempatan.", size=8)
    for pl in placements:
        doc.paragraph(f"{pl['instance_id']}: material={material['id']}; rotation={pl['rotation_deg']:g} deg; tx={pl['tx_mm']:g}, ty={pl['ty_mm']:g} mm.", size=8)
    outer = Polygon(material["outer_mm"])
    x0, y0, x1, y1 = outer.bounds
    available = doc.y - 45 * PT
    if available < 50 * PT:
        doc.new_page(f"LAYOUT / {material['id']}", material["id"])
        available = doc.y - 45 * PT
    scale = min(CONTENT_W / (x1 - x0), available / (y1 - y0))
    ox, oy = MARGIN, 40 * PT
    def points(ring):
        return [(ox + (x - x0) * scale, oy + (y - y0) * scale) for x, y in ring]
    pdf.saveState()
    pdf.setDash()
    pdf.setLineWidth(0.8)
    pdf.setStrokeColor(colors.HexColor("#444444"))
    try:
        base = colors.toColor(material["color"])
    except ValueError:
        base = colors.HexColor("#AAAAAA")
    pdf.setFillColor(colors.Color(0.85 + base.red * 0.15, 0.85 + base.green * 0.15, 0.85 + base.blue * 0.15))
    _ring(pdf, points(material["outer_mm"]), fill=1)
    pdf.setStrokeColor(colors.HexColor("#A32121"))
    pdf.setFillColor(colors.HexColor("#F4BDBD"))
    for defect in material["defects_mm"]:
        _ring(pdf, points(defect), fill=1)
    for pl in placements:
        ring = _transform(parts[pl["instance_id"]]["cut_mm"], pl)
        pdf.setStrokeColor(colors.HexColor("#1767AC"))
        pdf.setLineWidth(1)
        _ring(pdf, points(ring))
        centroid = Polygon(ring).centroid
        cx, cy = points([(centroid.x, centroid.y)])[0]
        doc.line(pl["instance_id"], cx, cy, 8, True, center=True)
    pdf.restoreState()
    doc.line(f"Diagram scale {scale / PT:.4f} pt/mm ratio to 1:1; gris = batas bahan; merah = cacat; biru = cut footprint.", MARGIN, 31 * PT, 7, width=CONTENT_W)
    doc.line("Origin bahan kiri bawah; +x kanan, +y atas; rotation positif CCW. Geometri dan posisi ditampilkan dalam mm.", MARGIN, 26 * PT, 7, width=CONTENT_W)


def validate_export(data, candidate, signature_val, mode="paper_test"):
    if mode not in ("paper_test", "fabric_trial"):
        raise _error("INVALID_INPUT", "Mode harus paper_test atau fabric_trial")
    data, candidate = deepcopy(data), deepcopy(candidate)
    if not isinstance(candidate, dict):
        raise _error("INVALID_STATE", "Kandidat harus snapshot tervalidasi")
    consent = candidate.get("change_acceptance")
    report = engine.validate_candidate(data, candidate, signature_val, consent=consent, activation=True)
    current_signature = engine.signature(data, consent)
    if candidate.get("input_signature") != signature_val or signature_val != current_signature:
        raise _error("STALE_REVISION", "Signature snapshot bukan signature backend terkini", report.get("failures"))
    checks = report.get("checks", [])
    if not report.get("passed") or report.get("input_signature") != signature_val or len(checks) != 10 or {check.get("id") for check in checks} != {f"VAL-{i:02d}" for i in range(1, 11)} or any(check.get("status") != "pass" for check in checks) or report.get("failures"):
        failures = report.get("failures") or [check for check in checks if check.get("status") != "pass"]
        raise _error("VALIDATION_FAILED", "Ekspor ditolak; failure details lengkap: " + _json(failures), failures)
    status = candidate.get("construction_status")
    template_status = getattr(t, "REVIEW_STATUS", "draft")
    if status not in ("draft", "reviewed", "fabric_trial_passed") or status != template_status:
        raise _error("CONSTRUCTION_UNREVIEWED", "Status konstruksi bukan status template/varian terdaftar")
    if mode == "fabric_trial" and (template_status not in ("reviewed", "fabric_trial_passed") or status not in ("reviewed", "fabric_trial_passed")):
        raise _error("CONSTRUCTION_UNREVIEWED", "STOP: template/varian draft; fabric_trial memerlukan review konstruksi")
    rev_id = candidate.get("design_revision_id")
    if not isinstance(rev_id, str) or not rev_id.strip() or rev_id == signature_val:
        raise _error("INVALID_STATE", "design_revision_id unik diperlukan, bukan hanya signature")
    if candidate["variant"] == "front_split_center" and (not isinstance(consent, dict) or consent.get("accepted") is not True or type(consent.get("additional_seams")) is not int or consent["additional_seams"] != 1 or consent.get("variant", candidate["variant"]) != candidate["variant"]):
        raise _error("VALIDATION_FAILED", "Persetujuan satu sambungan tambahan untuk varian split diperlukan")
    if not isinstance(candidate.get("assembly_steps"), list) or not candidate["assembly_steps"]:
        raise _error("INVALID_STATE", "Langkah konstruksi template lengkap diperlukan")
    for step in candidate["assembly_steps"]:
        if not isinstance(step, dict) or "id" not in step or not isinstance(step.get("text"), str) or not step["text"].strip():
            raise _error("INVALID_STATE", "Langkah konstruksi tidak lengkap")
    for part in candidate["patterns"]:
        bounds = Polygon(part["cut_mm"]).bounds
        if not 0 < part["width_mm"] <= 180 or not 0 < part["height_mm"] <= 200 or bounds[0] < 0 or bounds[1] < 0 or bounds[2] > 180 or bounds[3] > 200:
            raise _error("PDF_PATTERN_TOO_LARGE", "Pola melebihi 180 x 200 mm; tidak ada scaling/tiling")
    return data, candidate, report


def generate_pdf(data, candidate, signature_val, mode="paper_test") -> bytes:
    data, candidate, report = validate_export(data, candidate, signature_val, mode)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
    pdf.setAuthor("SISA")
    pdf.setTitle(f"SISA {candidate['design_revision_id']} {mode}")
    doc = _Document(pdf, data, candidate, signature_val, mode)
    _summary(doc, report)
    package = candidate.get("ai_package")
    if package and package.get("source") == "live":
        doc.new_page("PAKET DESAIN AI / 9ROUTER")
        doc.paragraph("AI mengusulkan dimensi potong dan susunan, memilih urutan konstruksi terdaftar, dan menjelaskan paket ini. Pola 1:1 dirender sebagai vektor dari ukuran yang diperiksa backend. Teks AI bukan hasil uji fisik atau pengganti laporan validator.")
        for field, title in (("summary", "Desain pouch"), ("fabric_choice", "Pilihan kain"), ("what_to_expect", "Hasil rancangan"), ("watch_out", "Perlu diperiksa"), ("limits", "Batas penggunaan")):
            doc.heading(title)
            doc.paragraph(package[field])
        doc.paragraph(f"Sumber: AI live melalui 9Router; model {package['model']}. Signature paket: {package['input_signature']}", size=8)
    materials = {m["id"]: m for m in data["materials"]}
    placements = {pl["instance_id"]: pl for pl in candidate["placements"]}
    parts = {part["id"]: part for part in candidate["patterns"]}
    for part in candidate["patterns"]:
        pl = placements[part["id"]]
        _pattern(doc, part, pl, materials[pl["material_id"]])
    _instructions(doc)
    for material_id in sorted({pl["material_id"] for pl in candidate["placements"]}):
        _layout(doc, materials[material_id], [pl for pl in candidate["placements"] if pl["material_id"] == material_id], parts)
    pdf.save()
    return buffer.getvalue()
