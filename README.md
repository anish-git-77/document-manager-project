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

## How to explain each part in the interview

### 1. "Walk me through the pipeline"
"A document arrives — either uploaded or from a physical scan. First we optionally **edit**
it: remove pages that shouldn't be archived (blank scans, duplicates) and insert pages that
arrive separately (like a signed acknowledgement). Then we **extract text**: for born-digital
PDFs we just read the embedded text layer, which is instant and exact. For scanned pages that
have no text layer, we rasterize just that page and run it through Tesseract OCR. Finally we
package the document — full text plus per-page text — into JSON and **index** it into
Elasticsearch or Solr so it's searchable by content, not just filename."

### 2. "Why check the native text layer before OCR'ing everything?"
OCR is slow and lossy compared to a PDF's real text layer. Running OCR on every page
regardless would waste compute and can introduce recognition errors on documents that were
already digitally clean. Checking `page.extract_text()` first and only OCR'ing pages that come
back empty mirrors how production ingestion pipelines control OCR cost — this is the
detail worth mentioning since it shows you understand *why*, not just *how*.

### 3. "How do you remove/add pages without corrupting the PDF?"
`pypdf`'s `PdfReader`/`PdfWriter` operate on the PDF's page objects directly — you build a new
`PdfWriter`, copy over the page objects you want to keep (in whatever order you want), and
write it out. Nothing is re-rendered, so a page that came from a scanner stays pixel-identical;
only the *page list* structure changes. Adding pages is the same idea in reverse: append or
splice pages from a second PDF's reader into the writer's ordered list.

### 4. "Why both Elasticsearch and Solr in the same project?"
They're both built on Apache Lucene and solve the same problem — inverted-index full-text
search — but differ in API shape and typical deployment. Elasticsearch's clients speak
JSON/REST with a document-oriented API; Solr traditionally speaks a params-based API (though
it also supports JSON). Building a thin `StorageBackend` interface with a `get_backend()`
factory means the ingestion code doesn't care which one is behind it — which is exactly the
kind of abstraction that matters if the company might migrate between them or run both.

### 5. "What would you change for production?"
- Store the original binary (S3/blob storage) and only index derived text + metadata, rather
  than keeping PDFs in the search engine itself.
- Make OCR async (a queue/worker) since Tesseract on a large scanned batch is slow — don't
  block the upload request on it.
- Add access control / multi-tenant field (`org_id`) to the index schema.
- Use the file hash (already computed here as `doc_id`) to deduplicate re-uploads.
- Add retry/backoff around the ES/Solr client calls, and a dead-letter queue for documents
  that fail OCR or indexing.
- Real Elasticsearch mapping would tune analyzers (e.g. language-specific stemming) instead of
  the default `text` type used here.

### 6. "Why is there both a Streamlit app and a CLI?"
`app.py` doesn't contain any pipeline logic — it only imports `pdf_processor` and
`storage_backend` and wires their outputs to widgets. That separation (business logic in
`src/`, UI as a thin consumer of it) is deliberate: it's the same reason you'd keep pipeline
code separate from a REST API layer in a real service, and it means the CLI and the UI can
never drift out of sync with each other since they call the exact same functions.

### 7. "How did you test it without a real Elasticsearch/Solr server?"
The `LocalSQLiteBackend` uses SQLite's FTS5 (full-text search) virtual table, which gives the
same "index text, search by relevance" behavior on a single file with zero setup — useful for
local development and for this demo, while the `ElasticsearchBackend`/`SolrBackend` classes are
the real integration you'd point at a running cluster.

---

## Talking points if they ask you to extend it live

- Add a `/search` highlight — Elasticsearch already returns `highlight` fragments in the query
  above; wiring that into the CLI output is a 10-minute add.
- Add pagination/`from` to `search()`.
- Add a REST wrapper (FastAPI/Flask) around `main.py`'s three functions so it's an actual
  service instead of a CLI — natural next step to mention even if you don't build it live.
