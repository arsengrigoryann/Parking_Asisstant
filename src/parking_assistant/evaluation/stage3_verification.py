"""Execute the Stage 3 security, reliability, and MCP Inspector evidence flow."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import httpx
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import delete

from parking_assistant.api.main import create_app
from parking_assistant.config import Settings, get_settings
from parking_assistant.db.models import ReservationRequest, ReservationRequestStatus
from parking_assistant.db.seed import FACILITY_ID, seed_data
from parking_assistant.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from parking_assistant.evaluation.stage3_support import (
    free_local_port,
    running_server,
    synthetic_details,
)
from parking_assistant.mcp.client import MCPRecordingError, ReservationMCPClient
from parking_assistant.mcp.recorder import ApprovedReservationRecorder
from parking_assistant.mcp.server import build_mcp_server, create_http_app
from parking_assistant.reservations.submission import ReservationSubmissionService

DEFAULT_OUTPUT = Path("evaluation/stage3_verification_report.json")


class Stage3VerificationReport(BaseModel):
    """Machine-readable results retained from one real Stage 3 evidence run."""

    model_config = ConfigDict(frozen=True)

    approved_cases_passed: int
    unauthorized_cases_tested: int
    unauthorized_cases_blocked: int
    unauthorized_states: list[str]
    duplicate_records_created: int
    concurrent_duplicate_records_created: int
    authentication_rejections: int
    configured_path_enforced: bool
    transport_success: bool
    transport_failures: int
    retry_after_temporary_failure_passed: bool
    file_format_checks: int
    file_format_passed: bool
    tool_count: int
    tool_names: list[str]
    tool_input_fields: list[str]
    inspector_cli_verified: bool
    inspector_tool_list_verified: bool
    inspector_invocation_verified: bool


def inspector_tool_list_verified(output: str) -> bool:
    """Check retained Inspector output for the sole bounded tool and input field."""
    return (
        "record_approved_reservation" in output
        and "reservation_id" in output
        and "first_name" not in output
        and "car_number" not in output
    )


def inspector_invocation_verified(output: str) -> bool:
    """Check Inspector call output for one valid typed outcome."""
    return "already_recorded" in output or '"recorded"' in output


def _run_inspector(url: str, token: str, reservation_id: UUID) -> tuple[bool, bool]:
    executable = shutil.which("npx.cmd") or shutil.which("npx")
    if executable is None:
        raise RuntimeError("Node npx is required for official MCP Inspector verification")
    common = [
        executable,
        "--yes",
        "@modelcontextprotocol/inspector@2.5.0",
        "--cli",
        "--transport",
        "http",
        "--server-url",
        url,
        "--header",
        f"Authorization: Bearer {token}",
        "--format",
        "json",
    ]
    listed = subprocess.run(
        [*common, "--method", "tools/list"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    called = subprocess.run(
        [
            *common,
            "--method",
            "tools/call",
            "--tool-name",
            "record_approved_reservation",
            "--tool-arg",
            f"reservation_id={reservation_id}",
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    list_ok = listed.returncode == 0 and inspector_tool_list_verified(listed.stdout)
    call_ok = called.returncode == 0 and inspector_invocation_verified(called.stdout)
    if not list_ok or not call_ok:
        raise RuntimeError("official MCP Inspector verification failed; sensitive output omitted")
    return list_ok, call_ok


def run_stage3_verification(
    settings: Settings,
    *,
    inspector: bool,
) -> Stage3VerificationReport:  # pragma: no cover - real evidence CLI
    """Run real PostgreSQL, API, MCP transport, file, and optional Inspector checks."""
    port = free_local_port()
    mcp_token = f"stage3-evidence-{uuid4()}"
    admin_token = f"stage3-admin-{uuid4()}"
    reservation_ids: list[UUID] = []
    inspector_list_ok = False
    inspector_call_ok = False
    with TemporaryDirectory(prefix="parking-stage3-evidence-") as directory:
        root = Path(directory)
        output = root / "configured" / "confirmed.txt"
        evidence_settings = settings.model_copy(
            update={
                "langsmith_tracing": False,
                "admin_api_token": SecretStr(admin_token),
                "admin_api_identity": "stage3-evidence-admin",
                "mcp_server_host": "127.0.0.1",
                "mcp_server_port": port,
                "mcp_server_token": SecretStr(mcp_token),
                "mcp_reservation_file": output,
            }
        )
        engine = create_database_engine(evidence_settings)
        factory = create_session_factory(engine)
        reservations = ReservationSubmissionService(factory, evidence_settings)

        def submit(label: str, offset: int) -> UUID:
            row = reservations.submit_for_approval(
                synthetic_details(label, minute_offset=offset),
                facility_id=FACILITY_ID,
                idempotency_key=f"stage3-evidence-{label}-{uuid4()}",
            )
            reservation_ids.append(row.id)
            return row.id

        try:
            with session_scope(factory) as session:
                seed_data(session)
            approved_id = submit("approved", 0)
            pending_id = submit("pending", 1)
            rejected_id = submit("rejected", 2)
            cancelled_id = submit("cancelled", 3)
            concurrent_id = submit("concurrent", 4)
            retry_id = submit("retry", 5)
            reservations.reject(rejected_id, decision_by="stage3-evidence-admin")
            reservations.cancel(cancelled_id, decision_by="stage3-evidence-admin")
            reservations.approve(concurrent_id, decision_by="stage3-evidence-admin")

            recorder = ApprovedReservationRecorder(reservations, output)
            tools = asyncio.run(build_mcp_server(recorder).list_tools())
            tool_names = [tool.name for tool in tools]
            tool_fields = sorted(tools[0].input_schema["properties"])
            assert tool_names == ["record_approved_reservation"]
            assert tool_fields == ["reservation_id"]

            mcp_app = create_http_app(settings=evidence_settings, recorder=recorder)
            with running_server(mcp_app, port):
                unauthorized = httpx.post(
                    evidence_settings.mcp_server_url,
                    json={},
                    timeout=5,
                )
                assert unauthorized.status_code == 401
                client = ReservationMCPClient(evidence_settings)
                api = create_app(
                    settings=evidence_settings,
                    service=reservations,
                    approved_recorder=client,
                )
                headers = {"Authorization": f"Bearer {admin_token}"}
                with TestClient(api) as api_client:
                    approved_response = api_client.post(
                        f"/admin/reservations/{approved_id}/approve",
                        headers=headers,
                    )
                    assert approved_response.status_code == 200
                    assert reservations.get(approved_id).status is ReservationRequestStatus.APPROVED
                    assert len(output.read_text(encoding="utf-8").splitlines()) == 1

                    second = client.record_if_approved(approved_id)
                    assert second.outcome == "already_recorded"
                    assert len(output.read_text(encoding="utf-8").splitlines()) == 1

                    before_unauthorized = output.read_text(encoding="utf-8")
                    blocked = 0
                    for reservation_id in (
                        pending_id,
                        rejected_id,
                        cancelled_id,
                        uuid4(),
                    ):
                        try:
                            client.record_if_approved(reservation_id)
                        except MCPRecordingError:
                            blocked += 1
                    assert blocked == 4
                    assert output.read_text(encoding="utf-8") == before_unauthorized

                    class TemporaryFailure:
                        calls = 0

                        def record_if_approved(self, reservation_id: UUID) -> object:
                            self.calls += 1
                            raise MCPRecordingError("synthetic temporary failure")

                    failing = TemporaryFailure()
                    failing_api = create_app(
                        settings=evidence_settings,
                        service=reservations,
                        approved_recorder=failing,  # type: ignore[arg-type]
                    )
                    with TestClient(failing_api) as failing_client:
                        failed = failing_client.post(
                            f"/admin/reservations/{retry_id}/approve",
                            headers=headers,
                        )
                    assert failed.status_code == 503
                    assert reservations.get(retry_id).status is ReservationRequestStatus.APPROVED
                    retried = api_client.post(
                        f"/admin/reservations/{retry_id}/approve",
                        headers=headers,
                    )
                    assert retried.status_code == 200

                if inspector:
                    inspector_list_ok, inspector_call_ok = _run_inspector(
                        evidence_settings.mcp_server_url,
                        mcp_token,
                        approved_id,
                    )

            before_concurrent = output.read_text(encoding="utf-8").splitlines()
            with ThreadPoolExecutor(max_workers=8) as executor:
                concurrent_results = list(
                    executor.map(lambda _: recorder.record(concurrent_id), range(16))
                )
            after_concurrent = output.read_text(encoding="utf-8").splitlines()
            concurrent_recorded = sum(result.recorded for result in concurrent_results)
            assert concurrent_recorded == 1
            assert len(after_concurrent) == len(before_concurrent) + 1

            lines = output.read_text(encoding="utf-8").splitlines()
            approved_line = lines[0]
            parts = approved_line.split(" | ")
            format_checks = [
                len(parts) == 5,
                parts[0] == "Stage Three",
                parts[1].startswith("S3"),
                parts[2] == "Central Station Parking",
                "\N{EN DASH}" in parts[3] and "+00:00" in parts[3],
                "+00:00" in parts[4],
            ]
            assert all(format_checks)
            assert not (root / "confirmed.txt").exists()
        finally:
            if reservation_ids:
                with session_scope(factory) as session:
                    session.execute(
                        delete(ReservationRequest).where(
                            ReservationRequest.id.in_(reservation_ids)
                        )
                    )
            engine.dispose()

    return Stage3VerificationReport(
        approved_cases_passed=2,
        unauthorized_cases_tested=4,
        unauthorized_cases_blocked=blocked,
        unauthorized_states=["PENDING_APPROVAL", "REJECTED", "CANCELLED", "UNKNOWN"],
        duplicate_records_created=0,
        concurrent_duplicate_records_created=concurrent_recorded - 1,
        authentication_rejections=1,
        configured_path_enforced=True,
        transport_success=True,
        transport_failures=0,
        retry_after_temporary_failure_passed=True,
        file_format_checks=len(format_checks),
        file_format_passed=all(format_checks),
        tool_count=len(tool_names),
        tool_names=tool_names,
        tool_input_fields=tool_fields,
        inspector_cli_verified=inspector and inspector_list_ok and inspector_call_ok,
        inspector_tool_list_verified=inspector_list_ok,
        inspector_invocation_verified=inspector_call_ok,
    )


def main() -> None:  # pragma: no cover - real evidence CLI
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspector", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    report = run_stage3_verification(get_settings(), inspector=arguments.inspector)
    arguments.output.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report.model_dump(mode="json"), indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
