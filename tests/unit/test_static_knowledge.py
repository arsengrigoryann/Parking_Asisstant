"""Validation for the approved public knowledge corpus."""

from pathlib import Path

STATIC_DATA = Path("data/static")
REQUIRED_METADATA = {"id", "title", "category", "facility_id", "visibility"}
FORBIDDEN_FIELDS = {
    "customer_name",
    "license_plate",
    "reservation_history",
    "administrator_notes",
    "credentials",
    "api_key",
}


def read_front_matter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    marker, front_matter, body = text.split("---", maxsplit=2)
    assert marker == ""
    metadata = {
        key.strip(): value.strip()
        for line in front_matter.strip().splitlines()
        for key, value in [line.split(":", maxsplit=1)]
    }
    return metadata, body.strip()


def test_every_static_document_has_required_public_metadata() -> None:
    documents = sorted(STATIC_DATA.glob("*.md"))
    assert len(documents) == 7

    for document in documents:
        metadata, body = read_front_matter(document)
        assert metadata.keys() >= REQUIRED_METADATA, document
        assert metadata["visibility"] == "public", document
        assert all(metadata[field] for field in REQUIRED_METADATA), document
        assert body, document


def test_static_documents_exclude_private_metadata_fields() -> None:
    for document in STATIC_DATA.glob("*.md"):
        metadata, _ = read_front_matter(document)
        assert FORBIDDEN_FIELDS.isdisjoint(metadata.keys()), document

        content = document.read_text(encoding="utf-8").lower()
        for forbidden_phrase in (
            "customer name",
            "license plate",
            "reservation history",
            "administrator note",
            "api key",
            "credential",
        ):
            assert forbidden_phrase not in content, document


def test_document_ids_are_unique_and_facility_is_consistent() -> None:
    metadata = [read_front_matter(path)[0] for path in STATIC_DATA.glob("*.md")]

    assert len({item["id"] for item in metadata}) == len(metadata)
    assert {item["facility_id"] for item in metadata} == {
        "91e669b8-bdd9-534d-95b3-15671fe6531d"
    }
