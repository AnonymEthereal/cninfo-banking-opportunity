from pathlib import Path
from parsers.document import parse_document

def test_parse_text_document(tmp_path: Path):
    path = tmp_path / "a.txt"
    path.write_text("公告正文", encoding="utf-8")
    text, pages = parse_document(path, "text/plain")
    assert text == "公告正文"
    assert pages[0]["page"] == 1
