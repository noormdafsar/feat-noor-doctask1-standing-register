"""Put the system into a state worth looking at.

`make up` leaves an empty register, because nothing publishes before a human
approves it -- which is correct, and makes for a dull first look. This script
walks the full lifecycle so every screen in the review interface has real content:

  1. a completed run on acme-v1      -> Register, Findings, Runs, Changelog
  2. a new amendment dropped in      -> Review, parked at the gate with one item

It uses only the public REST API, so it is also a worked example of driving the
system from outside.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost:8000"
ARRIVAL = Path("/srv/corpus/_arrivals/acme-v1/08-amendment-3.md")
WATCHED = Path("/srv/watched/acme-v1")


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=300)

    for _ in range(60):
        try:
            c.get("/health").raise_for_status()
            break
        except Exception:
            time.sleep(2)
    else:
        print("API never came up", file=sys.stderr)
        return 1

    print("1/4  building the register for acme-v1 ...")
    run = c.post("/runs", json={"family": "acme-v1"}).json()
    rid = run["run_id"]
    print(f"     run {rid} -> {run['status']}")

    batch = c.get(f"/runs/{rid}/approvals").json()
    print(f"2/4  approving {len(batch['items'])} gate items "
          f"(rejecting one, so the interface shows a real rejection) ...")
    reject = next(
        (i for i in batch["items"] if i["kind"] == "row_update" and i["ref_id"] == "sla_credit_pct"),
        None,
    )
    decisions = [
        {
            "item_id": i["item_id"],
            "decision": "reject" if reject and i["item_id"] == reject["item_id"] else "accept",
            "reason": "not tracked by this team" if reject and i["item_id"] == reject["item_id"] else None,
        }
        for i in batch["items"]
    ]
    c.post(f"/approvals/{batch['batch_id']}/decide", json={"decisions": decisions}).raise_for_status()
    final = c.post(f"/runs/{rid}/resume").json()
    print(f"     run -> {final['status']}")

    reg = c.get("/families/acme-v1/register").json()
    print(f"     {len(reg['rows'])} rows published, "
          f"{len(reg['unsupported_terms'])} term(s) not stated in sources")

    print("3/4  dropping amendment 3 into the watched folder ...")
    WATCHED.mkdir(parents=True, exist_ok=True)
    target = WATCHED / ARRIVAL.name
    if not target.exists():
        target.write_bytes(ARRIVAL.read_bytes())
        print("     waiting for the watcher to pick it up ...")
        time.sleep(10)
    else:
        print("     already there; skipping")

    runs = c.get("/runs?family=acme-v1").json()
    pending = [r for r in runs if r["status"] == "awaiting_approval"]
    print("4/4  done.\n")
    if pending:
        s = pending[0]["stats"]
        print(f"     A second run is parked at the gate: "
              f"{s.get('rows_changed')} row changed, "
              f"{s.get('rows_byte_identical')} byte-identical.")
        print("     Open the Review tab to approve or reject it yourself.\n")

    print("     Review interface : http://localhost:3000")
    print("     API docs         : http://localhost:8000/docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
