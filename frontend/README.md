# Parking Assistant UI

This Stage 4A demo frontend is adapted from the official
[LangChain Agent Chat UI](https://github.com/langchain-ai/agent-chat-ui). The upstream Next.js
structure, UI components, and license are retained. Project additions are limited to parking
workflow status, authenticated reservation review, human decision controls, and MCP retry.

The browser calls only local `/api/parking/*` routes. These Next.js server routes proxy the Python
API and add `ADMIN_API_TOKEN` only for administrator operations. Never prefix server secrets with
`NEXT_PUBLIC_`.

## Run

With `parking_assistant.api.unified:app` on port 8000:

```powershell
Copy-Item .env.example .env
corepack pnpm install --frozen-lockfile
corepack pnpm dev
```

Open `http://localhost:3000`.

## Verify

```powershell
corepack pnpm format:check
corepack pnpm build
```

The authenticated review intentionally displays reservation PII. It must not be logged, embedded,
placed in URLs, or copied into LangGraph checkpoints.
