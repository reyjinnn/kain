import re
from backend.design import step_catalog
from backend.design import presentation


def intent(text):
    normalized = " ".join(text.lower().strip().split()).rstrip(".!?")
    if normalized in ("buat ulang gambar", "ulangi gambar", "gambar ulang", "buat gambar"):
        return {"action": "regenerate_image", "regenerate": normalized != "buat gambar"}
    if normalized in ("kenapa disusun begini", "kenapa disusun begitu", "jelaskan susunan", "jelaskan rancangan", "apa yang akan dibuat"):
        return {"action": "explain", "topic": "layout"}
    match = re.fullmatch(r"(?:jelaskan|jelasin|terangkan)(?: tentang)? langkah(?: ke)?\s*(\d{1,2})", normalized)
    if match:
        return {"action": "explain", "topic": "step", "step": int(match[1])}
    return None


def explanation(data, candidate, action):
    if action["topic"] == "layout":
        return presentation.summary(data, candidate)["fabric_choice"]
    steps = step_catalog.steps(data, candidate)
    # Use displayed numbers, which omit the split step for a whole front.
    index = action["step"] - 1
    if not 0 <= index < len(steps):
        return "Langkah itu belum ada di petunjuk rancangan ini. Pilih nomor langkah yang terlihat di petunjuk kamu."
    step = steps[index]
    return f'{step["title"]}. {step["text"]} Cek: {step["check"]}'
