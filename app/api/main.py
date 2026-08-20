from __future__ import annotations

from typing import Any

import logging

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import __version__
from app.domain import approvals, deliverable, families, runs
from app.rules.loader import list_packs

app = FastAPI(
    title="Standing Register",
    version=__version__,
    description=(
        "A register of contract obligations that keeps itself current. Every "
        "operation the review interface performs is available here, including the "
        "approval gate -- see POST /approvals/{batch_id}/decide."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartRun(BaseModel):
    family: str
    mode: str = "full"
    rule_pack: str = "procurement-baseline"
    trigger_document_id: str | None = None


class Decision(BaseModel):
    item_id: str | None = None
    ref_id: str | None = None
    decision: str
    reason: str | None = None


class DecideBody(BaseModel):
    decisions: list[Decision]
    decided_by: str = "api"


log = logging.getLogger("standing-register")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defence: never answer a client with a bare 500.

    An unexpected failure is still a failure, but the caller gets the exception
    class and message so they can report something useful, and the traceback goes
    to the server log. "Internal Server Error" with no body tells a user nothing
    and tells a developer almost as little.
    """
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "where": f"{request.method} {request.url.path}",
            "hint": "This is a bug. The full traceback is in the API log (`make logs`).",
        },
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "rule_packs": list_packs()}


@app.get("/families")
def get_families() -> list[dict[str, Any]]:
    return families.list_families()


@app.post("/families/{slug}/documents")
async def upload(slug: str, files: list[UploadFile]) -> dict[str, Any]:
    fam = _resolve(slug)
    if not files:
        raise HTTPException(400, "No files were sent. Choose at least one file to upload.")
    results = []
    for f in files:
        name = f.filename or "unnamed"
        try:
            blob = await f.read()
        except Exception as exc:  # noqa: BLE001
            results.append(
                {"filename": name, "status": "rejected",
                 "reason": f"{name}: upload stream failed ({type(exc).__name__}: {exc})"}
            )
            continue
        results.append(families.ingest_bytes(fam, name, blob))
    accepted = sum(1 for r in results if r["status"] == "accepted")
    return {
        "family_id": fam,
        "accepted": accepted,
        "rejected": sum(1 for r in results if r["status"] == "rejected"),
        "duplicate": sum(1 for r in results if r["status"] == "duplicate"),
        "results": results,
    }


@app.get("/families/{slug}/documents")
def documents(slug: str) -> list[dict[str, Any]]:
    return families.list_documents(_resolve(slug))


@app.get("/documents/{document_id}/text")
def document_text(document_id: str) -> dict[str, Any]:
    try:
        return families.document_text(document_id)
    except LookupError:
        raise HTTPException(404, f"no document {document_id}")


@app.post("/runs")
def start_run(body: StartRun) -> dict[str, Any]:
    return runs.start_run(
        family_id=_resolve(body.family),
        mode=body.mode,
        rule_pack=body.rule_pack,
        trigger_document_id=body.trigger_document_id,
    )


@app.get("/runs")
def get_runs(family: str | None = None) -> list[dict[str, Any]]:
    return runs.list_runs(_resolve(family) if family else None)


@app.get("/runs/{run_id}")
def run_status(run_id: str) -> dict[str, Any]:
    try:
        return runs.status(run_id)
    except runs.RunNotFound:
        raise HTTPException(404, f"no run {run_id}")


@app.get("/runs/{run_id}/cost")
def run_cost(run_id: str) -> dict[str, Any]:
    return runs.cost(run_id)


@app.get("/runs/{run_id}/approvals")
def run_approvals(run_id: str) -> dict[str, Any]:
    return approvals.pending_for_run(run_id)


@app.post("/approvals/{batch_id}/decide")
def decide(batch_id: str, body: DecideBody) -> dict[str, Any]:
    try:
        result = approvals.decide(
            batch_id, [d.model_dump() for d in body.decisions], decided_by=body.decided_by
        )
    except approvals.BatchNotFound:
        raise HTTPException(404, f"no approval batch {batch_id}")
    except approvals.UnknownItem as exc:
        raise HTTPException(400, str(exc))
    return result


@app.post("/runs/{run_id}/resume")
def resume(run_id: str) -> dict[str, Any]:
    """Continue a run: past the gate once every item is decided, or from the last
    checkpoint if the process was killed."""
    try:
        return runs.resume_run(run_id)
    except runs.RunNotFound:
        raise HTTPException(404, f"no run {run_id}")
    except runs.RunNotAwaitingApproval as exc:
        raise HTTPException(409, str(exc))


@app.get("/families/{slug}/register")
def register(slug: str, format: str = "json") -> Any:
    fam = _resolve(slug)
    if format == "markdown":
        return Response(deliverable.register(fam, "markdown"), media_type="text/markdown")
    return deliverable.register(fam, "json")


@app.get("/families/{slug}/findings")
def findings(slug: str, run_id: str | None = None) -> dict[str, Any]:
    return deliverable.findings(_resolve(slug), run_id)


@app.get("/families/{slug}/changelog")
def changelog(slug: str, since: str | None = None) -> dict[str, Any]:
    return deliverable.changelog(_resolve(slug), since)


@app.get("/families/{slug}/provenance/{term_key}")
def provenance(slug: str, term_key: str) -> dict[str, Any]:
    try:
        return deliverable.provenance(_resolve(slug), term_key)
    except LookupError:
        raise HTTPException(404, f"no term {term_key}")


def _resolve(slug: str) -> str:
    try:
        return families.resolve(slug)
    except families.FamilyNotFound:
        raise HTTPException(404, f"no family {slug!r}")
