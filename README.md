# Document Scan-and-Index Pipeline

A small end-to-end demo of the core workflow behind "physical document services + digital
workflow" companies: a paper/scanned document comes in, gets edited (pages removed/added),
OCR'd into searchable text, and pushed into a search index (Elasticsearch or Solr) so it can
be retrieved by content later.

```
 input.pdf ──▶ remove pages ──▶ add pages ──▶ OCR (native text, fallback to Tesseract)
                                                        │
                                                        ▼
                                           structured JSON payload
                                          (doc_id, filename, pages[])
                                                        │
                                        ┌───────────────┼───────────────┐
                                        ▼               ▼               ▼
                                 Elasticsearch         Solr        Local SQLite
                                  (production)      (production)   (offline demo)
```

## Project layout

```
doc-management-system/
├── app.py                     # ⭐ Streamlit UI — the easiest way to run this
├── requirements.txt
├── sample_docs/
│   ├── make_sample_pdf.py     # generates a fake 3-page "invoice"
│   ├── sample_invoice.pdf
│   ├── extra_page.pdf
│   └── scanned_receipt.pdf    # image-only PDF, used to prove OCR works
├── src/
│   ├── pdf_processor.py       # remove/add pages, OCR-with-fallback
│   ├── storage_backend.py     # ES / Solr / local-SQLite, same interface
│   └── main.py                # CLI version — same pipeline, no UI
└── data/
    └── local_index.db         # created at runtime by the local backend
```

## How to run it (UI — recommended)

```bash
pip install -r requirements.txt
# system packages: tesseract-ocr, poppler-utils (for pdf2image)

cd sample_docs && python3 make_sample_pdf.py && cd ..
streamlit run app.py
```

This opens a browser tab where you can:
1. Upload a PDF (try `sample_docs/sample_invoice.pdf`)
2. Untick any pages you want removed — each one shows a preview of its first line of text
3. Optionally upload a second PDF (`sample_docs/extra_page.pdf`) to append or insert
4. Click **Process & Index** — it OCRs whatever needs OCR, shows you the extracted text
   per page (labelled "native text" vs "OCR" so you can see the fallback in action), and lets
   you download the edited PDF
5. Search the indexed content lower on the page

The sidebar lets you switch the storage backend between the local SQLite index (default, no
setup), a real Elasticsearch cluster, or a real Solr collection — same UI, just point it at a
different host.

## How to run it (CLI — same pipeline, scriptable)

```bash
cd src

# Remove page 2, append extra_page.pdf, OCR, and index — using the
# zero-infrastructure local backend
python3 main.py --backend local process \
  --input ../sample_docs/sample_invoice.pdf \
  --remove-pages 2 \
  --add-pdf ../sample_docs/extra_page.pdf \
  --save-output ../data/final_output.pdf

# Search what got indexed
python3 main.py --backend local search --query "invoice"
```

To point at real Elasticsearch or Solr instead (e.g. via Docker):

```bash
docker run -d -p 9200:9200 -e "discovery.type=single-node" \
  -e "xpack.security.enabled=false" elasticsearch:8.15.0
python3 main.py --backend elasticsearch process --input ../sample_docs/sample_invoice.pdf

docker run -d -p 8983:8983 solr:9.6 solr-precreate documents
python3 main.py --backend solr process --input ../sample_docs/sample_invoice.pdf
```

Nothing else in the code changes — `storage_backend.get_backend()` is a factory, and all
three backends implement the same `index_document / search / get / delete` interface.

---


- Add a `/search` highlight — Elasticsearch already returns `highlight` fragments in the query
  above; wiring that into the CLI output is a 10-minute add.
- Add pagination/`from` to `search()`.
- Add a REST wrapper (FastAPI/Flask) around `main.py`'s three functions so it's an actual
  service instead of a CLI — natural next step to mention even if you don't build it live.
