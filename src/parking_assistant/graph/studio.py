"""LangGraph development-tool entry point for the real Stage 2B topology."""

from parking_assistant.config import get_settings
from parking_assistant.db.session import create_database_engine, create_session_factory
from parking_assistant.graph.workflow import build_approval_graph
from parking_assistant.reservations.submission import ReservationSubmissionService

_settings = get_settings()
_engine = create_database_engine(_settings)
_reservations = ReservationSubmissionService(create_session_factory(_engine), _settings)

# LangGraph development tooling supplies its own checkpointer. Production/demo application paths
# use PostgresApprovalGraphProvider and PostgresSaver explicitly.
graph = build_approval_graph(_reservations, checkpointer=None)
