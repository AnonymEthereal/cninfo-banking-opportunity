from pathlib import Path
import pymupdf


def parse_pdf(path: Path) -> tuple[str, list[dict]]:
    pages = []
    with pymupdf.open(path) as document:
        for index, page in enumerate(document, 1):
            text = page.get_text("text").strip()
            if text:
                pages.append({"page": index, "text": text})
    text = "\n\n".join(item["text"] for item in pages)
    if not text:
        raise ValueError("PDF_PARSE_FAILED: 未提取到正文")
    return text, pages
