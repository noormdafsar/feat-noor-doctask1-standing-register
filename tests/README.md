# The behaviour suite

Run it with `make test`. It runs with `GEMINI_API_KEY` deliberately unset.

## Where the line is drawn

Every test in `behaviour/` exercises the real LangGraph state machine, the real
Postgres schema, real transactions, real advisory locks and real process kills.
The **only** thing substituted is the model provider, which replays canned JSON
from `fixtures/model_outputs.json`.

That line is deliberate. A test that asserts "the model returned what the mock was
told to return" proves nothing. These tests assert what the *system* does with
model output:

| File | Claim under test |
| --- | --- |
| `test_full_run_and_gate.py` | The run reaches the gate, and nothing publishes before a human decides. |
| `test_kill_and_resume.py` | SIGKILL mid-run, restart, finish -- with no model call paid for twice. |
| `test_concurrency.py` | Two runs on one family produce the same register as running them one after the other. |
| `test_reject_one_item.py` | Rejecting one review item leaves the other decisions intact. |
| `test_span_verification.py` | A fact whose quote is not in the source is dropped, not published. |
| `test_injection.py` | A document giving orders is reported, and changes no verdict. |
| `test_incremental_update.py` | One new amendment changes the rows it touches and no others, byte for byte. |
| `test_clean_corpus.py` | A corpus with nothing wrong in it yields an honest empty report. |
| `test_escalation.py` | An unclassifiable document is retried, then escalated, without stopping the run. |
| `test_cost_and_scope.py` | The cost rollup is arithmetic, and retrieval cannot cross families. |
| `test_mcp_surface.py` | A program can drive the whole flow, gate included, through MCP alone. |

Two fixtures are wrong on purpose, so span verification and the escalation path
have something real to catch. `fixtures/model_outputs.json` says which and why.
