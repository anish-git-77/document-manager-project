"""
pdf_processor.py
-----------------
Core document-processing engine. Mirrors what a "physical document
services -> digital workflow" pipeline needs to do to a scanned/uploaded
PDF before it is searchable:

    1. OCR            -> turn pixels into searchable text
    2. Page editing    -> remove unwanted pages (blank/duplicate scans),
                          add new pages (e.g. an acknowledgement that
                          arrives later)
    3. Metadata        -> page count, per-page text, file hash for
                          dedup / audit trail

Design notes for the interview:
- pypdf handles page-level surgery (remove/add/merge) without needing to
  re-render anything -> fast, lossless for text-based PDFs.
- pytesseract + pdf2image handle OCR for scanned/image-only pages. We
  first try native text extraction (cheap, exact) and only fall back to
  OCR per-page if a page has no extractable text -> this is exactly how
  production scanning pipelines keep OCR cost down.
"""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass, field
from typing import List, Optional

from pypdf import PdfReader, PdfWriter

try:
    from pdf2image import convert_from_path
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:  # pragma: no cover - OCR deps are optional at import time
    OCR_AVAILABLE = False


@dataclass
class PageContent:
    page_number: int
    text: str
    source: str  # "native" (text layer) or "ocr" (image recognition)


@dataclass
class ProcessedDocument:
    doc_id: str
    filename: str
    page_count: int
    pages: List[PageContent] = field(default_factory=list)
    full_text: str = ""

    def as_index_payload(self) -> dict:
        """Shape the document the way we will hand it to Elasticsearch/Solr."""
        return {
            "doc_id": self.doc_id,
            "filename": self.filename,
            "page_count": self.page_count,
            "content": self.full_text,
            "pages": [
                {"page_number": p.page_number, "text": p.text, "source": p.source}
                for p in self.pages
            ],
        }


def _file_hash(path: str) -> str:
    """Content hash -> stable doc_id, and lets us detect duplicate uploads."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def remove_pages(input_path: str, output_path: str, pages_to_remove: List[int]) -> str:
    """
    pages_to_remove: 1-indexed page numbers, matching how a human would
    refer to "page 2 of the PDF".
    """
    reader = PdfReader(input_path)
    writer = PdfWriter()
    remove_set = {p - 1 for p in pages_to_remove}  # convert to 0-indexed

    for i, page in enumerate(reader.pages):
        if i not in remove_set:
            writer.add_page(page)

    with open(output_path, "wb") as f:
        writer.write(f)
    return output_path


def add_pages(base_path: str, pages_to_add_path: str, output_path: str,
              insert_at: Optional[int] = None) -> str:
    """
    Append (or insert) all pages from `pages_to_add_path` into `base_path`.
    insert_at: 1-indexed position to insert BEFORE. None = append at the end.
    """
    base_reader = PdfReader(base_path)
    add_reader = PdfReader(pages_to_add_path)
    writer = PdfWriter()

    base_pages = list(base_reader.pages)
    new_pages = list(add_reader.pages)

    if insert_at is None:
        ordered = base_pages + new_pages
    else:
        idx = insert_at - 1
        ordered = base_pages[:idx] + new_pages + base_pages[idx:]

    for page in ordered:
        writer.add_page(page)

    with open(output_path, "wb") as f:
        writer.write(f)
    return output_path


def extract_text_with_ocr_fallback(pdf_path: str, ocr_dpi: int = 200) -> ProcessedDocument:
    """
    Per page: try the PDF's native text layer first. If a page comes back
    empty (i.e. it's a scanned image with no text layer), rasterize just
    that page and run Tesseract OCR on it.
    """
    reader = PdfReader(pdf_path)
    pages: List[PageContent] = []
    images_cache = None  # lazily rendered only if a page actually needs OCR

    for i, page in enumerate(reader.pages):
        native_text = (page.extract_text() or "").strip()

        if native_text:
            pages.append(PageContent(page_number=i + 1, text=native_text, source="native"))
            continue

        # Needs OCR
        if not OCR_AVAILABLE:
            pages.append(PageContent(page_number=i + 1, text="", source="native"))
            continue

        if images_cache is None:
            images_cache = convert_from_path(pdf_path, dpi=ocr_dpi)

        ocr_text = pytesseract.image_to_string(images_cache[i]).strip()
        pages.append(PageContent(page_number=i + 1, text=ocr_text, source="ocr"))

    doc = ProcessedDocument(
        doc_id=_file_hash(pdf_path),
        filename=os.path.basename(pdf_path),
        page_count=len(pages),
        pages=pages,
        full_text="\n\n".join(p.text for p in pages if p.text),
    )
    return doc


if __name__ == "__main__":
    # Quick manual smoke test
    here = os.path.dirname(__file__)
    sample = os.path.join(here, "..", "sample_docs", "sample_invoice.pdf")
    doc = extract_text_with_ocr_fallback(sample)
    print(f"doc_id={doc.doc_id} pages={doc.page_count}")
    print(doc.full_text[:200])
