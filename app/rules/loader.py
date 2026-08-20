from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.config import settings


@dataclass
class Rule:
    id: str
    title: str
    severity: str
    stage: str  # deterministic | grounded
    check: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    question: str = ""
    retrieval_query: str = ""


@dataclass
class RulePack:
    name: str
    version: int
    sha: str
    rules: list[Rule]


class RulePackError(ValueError):
    pass


def load_pack(name: str) -> RulePack:
    """A new obligation is a data change, not a rewrite.

    Adding a rule means adding a YAML entry; adding a *kind* of rule means adding
    one function to app/rules/deterministic.py. Neither touches the graph.
    """
    path = Path(settings.rules_dir) / f"{name}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in Path(settings.rules_dir).glob("*.yaml"))
        raise RulePackError(
            f"rule pack {name!r} not found in {settings.rules_dir}. Available: {available}"
        )
    raw = path.read_bytes()
    doc = yaml.safe_load(raw.decode("utf-8"))
    rules = []
    for r in doc.get("rules", []):
        stage = r.get("stage", "deterministic")
        if stage not in ("deterministic", "grounded"):
            raise RulePackError(f"{name}/{r.get('id')}: unknown stage {stage!r}")
        rules.append(
            Rule(
                id=r["id"],
                title=r["title"],
                severity=r.get("severity", "medium"),
                stage=stage,
                check=r.get("check", ""),
                params=r.get("params", {}) or {},
                question=r.get("question", ""),
                retrieval_query=r.get("retrieval_query", "") or r.get("question", ""),
            )
        )
    if not rules:
        raise RulePackError(f"rule pack {name!r} contains no rules")
    return RulePack(
        name=doc.get("pack", name),
        version=int(doc.get("version", 1)),
        sha=hashlib.sha256(raw).hexdigest()[:12],
        rules=rules,
    )


def list_packs() -> list[str]:
    return sorted(p.stem for p in Path(settings.rules_dir).glob("*.yaml"))
