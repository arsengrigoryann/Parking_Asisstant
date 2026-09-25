"""Minimal one-shot and interactive Stage 1D assistant demonstration."""

import argparse
import json

from parking_assistant.application import create_conversation_service


def main() -> None:  # pragma: no cover - real CLI is covered by integration tests
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", help="one parking question or request")
    parser.add_argument("--interactive", action="store_true", help="keep in-memory session state")
    args = parser.parse_args()
    if not args.interactive and args.query is None:
        parser.error("query is required unless --interactive is used")

    with create_conversation_service() as service:
        if args.interactive:
            print("Parking Assistant interactive mode. Enter :submit or :quit.")
            while True:
                message = input("> ").strip()
                if message == ":quit":
                    break
                if message == ":submit":
                    escalation = service.escalate_completed("cli-session")
                    print(json.dumps(escalation.model_dump(mode="json"), indent=2))
                    continue
                if message:
                    print(service.handle_message(message, "cli-session").answer)
        else:
            assert args.query is not None
            response = service.handle_message(args.query, "cli-session")
            print(json.dumps(response.model_dump(mode="json"), indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
