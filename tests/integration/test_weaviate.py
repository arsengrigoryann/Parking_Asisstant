"""Real authenticated Weaviate readiness integration coverage."""

import os

import pytest

from parking_assistant.rag.weaviate import weaviate_is_ready

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 to use local services",
    ),
]


def test_authenticated_weaviate_is_ready() -> None:
    assert weaviate_is_ready() is True
