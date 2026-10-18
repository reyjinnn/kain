from backend.pdf import pdf_technical, pdf_guide
from backend.pdf.pdf_technical import PT, BUFFER_VERSION, _areas, _safe_text, _fonts


def generate_pdf(data, candidate, signature_val, mode="paper_test", kind="guide", illustration=None) -> bytes:
    if kind not in ("guide", "technical"):
        raise pdf_technical._error("INVALID_INPUT", "Tipo keluaran tidak dikenal")
    data, candidate, report = pdf_technical.validate_export(data, candidate, signature_val, mode)
    if kind == "technical":
        return pdf_technical.generate_pdf(data, candidate, signature_val, mode)
    return pdf_guide.generate_pdf(data, candidate, signature_val, mode, illustration=illustration)
