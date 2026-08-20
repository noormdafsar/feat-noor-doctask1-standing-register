from __future__ import annotations

import time
from pathlib import Path

from app.config import settings
from app.db.session import init_db
from app.domain import families, runs

STAMP = ".ingested"


def watch_loop(interval: float = 3.0) -> None:
    """New paper lands in watched/<family-slug>/ and produces a focused update.

    An arrival triggers an incremental run, not a rebuild: the reconcile stage
    reports how many register rows actually changed and how many carried forward
    byte-identical, and the update is gated exactly like a full run.
    """
    init_db()
    root = Path(settings.watch_dir)
    root.mkdir(parents=True, exist_ok=True)
    print(f"watching {root} (every {interval}s)", flush=True)

    while True:
        try:
            _sweep(root)
        except Exception as exc:  # noqa: BLE001
            # A bad file must not kill the watcher; it names the cause and continues.
            print(f"watch error: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(interval)


def _sweep(root: Path) -> None:
    for family_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        done = family_dir / STAMP
        seen = set(done.read_text().splitlines()) if done.exists() else set()
        arrivals = [
            p
            for p in sorted(family_dir.iterdir())
            if p.is_file() and p.name != STAMP and p.name not in seen
        ]
        if not arrivals:
            continue

        fam = families.ensure_family(family_dir.name, family_dir.name.replace("-", " ").title())
        for path in arrivals:
            result = families.ingest_bytes(fam, path.name, path.read_bytes())
            seen.add(path.name)
            print(f"[watch] {path.name}: {result['status']}", flush=True)
            if result["status"] != "accepted":
                continue
            out = runs.start_run(
                family_id=fam,
                mode="incremental",
                rule_pack="procurement-baseline",
                trigger_document_id=result["document_id"],
            )
            stats = out.get("stats", {})
            print(
                f"[watch] run {out['run_id']} -> {out['status']}: "
                f"{stats.get('rows_changed', '?')} row(s) changed, "
                f"{stats.get('rows_byte_identical', '?')} byte-identical, "
                f"awaiting approval",
                flush=True,
            )
        done.write_text("\n".join(sorted(seen)))
