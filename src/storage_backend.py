"""
storage_backend.py
-------------------
A small "index store" abstraction so main.py can point at Elasticsearch,
Solr, or (for a demo with no infrastructure running) a local SQLite
full-text index, without changing any calling code. This is the same
pattern you'd use in a real system to keep the search backend swappable.

Every backend implements:
    index_document(payload: dict) -> str        # returns doc_id
    search(query: str, size: int = 10) -> list[dict]
    get(doc_id: str) -> dict | None
    delete(doc_id: str) -> bool
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from typing import List, Optional


class StorageBackend(ABC):
    @abstractmethod
    def index_document(self, payload: dict) -> str: ...

    @abstractmethod
    def search(self, query: str, size: int = 10) -> List[dict]: ...

    @abstractmethod
    def get(self, doc_id: str) -> Optional[dict]: ...

    @abstractmethod
    def delete(self, doc_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# 1. Elasticsearch backend (real production target)
# ---------------------------------------------------------------------------
class ElasticsearchBackend(StorageBackend):
    """
    Requires a running Elasticsearch instance, e.g. via Docker:

        docker run -d --name es -p 9200:9200 -e "discovery.type=single-node" \\
            -e "xpack.security.enabled=false" elasticsearch:8.15.0

    Index mapping is intentionally simple: `content` is analyzed full text,
    `filename`/`doc_id` are keywords for exact lookups, `pages` is a nested
    field so we can later highlight/search within a specific page.
    """

    def __init__(self, host: str = "http://localhost:9200", index_name: str = "documents"):
        from elasticsearch import Elasticsearch  # imported lazily; optional dep

        self.es = Elasticsearch(host)
        self.index_name = index_name
        self._ensure_index()

    def _ensure_index(self):
        if not self.es.indices.exists(index=self.index_name):
            self.es.indices.create(
                index=self.index_name,
                mappings={
                    "properties": {
                        "doc_id": {"type": "keyword"},
                        "filename": {"type": "keyword"},
                        "page_count": {"type": "integer"},
                        "content": {"type": "text"},
                        "pages": {
                            "type": "nested",
                            "properties": {
                                "page_number": {"type": "integer"},
                                "text": {"type": "text"},
                                "source": {"type": "keyword"},
                            },
                        },
                    }
                },
            )

    def index_document(self, payload: dict) -> str:
        self.es.index(index=self.index_name, id=payload["doc_id"], document=payload)
        self.es.indices.refresh(index=self.index_name)
        return payload["doc_id"]

    def search(self, query: str, size: int = 10) -> List[dict]:
        resp = self.es.search(
            index=self.index_name,
            query={"match": {"content": query}},
            highlight={"fields": {"content": {}}},
            size=size,
        )
        return [hit["_source"] for hit in resp["hits"]["hits"]]

    def get(self, doc_id: str) -> Optional[dict]:
        try:
            return self.es.get(index=self.index_name, id=doc_id)["_source"]
        except Exception:
            return None

    def delete(self, doc_id: str) -> bool:
        try:
            self.es.delete(index=self.index_name, id=doc_id)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 2. Solr backend (the other system the job posting mentioned)
# ---------------------------------------------------------------------------
class SolrBackend(StorageBackend):
    """
    Requires a running Solr core, e.g.:

        docker run -d --name solr -p 8983:8983 solr:9.6 solr-precreate documents

    Solr's schema-less mode will auto-infer field types the first time a
    document is indexed, which is fine for a demo; a production core would
    ship an explicit managed-schema.xml instead.
    """

    def __init__(self, url: str = "http://localhost:8983/solr/documents"):
        import pysolr  # imported lazily; optional dep

        self.solr = pysolr.Solr(url, always_commit=True, timeout=10)

    def index_document(self, payload: dict) -> str:
        # Solr wants a flat document; nested "pages" become a multi-valued field.
        flat = {
            "id": payload["doc_id"],
            "doc_id": payload["doc_id"],
            "filename": payload["filename"],
            "page_count": payload["page_count"],
            "content": payload["content"],
            "page_text": [p["text"] for p in payload["pages"]],
        }
        self.solr.add([flat])
        return payload["doc_id"]

    def search(self, query: str, size: int = 10) -> List[dict]:
        results = self.solr.search(f"content:{query}", rows=size)
        return list(results)

    def get(self, doc_id: str) -> Optional[dict]:
        results = self.solr.search(f"id:{doc_id}")
        return results.docs[0] if results.docs else None

    def delete(self, doc_id: str) -> bool:
        self.solr.delete(id=doc_id)
        return True


# ---------------------------------------------------------------------------
# 3. Local fallback backend -- lets the whole pipeline run with zero
#    external infrastructure, e.g. to demo on a laptop or in a sandbox.
#    Same interface, so swapping it out for real ES/Solr is a one-line
#    change in main.py.
# ---------------------------------------------------------------------------
class LocalSQLiteBackend(StorageBackend):
    def __init__(self, db_path: str = "local_index.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                filename TEXT,
                page_count INTEGER,
                content TEXT,
                pages_json TEXT
            )"""
        )
        # Standalone FTS5 virtual table gives us real full-text search
        # (the same relevance-ranked search ES/Solr provide), kept in sync
        # manually rather than via SQLite's external-content triggers.
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(doc_id, content)"
        )
        self.conn.commit()

    def index_document(self, payload: dict) -> str:
        self.conn.execute(
            """INSERT OR REPLACE INTO documents (doc_id, filename, page_count, content, pages_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                payload["doc_id"],
                payload["filename"],
                payload["page_count"],
                payload["content"],
                json.dumps(payload["pages"]),
            ),
        )
        self.conn.execute("DELETE FROM documents_fts WHERE doc_id = ?", (payload["doc_id"],))
        self.conn.execute(
            "INSERT INTO documents_fts (doc_id, content) VALUES (?, ?)",
            (payload["doc_id"], payload["content"]),
        )
        self.conn.commit()
        return payload["doc_id"]

    def search(self, query: str, size: int = 10) -> List[dict]:
        rows = self.conn.execute(
            """SELECT d.doc_id, d.filename, d.page_count, d.content, d.pages_json
               FROM documents_fts f JOIN documents d ON f.doc_id = d.doc_id
               WHERE documents_fts MATCH ? LIMIT ?""",
            (query, size),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, doc_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT doc_id, filename, page_count, content, pages_json FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def delete(self, doc_id: str) -> bool:
        self.conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        self.conn.execute("DELETE FROM documents_fts WHERE doc_id = ?", (doc_id,))
        self.conn.commit()
        return True

    @staticmethod
    def _row_to_dict(row) -> dict:
        doc_id, filename, page_count, content, pages_json = row
        return {
            "doc_id": doc_id,
            "filename": filename,
            "page_count": page_count,
            "content": content,
            "pages": json.loads(pages_json),
        }


def get_backend(kind: str, **kwargs) -> StorageBackend:
    """Factory so main.py / demo.py can select a backend by name: 'elasticsearch' | 'solr' | 'local'."""
    kind = kind.lower()
    if kind in ("es", "elasticsearch"):
        return ElasticsearchBackend(**kwargs)
    if kind == "solr":
        return SolrBackend(**kwargs)
    if kind == "local":
        return LocalSQLiteBackend(**kwargs)
    raise ValueError(f"Unknown backend: {kind}")
