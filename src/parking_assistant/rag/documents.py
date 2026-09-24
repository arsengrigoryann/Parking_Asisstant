"""Validated loading and deterministic chunking of approved public Markdown."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

STATIC_DATA_PATH = Path("data/static")


class PublicDocumentMetadata(BaseModel):
    """Required front matter for approved public knowledge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    facility_id: str = Field(min_length=1)
    visibility: str

    @field_validator("visibility")
    @classmethod
    def require_public_visibility(cls, value: str) -> str:
        if value != "public":
            msg = "only documents with visibility == 'public' may be loaded"
            raise ValueError(msg)
        return value


class PublicDocument(BaseModel):
    """One validated source document."""

    model_config = ConfigDict(frozen=True)

    metadata: PublicDocumentMetadata
    content: str = Field(min_length=1)
    source: str = Field(min_length=1)


class DocumentChunk(BaseModel):
    """One stable, metadata-rich unit for retrieval."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    content: str
    source_document_id: str
    source: str
    title: str
    category: str
    facility_id: str
    visibility: str
    chunk_index: int = Field(ge=0)

    def properties(self) -> dict[str, str | int]:
        """Return the exact property payload stored in Weaviate."""
        return self.model_dump()


class DocumentValidationError(ValueError):
    """Raised when an approved-document candidate is malformed or not public."""


def _parse_front_matter(text: str, source: Path) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise DocumentValidationError(f"{source}: missing opening front-matter delimiter")
    try:
        closing_index = next(
            index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"
        )
    except StopIteration as error:
        message = f"{source}: missing closing front-matter delimiter"
        raise DocumentValidationError(message) from error

    metadata: dict[str, str] = {}
    for line in lines[1:closing_index]:
        if not line.strip() or ":" not in line:
            raise DocumentValidationError(f"{source}: invalid front-matter line: {line!r}")
        key, value = line.split(":", 1)
        normalized_key = key.strip()
        if not normalized_key or normalized_key in metadata:
            raise DocumentValidationError(f"{source}: invalid or duplicate metadata key")
        metadata[normalized_key] = value.strip()

    body = "\n".join(lines[closing_index + 1 :]).strip()
    if not body:
        raise DocumentValidationError(f"{source}: document body must not be empty")
    return metadata, body


def load_public_document(path: Path) -> PublicDocument:
    """Load one Markdown document and reject invalid or non-public metadata."""
    metadata_values, body = _parse_front_matter(path.read_text(encoding="utf-8"), path)
    try:
        metadata = PublicDocumentMetadata.model_validate(metadata_values)
    except ValidationError as error:
        message = f"{path}: invalid public document metadata: {error}"
        raise DocumentValidationError(message) from error
    return PublicDocument(metadata=metadata, content=body, source=path.name)


def load_public_documents(directory: Path = STATIC_DATA_PATH) -> list[PublicDocument]:
    """Load all and only Markdown candidates from the approved static directory."""
    paths = sorted(directory.glob("*.md"))
    if not paths:
        raise DocumentValidationError(f"no Markdown documents found in {directory}")
    documents = [load_public_document(path) for path in paths]
    document_ids = [document.metadata.id for document in documents]
    if len(document_ids) != len(set(document_ids)):
        raise DocumentValidationError("source document ids must be unique")
    return documents


def _chunk_id(document_id: str, chunk_index: int, content: str) -> str:
    payload = f"{document_id}\0{chunk_index}\0{content}".encode()
    return sha256(payload).hexdigest()


def chunk_documents(
    documents: list[PublicDocument], *, chunk_size: int, chunk_overlap: int
) -> list[DocumentChunk]:
    """Split documents deterministically while preserving all approved metadata."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
        keep_separator=True,
    )
    chunks: list[DocumentChunk] = []
    for document in documents:
        for index, content in enumerate(splitter.split_text(document.content)):
            metadata = document.metadata
            chunks.append(
                DocumentChunk(
                    chunk_id=_chunk_id(metadata.id, index, content),
                    content=content,
                    source_document_id=metadata.id,
                    source=document.source,
                    title=metadata.title,
                    category=metadata.category,
                    facility_id=metadata.facility_id,
                    visibility=metadata.visibility,
                    chunk_index=index,
                )
            )
    return chunks
