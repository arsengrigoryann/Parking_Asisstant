"""LangGraph Studio entry point for the complete Stage 4 topology."""

from parking_assistant.api.unified import create_unified_app
from parking_assistant.graph.master import build_master_graph

_app = create_unified_app()
_components = _app.state.stage4_components

# Studio supplies its own checkpointer. The local unified API uses PostgresSaver.
graph = build_master_graph(
    _components["conversation"],
    _components["reservations"],
    _components["recorder"],
    checkpointer=None,
)
