
import os
import sys
import tempfile

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from pdf_processor import extract_text_with_ocr_fallback, remove_pages, add_pages
from storage_backend import get_backend
from pypdf import PdfReader

st.set_page_config(page_title="Document Scan & Index", page_icon="📄", layout="wide")

# ---------------------------------------------------------------------------
# Sidebar: pick where indexed documents get stored
# ---------------------------------------------------------------------------
st.sidebar.title("⚙️ Storage backend")
backend_choice = st.sidebar.radio(
    "Where should processed documents be indexed?",
    ["Local (no setup needed)", "Elasticsearch", "Solr"],
)

backend_kwargs = {}
if backend_choice == "Local (no setup needed)":
    backend_key = "local"
    backend_kwargs = {"db_path": os.path.join(os.path.dirname(__file__), "data", "local_index.db")}
    st.sidebar.caption("Uses a local SQLite full-text index. Nothing to install.")
elif backend_choice == "Elasticsearch":
    backend_key = "elasticsearch"
    host = st.sidebar.text_input("Elasticsearch host", "http://localhost:9200")
    backend_kwargs = {"host": host}
else:
    backend_key = "solr"
    url = st.sidebar.text_input("Solr collection URL", "http://localhost:8983/solr/documents")
    backend_kwargs = {"url": url}

st.sidebar.divider()
st.sidebar.caption(
    "This UI does 3 things: edit a PDF's pages, run OCR on it, "
    "and index the resulting text so it becomes searchable."
)


# Main: Upload + edit + process

st.title("📄 Document Scan & Index")
st.write(
    "Upload a PDF, remove pages you don't want archived, optionally attach another "
    "PDF's pages, then process it — the tool OCRs whatever needs OCR and makes the "
    "content searchable."
)

col1, col2 = st.columns(2)
with col1:
    main_file = st.file_uploader("1. Upload the main PDF", type=["pdf"])
with col2:
    extra_file = st.file_uploader("2. (Optional) PDF with pages to add", type=["pdf"])

pages_to_keep = None
insert_at = None

if main_file:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(main_file.getvalue())
        main_path = tmp.name

    reader = PdfReader(main_path)
    n_pages = len(reader.pages)
    st.subheader(f"3. Choose which of the {n_pages} pages to keep")

    cols = st.columns(min(n_pages, 6))
    keep_flags = []
    for i in range(n_pages):
        with cols[i % len(cols)]:
            preview = (reader.pages[i].extract_text() or "").strip().split("\n")
            label = preview[0][:28] if preview and preview[0] else f"Page {i + 1}"
            keep = st.checkbox(f"Page {i + 1}: {label}", value=True, key=f"keep_{i}")
            keep_flags.append(keep)
    pages_to_keep = [i + 1 for i, k in enumerate(keep_flags) if k]

    if extra_file:
        insert_choice = st.radio(
            "Where should the extra PDF's pages go?",
            ["Append at the end", "Insert at the start"],
            horizontal=True,
        )
        insert_at = 1 if insert_choice == "Insert at the start" else None

    if st.button("🚀 Process & Index", type="primary"):
        with st.spinner("Editing pages, running OCR, and indexing..."):
            pages_to_remove = [i + 1 for i, k in enumerate(keep_flags) if not k]
            working_path = main_path

            if pages_to_remove:
                tmp_out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                remove_pages(working_path, tmp_out.name, pages_to_remove)
                working_path = tmp_out.name

            if extra_file:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_extra:
                    tmp_extra.write(extra_file.getvalue())
                    extra_path = tmp_extra.name
                tmp_out2 = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                add_pages(working_path, extra_path, tmp_out2.name, insert_at=insert_at)
                working_path = tmp_out2.name

            doc = extract_text_with_ocr_fallback(working_path)
            backend = get_backend(backend_key, **backend_kwargs)
            doc_id = backend.index_document(doc.as_index_payload())

            with open(working_path, "rb") as f:
                final_bytes = f.read()

        st.success(f"Indexed as `{doc_id}` — {doc.page_count} pages in the final PDF.")

        st.subheader("Extracted text per page")
        for p in doc.pages:
            badge = "🔎 OCR" if p.source == "ocr" else "📝 native text"
            with st.expander(f"Page {p.page_number} — {badge} — {len(p.text)} chars"):
                st.text(p.text or "(no text found)")

        st.download_button(
            "⬇️ Download final PDF",
            data=final_bytes,
            file_name="processed_document.pdf",
            mime="application/pdf",
        )

st.divider()

# Search

st.header("🔍 Search indexed documents")
query = st.text_input("Search by content", placeholder="e.g. invoice, receipt, acknowledgement")
if st.button("Search") and query:
    backend = get_backend(backend_key, **backend_kwargs)
    results = backend.search(query, size=10)
    if not results:
        st.info("No matching documents found.")
    for r in results:
        with st.container(border=True):
            st.markdown(f"**{r['filename']}** — {r['page_count']} pages — `{r['doc_id']}`")
            snippet = r["content"][:300] + ("..." if len(r["content"]) > 300 else "")
            st.caption(snippet)
