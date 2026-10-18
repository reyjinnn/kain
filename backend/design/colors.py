from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

PALETTE = json.loads((Path(__file__).resolve().parents[2] / "frontend" / "src" / "config" / "colors.json").read_text(encoding="utf-8"))


def normalize_name(value):
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def lab(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    xyz = [(r * .4124564 + g * .3575761 + b * .1804375) / .95047,
           r * .2126729 + g * .7151522 + b * .0721750,
           (r * .0193339 + g * .1191920 + b * .9503041) / 1.08883]
    x, y, z = [v ** (1 / 3) if v > (6 / 29) ** 3 else v / (3 * (6 / 29) ** 2) + 4 / 29 for v in xyz]
    return 116 * y - 16, 500 * (x - y), 200 * (y - z)


def delta_e(first, second):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(lab(first), lab(second))))


@lru_cache(maxsize=512)
def resolve(value):
    name = normalize_name(value)
    custom = re.fullmatch(r'(.{1,40})\s+\(\s*(#[0-9a-f]{6})\s*\)', name)
    if custom:
        label, hex_color = custom.groups()
        entry = next((p for p in PALETTE if label in p['aliases'] or label == p['key']), None)
        return {'family': entry['key'] if entry else 'unknown:' + label, 'name': label, 'hex': hex_color.upper()}
    is_hex = re.fullmatch(r"#[0-9a-f]{6}", name) is not None
    entry = next((p for p in PALETTE if name in p["aliases"] or name == p["key"]), None)
    if is_hex:
        entry = min(PALETTE, key=lambda p: delta_e(name, p["hex"]))
    if entry is None:
        return {"family": "unknown:" + name, "name": name or "tanpa preferensi", "hex": None}
    return {"family": entry["key"], "name": entry["name"], "hex": name.upper() if is_hex else entry["hex"]}


def same_family(first, second):
    return first is not None and second is not None and resolve(first)["family"] == resolve(second)["family"]


def stock_warning(preference, materials):
    if not preference:
        return None
    available = [m for m in materials if m["available"]]
    if any(same_family(preference, m["color"]) for m in available):
        return None
    desired = resolve(preference)
    known = [m for m in available if resolve(m["color"])["hex"]]
    nearest = min(known, key=lambda m: delta_e(desired["hex"], resolve(m["color"])["hex"])) if known and desired["hex"] else None
    actions = []
    if nearest:
        color = resolve(nearest["color"])
        actions.append({"id": "nearest", "label": f"Pakai warna terdekat ({color['name']})", "value": nearest["color"]})
    seen = {resolve(nearest['color'])['hex'] or resolve(nearest['color'])['family']} if nearest else set()
    for material in available:
        color = resolve(material['color'])
        identity = color['hex'] or color['family']
        if identity in seen:
            continue
        seen.add(identity)
        actions.append({'id': 'choose', 'label': f"Pakai kain {color['name']}", 'value': material['color']})
    actions.extend([{"id": "add", "label": f"Tambah kain {desired['name']}", "value": preference},
                    {"id": "ignore", "label": "Abaikan preferensi warna", "value": None}])
    return {"code": "COLOR_NOT_IN_STOCK", "field": "front_color",
            "text": f"Warna {desired['name']} bisa dipakai, tetapi kainnya belum ada di stokmu. Pilih kain yang tersedia, atau masukkan kainmu sendiri dengan warna itu. Pilihanmu tidak akan diganti otomatis.", "actions": actions}
