from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from app.render.register import ProjectedRow


@dataclass
class CheckResult:
    passed: bool
    detail: str
    evidence: list[dict[str, Any]]
    unsupported: bool = False


Checker = Callable[[dict[str, ProjectedRow], dict[str, Any], list[dict]], CheckResult]

REGISTRY: dict[str, Checker] = {}


def register(name: str):
    def deco(fn: Checker) -> Checker:
        REGISTRY[name] = fn
        return fn

    return deco


def _num(raw: str | None) -> float | None:
    if raw is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(raw).replace(",", ""))
    return float(m.group(0)) if m else None


def _evidence(row: ProjectedRow | None) -> list[dict[str, Any]]:
    if row is None or not row.chain:
        return []
    link = row.chain[-1]
    return [
        {
            "document_id": link.document_id,
            "filename": link.filename,
            "block_id": link.block_id,
            "char_start": link.char_start,
            "char_end": link.char_end,
            "quote": link.quote,
        }
    ]


@register("term_required")
def term_required(rows, params, invoices) -> CheckResult:
    key = params["term_key"]
    row = rows.get(key)
    if row is None or row.status == "absent":
        return CheckResult(False, f"{key} is not stated anywhere in the sources.", [])
    return CheckResult(True, f"{key} is present.", _evidence(row))


@register("term_max")
def term_max(rows, params, invoices) -> CheckResult:
    key, limit = params["term_key"], float(params["max"])
    row = rows.get(key)
    if row is None or row.status == "absent":
        # Absent is not a pass and it is not a fail. Saying so is behaviour 5.
        return CheckResult(False, f"{key} is not stated, so the limit cannot be checked.", [], unsupported=True)
    val = _num(row.value.get("value"))
    if val is None:
        return CheckResult(False, f"{key} is not numeric: {row.value.get('value')!r}", _evidence(row), unsupported=True)
    if val <= limit:
        return CheckResult(True, f"{key} is {val:g}, within the limit of {limit:g}.", _evidence(row))
    return CheckResult(False, f"{key} is {val:g}, above the limit of {limit:g}.", _evidence(row))


@register("term_min")
def term_min(rows, params, invoices) -> CheckResult:
    key, floor = params["term_key"], float(params["min"])
    row = rows.get(key)
    if row is None or row.status == "absent":
        return CheckResult(False, f"{key} is not stated, so the floor cannot be checked.", [], unsupported=True)
    val = _num(row.value.get("value"))
    if val is None:
        return CheckResult(False, f"{key} is not numeric: {row.value.get('value')!r}", _evidence(row), unsupported=True)
    if val >= floor:
        return CheckResult(True, f"{key} is {val:g}, at or above the floor of {floor:g}.", _evidence(row))
    return CheckResult(False, f"{key} is {val:g}, below the floor of {floor:g}.", _evidence(row))


@register("term_in")
def term_in(rows, params, invoices) -> CheckResult:
    key = params["term_key"]
    allowed = [str(a).lower() for a in params["allowed"]]
    row = rows.get(key)
    if row is None or row.status == "absent":
        return CheckResult(False, f"{key} is not stated.", [], unsupported=True)
    val = str(row.value.get("value", "")).lower()
    if val in allowed:
        return CheckResult(True, f"{key} is {val!r}.", _evidence(row))
    return CheckResult(False, f"{key} is {val!r}, not one of {allowed}.", _evidence(row))


@register("not_contested")
def not_contested(rows, params, invoices) -> CheckResult:
    key = params["term_key"]
    row = rows.get(key)
    if row is None:
        return CheckResult(False, f"{key} is not stated.", [], unsupported=True)
    if row.status == "contested":
        return CheckResult(
            False,
            f"{key} has contradictory sources: {row.conflicts}",
            _evidence(row),
        )
    return CheckResult(True, f"{key} is settled.", _evidence(row))


@register("invoice_matches_contract")
def invoice_matches_contract(rows, params, invoices) -> CheckResult:
    """The check that anyone can verify by hand, which is the point of having it."""
    tolerance = float(params.get("tolerance", 0.01))
    price_row = rows.get("unit_price")
    if price_row is None or price_row.status == "absent":
        return CheckResult(False, "No contracted unit price in the sources.", [], unsupported=True)
    if not invoices:
        return CheckResult(True, "No invoices in this family to reconcile.", _evidence(price_row))

    problems: list[str] = []
    evidence: list[dict[str, Any]] = _evidence(price_row)
    for inv in invoices:
        service_date = inv.get("service_date")
        contracted = _price_as_of(price_row, service_date)
        billed = _num(inv.get("unit_price"))
        if contracted is None or billed is None:
            continue
        if abs(billed - contracted) > tolerance:
            problems.append(
                f"{inv.get('filename')}: billed {billed:g} but the contract as amended "
                f"on {service_date} says {contracted:g}"
            )
            evidence.append(
                {
                    "document_id": inv.get("document_id"),
                    "filename": inv.get("filename"),
                    "block_id": inv.get("block_id"),
                    "char_start": inv.get("char_start", 0),
                    "char_end": inv.get("char_end", 0),
                    "quote": inv.get("quote", ""),
                }
            )
    if problems:
        return CheckResult(False, " ; ".join(problems), evidence)
    return CheckResult(True, f"All {len(invoices)} invoice lines match the contracted rate.", evidence)


def _price_as_of(row: ProjectedRow, service_date: str | None) -> float | None:
    if not service_date:
        return _num(row.value.get("value"))
    try:
        target = date.fromisoformat(service_date)
    except ValueError:
        return _num(row.value.get("value"))
    applicable = [
        link
        for link in row.chain
        if link.effective_from is None or date.fromisoformat(link.effective_from) <= target
    ]
    if not applicable:
        return None
    return _num(applicable[-1].value)
