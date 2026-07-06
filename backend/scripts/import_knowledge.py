#!/usr/bin/env python3
"""Import APM knowledge documents into the Qdrant knowledge base.

Usage:
    python scripts/import_knowledge.py import-all
    python scripts/import_knowledge.py import-one knowledge/01-xxx.md
    python scripts/import_knowledge.py sync
    python scripts/import_knowledge.py list
    python scripts/import_knowledge.py delete knowledge/01-xxx.md
    python scripts/import_knowledge.py verify
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow running from backend/ without PYTHONPATH
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from personal_assistant.agent.router import OllamaBgeM3EmbeddingProvider
from personal_assistant.config import get_settings
from personal_assistant.knowledge.chunker import ChunkConfig, MarkdownChunker
from personal_assistant.knowledge.importer import KnowledgeImporter, doc_id_from_path
from personal_assistant.knowledge.qdrant_store import QdrantKnowledgeStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _build_importer(settings):
    store = QdrantKnowledgeStore(
        url=settings.knowledge_qdrant_url,
        collection=settings.knowledge_qdrant_collection,
        api_key=settings.knowledge_qdrant_api_key,
    )
    chunker = MarkdownChunker(ChunkConfig(
        max_chunk_tokens=settings.knowledge_chunk_max_tokens,
        context_buffer_tokens=settings.knowledge_chunk_context_buffer,
    ))
    embedding = OllamaBgeM3EmbeddingProvider(
        base_url=settings.skill_routing_ollama_base_url,
        model=settings.skill_routing_embedding_model,
    )
    knowledge_dir = Path(settings.knowledge_dir)
    if not knowledge_dir.is_absolute():
        knowledge_dir = _PROJECT_ROOT.parent / knowledge_dir
    return KnowledgeImporter(
        store=store,
        chunker=chunker,
        knowledge_dir=knowledge_dir,
        embedding_provider=embedding,
        version=settings.knowledge_version,
    )


def cmd_import_all(settings) -> None:
    importer = _build_importer(settings)
    results = asyncio.run(importer.import_all())
    if not results:
        logger.info("No documents imported (all up-to-date or empty directory)")
        return
    total_chunks = sum(results.values())
    logger.info("Imported %d document(s), %d total chunks", len(results), total_chunks)


def cmd_import_one(settings, file_path: str) -> None:
    importer = _build_importer(settings)
    count = asyncio.run(importer.import_file(file_path))
    logger.info("Imported %s → %d chunks", file_path, count)


def cmd_sync(settings) -> None:
    importer = _build_importer(settings)
    results = asyncio.run(importer.sync())
    if not results:
        logger.info("No changes detected — all documents up to date")
        return
    total_chunks = sum(results.values())
    logger.info("Synced %d document(s), %d total chunks", len(results), total_chunks)


def cmd_list(settings) -> None:
    importer = _build_importer(settings)
    docs = importer.list_documents()
    if not docs:
        logger.info("No documents in knowledge base")
        return
    logger.info("%d document(s) in knowledge base:", len(docs))
    for doc in docs:
        logger.info(
            "  [%s] %s (v%s, %d chunks, updated %s)",
            doc.doc_id[:12], doc.title, doc.version,
            doc.total_chunks, doc.updated_at[:10],
        )


def cmd_delete(settings, target: str) -> None:
    importer = _build_importer(settings)
    deleted = asyncio.run(importer.delete_document(target))
    logger.info("Deleted %d chunks for '%s'", deleted, target)


def cmd_verify(settings) -> None:
    importer = _build_importer(settings)
    docs = importer.list_documents()
    if not docs:
        logger.info("No documents in knowledge base — run 'import-all' first")
        return
    issues = []
    for doc in docs:
        count = importer.store.get_chunk_count(doc.doc_id)
        if count != doc.total_chunks:
            issues.append(
                f"{doc.title}: expected {doc.total_chunks} chunks, found {count}"
            )
    if issues:
        logger.warning("Found %d issue(s):", len(issues))
        for issue in issues:
            logger.warning("  %s", issue)
    else:
        logger.info(
            "All %d document(s) verified successfully (%d chunks OK)",
            len(docs), sum(d.total_chunks for d in docs),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="APM Knowledge Base Importer")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    subparsers.add_parser("import-all", help="Import all markdown docs from knowledge/")
    import_one = subparsers.add_parser("import-one", help="Import a single file")
    import_one.add_argument("file", help="Path to markdown file")
    subparsers.add_parser("sync", help="Incremental sync (only changed files)")
    subparsers.add_parser("list", help="List all documents in the knowledge base")
    delete_cmd = subparsers.add_parser("delete", help="Delete a document")
    delete_cmd.add_argument("target", help="File path or doc_id")
    subparsers.add_parser("verify", help="Verify chunk integrity")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    settings = get_settings()
    if not settings.knowledge_qdrant_url:
        logger.error("KNOWLEDGE_QDRANT_URL is not configured in .env")
        sys.exit(1)

    commands = {
        "import-all": lambda: cmd_import_all(settings),
        "import-one": lambda: cmd_import_one(settings, args.file),
        "sync": lambda: cmd_sync(settings),
        "list": lambda: cmd_list(settings),
        "delete": lambda: cmd_delete(settings, args.target),
        "verify": lambda: cmd_verify(settings),
    }
    commands[args.command]()


if __name__ == "__main__":
    main()
