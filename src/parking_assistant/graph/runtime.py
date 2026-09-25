"""Defensive PostgreSQL checkpointer lifecycle for the approval graph."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from parking_assistant.config import Settings
from parking_assistant.graph.workflow import build_approval_graph
from parking_assistant.reservations.submission import ReservationSubmissionService


def _checkpoint_connection_kwargs(settings: Settings) -> dict[str, Any]:
    """Parse the SQLAlchemy URL into explicit psycopg parameters without logging it."""
    url = make_url(settings.database_url.get_secret_value())
    return {
        "host": url.host,
        "port": url.port,
        "dbname": url.database,
        "user": url.username,
        "password": url.password,
        "connect_timeout": 5,
    }


def _safe_serializer() -> JsonPlusSerializer:
    """Disable pickle fallback and permit only the graph's known JSON constructor."""
    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=[("langgraph", "types", "Interrupt")],
    )


class PostgresApprovalGraphProvider:
    """Open short-lived graph/checkpointer contexts over durable PostgreSQL state."""

    def __init__(
        self,
        settings: Settings,
        reservations: ReservationSubmissionService,
    ) -> None:
        self._connection_kwargs = _checkpoint_connection_kwargs(settings)
        self._reservations = reservations

    @contextmanager
    def open(self) -> Iterator[Any]:
        """Compile one graph instance against the shared durable checkpointer tables."""
        with psycopg.connect(
            **self._connection_kwargs,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        ) as connection:
            checkpointer = PostgresSaver(connection, serde=_safe_serializer())
            yield build_approval_graph(self._reservations, checkpointer)

    def setup(self) -> None:
        """Idempotently initialize and migrate PostgresSaver's own schema."""
        with psycopg.connect(
            **self._connection_kwargs,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        ) as connection:
            PostgresSaver(connection, serde=_safe_serializer()).setup()

    def delete_thread(self, thread_id: str) -> None:
        """Delete test/demo checkpoints for one exact opaque thread when explicitly requested."""
        with psycopg.connect(
            **self._connection_kwargs,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        ) as connection, connection.cursor() as cursor:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                cursor.execute(
                    f"DELETE FROM {table} WHERE thread_id = %s",
                    (thread_id,),
                )
