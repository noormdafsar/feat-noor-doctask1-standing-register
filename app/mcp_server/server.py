from __future__ import annotations

"""The machine face.

Nine tools over the same service layer the REST API and the React interface use.
Nothing a person can do through the interface is unavailable to a program, and
that specifically includes the approval gate: `decide_approval` is the only way
past it for either of them. There is no auto-approve flag.
"""

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.domain import approvals, deliverable, families, runs

mcp = FastMCP("standing-register")


@mcp.tool()
def ingest_documents(family: str, paths: list[str]) -> str:
    """Ingest documents into a contract family from paths on the server.

    Returns one result per file: accepted, duplicate (identical content already
    present) or rejected (outside the declared format set), each with a reason.
    """
    fam = families.ensure_family(family)
    out = families.ingest_paths(fam, [Path(p) for p in paths])
    return json.dumps({"family_id": fam, "results": out}, indent=2)


@mcp.tool()
def start_run(
    family: str,
    mode: str = "full",
    rule_pack: str = "procurement-baseline",
    trigger_document_id: str | None = None,
) -> str:
    """Build or update the register. Runs until it finishes or stops at the gate.

    mode is 'full' or 'incremental'. The return value carries the run id, the
    per-stage decision log, and the approval batch id if a human is now needed.
    """
    return json.dumps(
        runs.start_run(
            family_id=families.resolve(family),
            mode=mode,
            rule_pack=rule_pack,
            trigger_document_id=trigger_document_id,
        ),
        indent=2,
    )


@mcp.tool()
def get_run_status(run_id: str) -> str:
    """Current status of a run, including every stage decision it has taken."""
    return json.dumps(runs.status(run_id), indent=2)


@mcp.tool()
def list_pending_approvals(run_id: str) -> str:
    """The review batch: proposed row updates, findings, conflicts and escalations,
    each with its evidence. Every item must be decided before the run can commit."""
    return json.dumps(approvals.pending_for_run(run_id), indent=2)


@mcp.tool()
def decide_approval(batch_id: str, decisions: list[dict[str, Any]]) -> str:
    """Accept or reject review items, one by one.

    Each decision is {"item_id": "...", "decision": "accept"|"reject", "reason": "..."}.
    Rejecting one item has no effect on any other. Partial decisions persist, so
    this can be called repeatedly until the batch is fully decided.
    """
    return json.dumps(approvals.decide(batch_id, decisions, decided_by="mcp"), indent=2)


@mcp.tool()
def resume_run(run_id: str) -> str:
    """Continue a run past the gate once every item is decided, or from its last
    checkpoint if the process was interrupted."""
    return json.dumps(runs.resume_run(run_id), indent=2)


@mcp.tool()
def get_deliverable(family: str, format: str = "json") -> str:
    """The published register. format is 'json' or 'markdown'.

    Reflects only what a human approved, not the newest thing the system believes.
    """
    fam = families.resolve(family)
    if format == "markdown":
        return deliverable.register(fam, "markdown")
    return json.dumps(deliverable.register(fam, "json"), indent=2)


@mcp.tool()
def get_provenance(family: str, term_key: str) -> str:
    """Where a register value came from: document, offsets, quote, and the full
    amendment chain that produced the current value."""
    return json.dumps(deliverable.provenance(families.resolve(family), term_key), indent=2)


@mcp.tool()
def explain_change(family: str, since: str | None = None) -> str:
    """What changed, when, and because of which source."""
    return json.dumps(deliverable.changelog(families.resolve(family), since), indent=2)


@mcp.tool()
def get_findings(family: str, run_id: str | None = None) -> str:
    """Rule findings with evidence. An empty list is a real, honest answer."""
    return json.dumps(deliverable.findings(families.resolve(family), run_id), indent=2)


@mcp.tool()
def get_run_cost(run_id: str) -> str:
    """What the run spent and where the time went, stage by stage."""
    return json.dumps(runs.cost(run_id), indent=2)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
