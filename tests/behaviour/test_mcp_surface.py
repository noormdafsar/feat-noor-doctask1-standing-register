from __future__ import annotations

import asyncio
import json

from app.mcp_server.server import mcp
from tests.conftest import CORPUS, arrival


def call(name: str, **kwargs) -> str:
    """Go through the MCP tool surface, not the Python functions behind it.

    The point of behaviour 4 is that a program can drive the system through the
    published interface, so the test drives the published interface.
    """
    result = asyncio.run(mcp.call_tool(name, kwargs))
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, list):
        parts = []
        for item in result:
            parts.append(getattr(item, "text", None) or str(item))
        return "\n".join(parts)
    return str(result)


def test_a_program_can_drive_the_whole_flow_including_the_gate():
    files = [str(p) for p in sorted((CORPUS / "acme-v1").iterdir()) if p.is_file()]
    ingested = json.loads(call("ingest_documents", family="acme-v1", paths=files))
    assert all(r["status"] == "accepted" for r in ingested["results"])

    run = json.loads(call("start_run", family="acme-v1", rule_pack="procurement-baseline"))
    assert run["status"] == "awaiting_approval"
    run_id = run["run_id"]

    status = json.loads(call("get_run_status", run_id=run_id))
    assert status["steps"], "a program must be able to see what the system decided"

    batch = json.loads(call("list_pending_approvals", run_id=run_id))
    assert batch["items"]

    # The gate is an explicit operation on this surface. There is no flag that
    # skips it, and a program has to make the call the same way a person does.
    decisions = [
        {
            "item_id": i["item_id"],
            "decision": "reject" if i["ref_id"] == "sla_credit_pct" else "accept",
            "reason": "not tracked by this team" if i["ref_id"] == "sla_credit_pct" else None,
        }
        for i in batch["items"]
    ]
    decided = json.loads(call("decide_approval", batch_id=batch["batch_id"], decisions=decisions))
    assert decided["still_pending"] == 0

    final = json.loads(call("resume_run", run_id=run_id))
    assert final["status"] == "complete"

    register = json.loads(call("get_deliverable", family="acme-v1", format="json"))
    keys = {r["term_key"] for r in register["rows"]}
    assert "unit_price" in keys
    assert "sla_credit_pct" not in keys, "the rejection made through MCP must hold"

    prov = json.loads(call("get_provenance", family="acme-v1", term_key="unit_price"))
    assert prov["chain"] and prov["chain"][-1]["quote"]

    cost = json.loads(call("get_run_cost", run_id=run_id))
    assert cost["total"]["ms"] >= 0

    findings = json.loads(call("get_findings", family="acme-v1"))
    assert findings["count"] >= 1


def test_markdown_export_and_changelog_over_mcp():
    files = [str(p) for p in sorted((CORPUS / "acme-v1").iterdir()) if p.is_file()]
    call("ingest_documents", family="acme-v1", paths=files)
    run = json.loads(call("start_run", family="acme-v1"))
    batch = json.loads(call("list_pending_approvals", run_id=run["run_id"]))
    call(
        "decide_approval",
        batch_id=batch["batch_id"],
        decisions=[{"item_id": i["item_id"], "decision": "accept"} for i in batch["items"]],
    )
    call("resume_run", run_id=run["run_id"])

    call("ingest_documents", family="acme-v1", paths=[str(arrival("08-amendment-3.md"))])
    run2 = json.loads(call("start_run", family="acme-v1", mode="incremental"))
    assert run2["stats"]["rows_changed"] == 1

    batch2 = json.loads(call("list_pending_approvals", run_id=run2["run_id"]))
    call(
        "decide_approval",
        batch_id=batch2["batch_id"],
        decisions=[{"item_id": i["item_id"], "decision": "accept"} for i in batch2["items"]],
    )
    call("resume_run", run_id=run2["run_id"])

    md = call("get_deliverable", family="acme-v1", format="markdown")
    assert "Obligation and Commercial Register" in md

    log = json.loads(call("explain_change", family="acme-v1"))
    assert log["entries"][0]["term_key"] == "unit_price"
    assert log["entries"][0]["because_of"] == "08-amendment-3.md"
