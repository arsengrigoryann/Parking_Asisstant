"""Unit tests for public document validation and deterministic chunking."""

from pathlib import Path

import pytest

from parking_assistant.rag.documents import (
    DocumentValidationError,
    chunk_documents,
    load_public_document,
    load_public_documents,
)


def write_document(path: Path, *, visibility: str = "public", body: str = "Public body") -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                "id: doc-one",
                "title: Document One",
                "category: faq",
                "facility_id: facility-one",
                f"visibility: {visibility}",
                "---",
                "",
                body,
            ]
        ),
        encoding="utf-8",
    )


def test_public_document_validation_and_metadata_preservation(tmp_path: Path) -> None:
    source = tmp_path / "approved.md"
    write_document(source, body="First paragraph.\n\nSecond paragraph.")

    document = load_public_document(source)
    chunks = chunk_documents([document], chunk_size=200, chunk_overlap=20)

    assert document.metadata.visibility == "public"
    assert len(chunks) == 1
    assert chunks[0].source_document_id == "doc-one"
    assert chunks[0].source == "approved.md"
    assert chunks[0].category == "faq"
    assert chunks[0].facility_id == "facility-one"


def test_non_public_document_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "private.md"
    write_document(source, visibility="private")

    with pytest.raises(DocumentValidationError, match="visibility"):
        load_public_document(source)


def test_chunk_ids_are_deterministic_and_content_sensitive(tmp_path: Path) -> None:
    source = tmp_path / "approved.md"
    write_document(source, body=("A useful public sentence. " * 30).strip())
    document = load_public_document(source)

    first = chunk_documents([document], chunk_size=200, chunk_overlap=30)
    second = chunk_documents([document], chunk_size=200, chunk_overlap=30)

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert len({chunk.chunk_id for chunk in first}) == len(first)


def test_loader_rejects_duplicate_document_ids(tmp_path: Path) -> None:
    write_document(tmp_path / "one.md")
    write_document(tmp_path / "two.md")

    with pytest.raises(DocumentValidationError, match="ids must be unique"):
        load_public_documents(tmp_path)
