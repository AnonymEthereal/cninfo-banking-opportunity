from pathlib import Path
from .pdf_parser import parse_pdf

def parse_document(path: Path, content_type: str = "application/pdf"):
    if content_type == "application/pdf" or path.suffix.lower() == ".pdf":
        return parse_pdf(path)
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("DOCUMENT_EMPTY")
    return text, [{"page": 1, "text": text}]
