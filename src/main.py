"""
main.py
-------
Command-line entry point for the pipeline:

    scan/upload -> [remove pages] -> [add pages] -> OCR -> index -> search

Example:
    python main.py process --input ../sample_docs/sample_invoice.pdf \\
        --remove-pages 2 \\
        --add-pdf ../sample_docs/extra_page.pdf \\
        --backend local

    python main.py search --query "invoice" --backend local
"""

import argparse
import json
import os
import tempfile

from pdf_processor import extract_text_with_ocr_fallback, remove_pages, add_pages
from storage_backend import get_backend


def cmd_process(args):
    working_path = args.input

    # Step 1: remove pages, if requested
    if args.remove_pages:
        pages = [int(p) for p in args.remove_pages.split(",")]
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        remove_pages(working_path, tmp.name, pages)
        working_path = tmp.name
        print(f"Removed pages {pages}")

    # Step 2: add pages, if requested
    if args.add_pdf:
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        insert_at = args.insert_at
        add_pages(working_path, args.add_pdf, tmp.name, insert_at=insert_at)
        working_path = tmp.name
        print(f"Appended pages from {args.add_pdf}")

    # Step 3: OCR / text extraction
    doc = extract_text_with_ocr_fallback(working_path)
    print(f"Extracted {doc.page_count} pages (doc_id={doc.doc_id})")
    for p in doc.pages:
        print(f"  page {p.page_number} [{p.source}] -> {len(p.text)} chars")

    # Step 4: index into the chosen store
    backend = get_backend(args.backend, **_backend_kwargs(args))
    doc_id = backend.index_document(doc.as_index_payload())
    print(f"Indexed as doc_id={doc_id} in backend='{args.backend}'")

    if args.save_output:
        with open(args.save_output, "wb") as out, open(working_path, "rb") as src:
            out.write(src.read())
        print(f"Final PDF saved to {args.save_output}")


def cmd_search(args):
    backend = get_backend(args.backend, **_backend_kwargs(args))
    results = backend.search(args.query, size=args.size)
    print(json.dumps(results, indent=2)[:4000])


def _backend_kwargs(args):
    if args.backend == "local":
        return {"db_path": args.db_path}
    if args.backend in ("es", "elasticsearch"):
        return {"host": args.es_host}
    if args.backend == "solr":
        return {"url": args.solr_url}
    return {}


def build_parser():
    parser = argparse.ArgumentParser(description="Document scanning + indexing pipeline")
    parser.add_argument("--backend", default="local", choices=["local", "elasticsearch", "es", "solr"])
    parser.add_argument("--db-path", default=os.path.join(os.path.dirname(__file__), "..", "data", "local_index.db"))
    parser.add_argument("--es-host", default="http://localhost:9200")
    parser.add_argument("--solr-url", default="http://localhost:8983/solr/documents")

    sub = parser.add_subparsers(dest="command", required=True)

    p_process = sub.add_parser("process", help="Edit + OCR + index a PDF")
    p_process.add_argument("--input", required=True)
    p_process.add_argument("--remove-pages", help="Comma separated 1-indexed page numbers, e.g. 2,4")
    p_process.add_argument("--add-pdf", help="Path to a PDF whose pages get appended/inserted")
    p_process.add_argument("--insert-at", type=int, default=None, help="1-indexed position to insert before (default: append)")
    p_process.add_argument("--save-output", help="Where to save the final edited PDF")
    p_process.set_defaults(func=cmd_process)

    p_search = sub.add_parser("search", help="Full-text search the index")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--size", type=int, default=10)
    p_search.set_defaults(func=cmd_search)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
