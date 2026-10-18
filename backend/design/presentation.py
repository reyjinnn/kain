from backend.design import colors

PART_NAMES = {"FRONT": "Panel depan", "FRONT_L": "Depan kiri", "FRONT_R": "Depan kanan", "BACK": "Panel belakang"}
CHECK_LABELS = ["Ukuran dan bentuk sesuai", "Semua potongan muat di dalam kain", "Potongan menjauhi noda atau lubang",
               "Antar potongan tidak bertabrakan", "Arah kain dan gabungan warna sesuai pilihan", "Semua bagian dan kain tersedia",
               "Garis jahit dan lipatan cocok", "Ukuran jadi dan pilihan sambungan sesuai", "Data rancangan masih sesuai", "Ukuran kain sudah kamu periksa"]


def fabric_name(material):
    return material.get("name") or "Kain " + colors.resolve(material["color"])["name"]


def summary(data, candidate):
    materials = {m["id"]: m for m in data["materials"]}
    faces = {p["instance_id"]: materials[p["material_id"]] for p in candidate["placements"]}
    front = [fabric_name(faces[p]) for p in ("FRONT", "FRONT_L", "FRONT_R") if p in faces]
    back = fabric_name(faces["BACK"])
    w, h = data["request"]["W_mm"] / 10, data["request"]["H_mm"] / 10
    return {"summary": f"Pouch bukaan atas {w:g} × {h:g} cm; depan dari {' dan '.join(front)}, belakang dari {back}.",
            "fabric_choice": f"{' dan '.join(front)} dipakai di depan karena potongannya muat. {back} cukup untuk panel belakang; susunan menjauhi noda dan tepi kain.",
            "what_to_expect": "Depan memakai dua potongan dengan satu jahitan tambahan di tengah." if candidate["extra_seams"] else "Depan dan belakang masing-masing memakai satu potongan utuh.",
            "watch_out": "Cocokkan arah serat dan tanda segitiga. Periksa skala cetak sebelum menggunakan pola.",
            "limits": "Ilustrasi belum mensimulasikan kain atau menjamin barangmu muat. Panduan versi uji coba; rakit dulu dari kertas."}


def color_notice(data, candidate):
    preference=data['request']['preferences']['front_color']
    materials={m['id']:m for m in data['materials']}
    front=[materials[p['material_id']] for p in candidate['placements'] if p['instance_id'].startswith('FRONT')]
    if preference and not all(colors.same_family(m['color'],preference) for m in front):
        actual=' + '.join(dict.fromkeys(colors.resolve(m['color'])['name'] for m in front))
        return f"Pilihan depan {colors.resolve(preference)['name']} belum terpenuhi sepenuhnya pada susunan ini. Depan memakai {actual}. Cek warna pada swatch, atau tambahkan kain warna pilihanmu."
    return None
