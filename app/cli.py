from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from app.config import settings
from app.db.session import init_db
from app.domain import approvals, deliverable, families, runs
from app.graph.build import setup_checkpointer


def cmd_init_db(_args) -> int:
    init_db()
    setup_checkpointer()
    print("schema ready")
    return 0


def cmd_seed(_args) -> int:
    """Load the demo corpus. Idempotent: re-running ingests nothing new."""
    root = Path(settings.corpus_dir)
    total = 0
    # Directories prefixed with _ are held back on purpose: _arrivals feeds the
    # watcher, _injected and _edge are only pulled in by the behaviour suite.
    for family_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if family_dir.name.startswith("_"):
            continue
        fam = families.ensure_family(family_dir.name, family_dir.name.replace("-", " ").title())
        files = sorted(p for p in family_dir.iterdir() if p.is_file())
        results = families.ingest_paths(fam, files)
        accepted = sum(1 for r in results if r["status"] == "accepted")
        total += accepted
        print(f"{family_dir.name}: {accepted} accepted, {len(results) - accepted} skipped")
    print(f"seeded {total} document(s)")
    return 0


def cmd_run(args) -> int:
    fam = families.resolve(args.family)
    out = runs.start_run(family_id=fam, mode=args.mode, rule_pack=args.rule_pack)
    print(json.dumps(out, indent=2))
    return 0


def cmd_demo(_args) -> int:
    """Clone-to-working proof: one family, end to end, printed as it goes."""
    fam_slug = "acme-v1"
    fam = families.resolve(fam_slug)

    print("=" * 72)
    print("STANDING REGISTER - end to end on", fam_slug)
    print("=" * 72)

    out = runs.start_run(family_id=fam, mode="full", rule_pack="procurement-baseline")
    run_id = out["run_id"]
    print(f"\nrun {run_id} -> {out['status']}")
    print("\n-- stage decisions " + "-" * 52)
    for st in out["steps"]:
        conf = f" conf={st['confidence']:.2f}" if st["confidence"] is not None else ""
        print(f"  {st['seq']:>3}. {st['node']:<16} {st['decision']}{conf}")

    batch = approvals.pending_for_run(run_id)
    print(f"\n-- human gate: {len(batch['items'])} item(s) awaiting a decision " + "-" * 20)
    for i in batch["items"][:12]:
        print(f"  [{i['kind']:<10}] {i['title'][:78]}")
    if len(batch["items"]) > 12:
        print(f"  ... and {len(batch['items']) - 12} more")

    # A demo has to make the call explicitly too -- there is no auto-approve path.
    print("\n  (demo accepts every item; the interface and the API decide item by item)")
    approvals.decide_all(batch["batch_id"], "accept", reason="accepted in `make demo`")
    runs.resume_run(run_id)

    print("\n-- register " + "-" * 60)
    print(deliverable.register(fam, "markdown"))

    f = deliverable.findings(fam, run_id)
    print(f"-- findings: {f['message']} " + "-" * 40)
    for item in f["findings"][:10]:
        print(f"  [{item['severity']:<6}] {item['rule_id']:<8} {item['detail'][:90]}")

    c = runs.cost(run_id)
    print("\n-- cost and time " + "-" * 55)
    for stage in c["by_stage"]:
        print(
            f"  {stage['node']:<16} {stage['steps']:>3} steps  "
            f"{stage['tokens_in']:>7} in  {stage['tokens_out']:>7} out  "
            f"${stage['usd']:.6f}  {stage['ms']:>6} ms"
        )
    t = c["total"]
    print(f"  {'TOTAL':<16} {'':>3}        {t['tokens_in']:>7} in  {t['tokens_out']:>7} out  "
          f"${t['usd']:.6f}  {t['ms']:>6} ms")
    print(f"\n  {c['note']}")
    print("\nReview interface: http://localhost:3000   API: http://localhost:8000/docs")
    return 0


def cmd_watch(args) -> int:
    """Behaviour 3: new documents keep arriving, each producing a focused update."""
    from app.watch.watcher import watch_loop

    watch_loop(interval=args.interval)
    return 0


def cmd_record(_args) -> int:
    if settings.model_backend != "record":
        print("set MODEL_BACKEND=record and GEMINI_API_KEY first", file=sys.stderr)
        return 2
    init_db()
    setup_checkpointer()
    cmd_seed(None)
    fam = families.resolve("acme-v1")
    runs.start_run(family_id=fam, mode="full", rule_pack="procurement-baseline")
    print(f"cassettes written to {settings.cassette_dir}")
    return 0


def cmd_wait_db(args) -> int:
    from sqlalchemy import text

    from app.db.session import engine

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            print("database ready")
            return 0
        except Exception:  # noqa: BLE001
            time.sleep(1)
    print("database never became ready", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="standing-register")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db").set_defaults(fn=cmd_init_db)
    sub.add_parser("seed").set_defaults(fn=cmd_seed)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    sub.add_parser("record").set_defaults(fn=cmd_record)

    r = sub.add_parser("run")
    r.add_argument("family")
    r.add_argument("--mode", default="full", choices=["full", "incremental"])
    r.add_argument("--rule-pack", dest="rule_pack", default="procurement-baseline")
    r.set_defaults(fn=cmd_run)

    w = sub.add_parser("watch")
    w.add_argument("--interval", type=float, default=3.0)
    w.set_defaults(fn=cmd_watch)

    d = sub.add_parser("wait-db")
    d.add_argument("--timeout", type=float, default=60)
    d.set_defaults(fn=cmd_wait_db)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
