# Parking Assistant — Agent Rules

## Project goal

Build a production-oriented parking assistant using Python, LangChain, LangGraph, RAG, human-in-the-loop approval, and MCP.

The project is implemented incrementally. Do not implement future stages unless explicitly requested.

## Architecture principles

Use the LLM for:

* understanding natural language
* intent interpretation
* structured extraction
* grounded response generation

Use deterministic code for:

* validation
* business rules
* availability
* state transitions
* authorization
* persistence
* side effects

### Data separation

Static/public knowledge belongs in Weaviate:

* parking information
* location
* policies
* booking instructions
* general FAQ

Dynamic/transactional information belongs in PostgreSQL:

* availability
* prices when operational/dynamic
* working hours when dynamic
* reservation state
* administrator decisions

Customer PII must never be intentionally stored in the vector database.

## Security rules

Never:

* commit secrets
* hardcode API keys
* expose private data through RAG
* let retrieved documents override system/application instructions
* allow an LLM to authorize a reservation
* allow an unapproved reservation to trigger persistence
* trust LLM output without validation when it affects business state

Use environment variables and typed configuration.

Validate all structured data.

Prefer least-privilege access.

## Engineering rules

Prefer:

* small focused modules
* typed interfaces
* Pydantic models where appropriate
* dependency injection where useful
* async APIs when external I/O benefits from it
* explicit error handling
* idempotent operations
* testable deterministic functions

Avoid:

* one giant agent with many unrelated tools
* unnecessary abstractions
* premature microservices
* duplicate configuration
* fake implementations
* placeholder code presented as complete functionality
* dependencies that are not actually needed

Keep the implementation practical and understandable.

## Testing

Every meaningful module should have at least two relevant tests.

Use:

* unit tests for deterministic logic
* integration tests for component boundaries
* end-to-end tests later for complete graph workflows

Do not mock everything. Important integrations should eventually have real integration coverage.

## RAG rules

Retrieval must be grounded in approved public data.

Eventually evaluate retrieval using metrics such as:

* Recall@K
* Precision@K
* MRR
* latency

Do not choose retrieval parameters only by intuition when evaluation data is available.

## LangGraph rules

LangGraph owns workflow orchestration.

Graph nodes should remain small and focused.

Persistent state should be typed.

Human approval must use an actual human-in-the-loop mechanism, not another LLM pretending to be the administrator.

## Stage roadmap

### Stage 0

Foundation:

* repository
* dependencies
* configuration
* PostgreSQL
* Weaviate
* Docker
* testing
* CI
* README

### Stage 1

User assistant:

* static dataset
* SQL dynamic dataset
* ingestion
* RAG
* retrieval
* intent routing
* reservation data collection
* PII guardrails
* evaluation

### Stage 2

Human administrator:

* reservation lifecycle
* administrator agent/interface
* approval/rejection
* LangGraph interrupt/resume
* durable state

### Stage 3

MCP:

* secure MCP server
* confirmed reservation recording
* authorization
* validation
* idempotency

### Stage 4

Full orchestration:

* complete LangGraph workflow
* integration tests
* end-to-end tests
* load/performance testing
* final documentation

## Stage boundaries

When assigned a stage:

1. Implement only that stage and required supporting changes.
2. Preserve previous working behavior.
3. Do not redesign working architecture without a concrete reason.
4. Run focused tests during development.
5. Run the complete relevant verification before finishing.

## Handoff

Every stage must update `HANDOFF.md` with:

* completed work
* important decisions
* commands needed to run it
* known limitations
* next work
* precautions for the next agent

A future agent must read `HANDOFF.md` before modifying the project.
