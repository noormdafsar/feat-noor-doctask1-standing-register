from __future__ import annotations

import contextlib
import os
import signal
import time
from typing import Any, Callable, Iterator

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.db.session import session_scope
from app.graph import nodes
from app.graph.state import RunState
from app.llm.call import log_decision

_SETUP_DONE = False


def pg_dsn() -> str:
    """LangGraph's saver wants a raw libpq DSN, not SQLAlchemy's dialect URL."""
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://")


def _instrument(name: str, fn: Callable) -> Callable:
    """Wall-clock per node, plus the fault-injection hook.

    Timing lives here rather than inside each node so behaviour 10 reports where
    the time actually went even when no model is called at all -- segmentation,
    embedding and the database are real work and belong in the waterfall.

    CRASH_AFTER_NODE=<node> sends a real SIGKILL to our own pid the instant that
    node commits. Not a raised exception: an exception the process could catch and
    tidy up after would prove nothing about surviving being stopped. Deterministic
    timing also beats racing a kill from the parent. Exercised by
    tests/behaviour/test_kill_and_resume.py.
    """

    def inner(state):
        t0 = time.perf_counter()
        out = fn(state)
        elapsed_ms = int(round((time.perf_counter() - t0) * 1000))
        run_id = state.get("run_id")
        if run_id:
            try:
                with session_scope() as s:
                    log_decision(
                        s,
                        run_id=run_id,
                        node=name,
                        decision="node:complete",
                        detail={"elapsed_ms": elapsed_ms},
                        latency_ms=elapsed_ms,
                    )
            except Exception:  # noqa: BLE001
                # Instrumentation must never be the thing that fails a run.
                pass
        if os.getenv("CRASH_AFTER_NODE") == name:
            os.kill(os.getpid(), signal.SIGKILL)
        return out

    inner.__name__ = getattr(fn, "__name__", name)
    return inner


def build_graph(checkpointer):
    g = StateGraph(RunState)

    def add(name: str, fn: Callable) -> None:
        g.add_node(name, _instrument(name, fn))

    add("intake", nodes.intake)
    add("classify", nodes.classify)
    add("reclassify", nodes.reclassify)
    add("escalate_unknown", nodes.escalate_unknown)
    add("segment", nodes.segment_node)
    add("extract", nodes.extract)
    add("repair_extract", nodes.repair_extract)
    add("verify_spans", nodes.verify_spans)
    add("link", nodes.link)
    add("reconcile", nodes.reconcile)
    add("examine", nodes.examine_node)
    add("build_batch", nodes.build_batch)
    g.add_node("gate", nodes.gate)
    add("commit", nodes.commit)

    g.add_edge(START, "intake")
    g.add_edge("intake", "classify")

    # Three conditional edges, all routed on what a model actually decided.
    g.add_conditional_edges(
        "classify", nodes.route_after_classify, {"reclassify": "reclassify", "segment": "segment"}
    )
    g.add_conditional_edges(
        "reclassify",
        nodes.route_after_reclassify,
        {"escalate_unknown": "escalate_unknown", "segment": "segment"},
    )
    g.add_edge("escalate_unknown", "segment")
    g.add_edge("segment", "extract")
    g.add_conditional_edges(
        "extract",
        nodes.route_after_extract,
        {"repair_extract": "repair_extract", "verify_spans": "verify_spans"},
    )
    g.add_edge("repair_extract", "verify_spans")
    g.add_edge("verify_spans", "link")
    g.add_edge("link", "reconcile")
    g.add_edge("reconcile", "examine")
    g.add_edge("examine", "build_batch")
    g.add_edge("build_batch", "gate")
    g.add_edge("gate", "commit")
    g.add_edge("commit", END)

    return g.compile(checkpointer=checkpointer)


@contextlib.contextmanager
def compiled_graph() -> Iterator[Any]:
    global _SETUP_DONE
    with PostgresSaver.from_conn_string(pg_dsn()) as saver:
        if not _SETUP_DONE:
            _locked_setup(saver)
            _SETUP_DONE = True
        yield build_graph(saver)


def _locked_setup(saver) -> None:
    """The checkpointer's own DDL races between services exactly like ours does."""
    from app.db.session import schema_lock

    with schema_lock():
        saver.setup()


def setup_checkpointer() -> None:
    global _SETUP_DONE
    with PostgresSaver.from_conn_string(pg_dsn()) as saver:
        _locked_setup(saver)
        _SETUP_DONE = True
