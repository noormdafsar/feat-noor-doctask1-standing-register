# Standing Register

An agentic system that owns a pile of vendor contracts, their amendments and their
invoices. It builds one grounded register from them, checks it against rule packs,
and keeps it current as new paper arrives. Every commit passes a human gate. Every
claim points at a character range in a source document.

Built for the SuperDocs Round 2 engineering task (Task 1).

## Demo

[![Standing Register — SuperDocs Round 2 demo](https://img.youtube.com/vi/NcErPhDwxmM/hqdefault.jpg)](https://youtu.be/NcErPhDwxmM)

[Watch on YouTube](https://youtu.be/NcErPhDwxmM) · [copy on Google Drive](https://drive.google.com/file/d/1XcNVpy6q7yH-pw69fVymxHaiGqDk7Nnm/view?usp=sharing)


---

## Run it

```bash
git clone <this repo> && cd doctask-standing-register
make up
```

That is the whole thing. It brings up PostgreSQL with pgvector, the API, the
watcher and the review interface, creates the schema, and seeds the synthetic
corpus. No API key is needed: the default model backend replays deterministic
fixtures.

- Review interface: <http://localhost:3000>
- API docs: <http://localhost:8000/docs>

Then, in a second terminal:

```bash
make demo    # one family end to end, printed as it goes
make test    # the behaviour suite, with GEMINI_API_KEY deliberately unset
```

To run against a live model instead, put a Gemini key in `.env` and set
`MODEL_BACKEND=gemini`.

### A note on the model

The runtime model is **Gemini** (`2.5-flash` for classification and extraction,
`2.5-pro` for retries and adjudication). A Claude Code / Claude Max subscription
is a *building* tool and exposes no programmatic key, so it cannot be the model
inside the system — it is what wrote the system. Worth saying plainly, since the
tooling and the runtime differ on purpose.

---

## What it does

A **contract family** is one master agreement plus everything downstream: amendments,
order forms, SOWs, and the invoices billed against it. For each family the system
produces an **Obligation and Commercial Register** — one row per tracked term, carrying:

- the **current value** after the amendment chain is applied in effective-date order,
- the **chain** that produced it, each link citing an exact character range,
- a **status**: `settled`, `contested`, `blocked`, or `absent`. `absent` renders as
  *not stated in sources*. There is no fifth option and no inferred value.

Alongside it: invoice reconciliation against the contracted rate in force on each
service date, rule findings with mandatory evidence, and a changelog that answers
what changed, when, and because of which source.

### Why this domain

Vendor contracts generate *genuine* disagreement rather than manufactured
disagreement, which matters because three of the graded behaviours are about
conflict. Amendment 2 overrides what Amendment 1 already overrode. An invoice
either matches the contracted rate as amended on its service date or it does not —
a claim anyone can check by hand. And new paper arrives constantly, touching two
rows out of fourteen.

The shipped corpus is deliberately non-compliant in ways you can verify yourself:

| What | Where |
| --- | --- |
| Payment terms drift 30 → 45 → 60 days across two amendments | breaches the 45-day rule |
| Renewal notice cut 60 → 30 days | breaches the 60-day floor |
| An amendment and a renewal order form both effective 2025-01-01 with different prices | a real contested term |
| Invoice 2042 bills £52.00 when the contract as amended says £48.50 | a real reconciliation failure |
| A fixture claims a governing law of Singapore, citing text that does not exist | dropped, and reported as a gap |

`northwind-v1` is the control: a clean family that yields an honest empty report.

---

## The central design decision

**The deliverable is a projection of a fact store, not a document a model wrote.**

Models only ever fill a typed schema with spans attached. A deterministic renderer
(`app/render/register.py`) turns facts into the register. No language model writes
a line of the published output.

That one decision does most of the work:

- **Updates cost like updates.** A new amendment invalidates only the facts it
  touches. Documents already extracted are never re-read
  (`extract:already-settled` in the step log). Rows whose facts did not change keep
  their content hash and carry forward.
- **Non-modification is provable.** `register_row.content_hash` before and after.
  A diff, not a claim. The hash covers value plus provenance chain, and
  deliberately excludes surrogate keys — see the comment on `ChainLink.hash_payload`,
  which is there because getting this wrong once made every row look changed on
  every run.
- **It cannot bluff.** A schema field is filled from a verified span or it is null.
  There is no path by which a model's fluency becomes a register value.
- **Configuration over code.** A new tracked term is a schema entry; a new rule is
  a YAML file. Neither touches the graph.

The cost is prose quality: a projected register reads like a register, not like an
analyst's memo. That is the right trade for a brief that grades provenance and
precision.

---

## The ten behaviours, and where each one lives

| # | Behaviour | Mechanism | Proof |
| --- | --- | --- | --- |
| 1 | Visible steps, decisions that change the path | `step` row per node with decision, confidence, tokens, ms. Three conditional edges route on model output: classification confidence, extraction emptiness, link ambiguity | `test_escalation.py`, `test_full_run_and_gate.py` |
| 2 | Survives being stopped | `PostgresSaver` checkpointer + `node_result` and `model_call` idempotency caches | `test_kill_and_resume.py` — real SIGKILL |
| 3 | A human holds the gate | `approval_batch` / `approval_item`, LangGraph `interrupt()`, per-item accept **and** reject in one call | `test_reject_one_item.py` |
| 4 | A machine can drive it | MCP server, 11 tools, over the same service layer as the UI | `test_mcp_surface.py` — full flow, gate included |
| 5 | It never bluffs | `verify_spans` re-matches every quote against the source; unverifiable → deleted + gap recorded | `test_span_verification.py` |
| 6 | A stranger can run it | `make up`, one command, schema and corpus seeded on boot | — |
| 7 | It proves itself | Fixture provider + local hashing embedder; suite green with no key | `make test` |
| 8 | No orders from documents | Source text only inside `<source>` tags under a standing rule, plus a deterministic scanner that files attempts as findings | `test_injection.py` — verdicts compared against a control run |
| 9 | Two runs stay two runs | Transaction-scoped advisory lock per family around extract, verify, reconcile and commit | `test_concurrency.py` — concurrent result compared against serial |
| 10 | It knows what it cost | Per-node wall clock and per-call tokens priced into `usd_estimate`; `GET /runs/{id}/cost` | `test_cost_and_scope.py` |

### The machine face

Eleven MCP tools over the same service layer as the interface: `ingest_documents`,
`start_run`, `get_run_status`, `list_pending_approvals`, `decide_approval`,
`resume_run`, `get_deliverable`, `get_provenance`, `explain_change`,
`get_findings`, `get_run_cost`.

Attach a coding agent to it on stdio:

```json
{
  "mcpServers": {
    "standing-register": {
      "command": "docker",
      "args": ["compose", "-f", "/abs/path/docker-compose.yml",
               "run", "--rm", "-T", "api",
               "python", "-m", "app.mcp_server.server"]
    }
  }
}
```

`test_mcp_surface.py` drives a complete flow through `mcp.call_tool` — ingest,
run, review, reject one item, resume, export, provenance, changelog — so the claim
that a program can drive this without touching the interface is tested rather than
asserted.

### How the human gate actually behaves

- Nothing publishes before approval. A run that has not been through the gate has
  an empty register.
- `resume` **refuses** while any item is undecided, naming how many. There is no
  auto-approve flag on any surface, and `decide_all` exists only in the CLI for the
  demo.
- Rejecting one item has no effect on any other. A rejected row update never
  reaches the published register; a rejected finding is recorded as rejected with
  its reason and who gave it.

### How escalation behaves

An escalation blocks the **affected row**, not the run. A document that will not
classify above the confidence floor is retried at the deeper tier, then marked
blocked, and the run produces everything else. If escalation halted the run, one
ambiguous amendment would stop fourteen rows and the concurrency behaviour would
be unobservable.

---

## Architecture

```
watched/ or API upload
        │
        ▼
    intake ─ hash, dedupe by content
        │
    classify ──low confidence──▶ reclassify ──still low──▶ escalate (row blocked)
        │                                                        │
    segment ─ blocks with char offsets, chunks, embeddings ◀──────┘
        │
    extract ──empty──▶ repair_extract (deeper tier, max 2)
        │
    verify_spans ─ quote must appear verbatim, or the fact is deleted
        │
    link ─ amendment → parent agreement; ambiguity escalates
        │
    reconcile ─ project register, detect conflicts, measure what changed   [lock]
        │
    examine ─ deterministic → grounded → adjudication
        │
    build_batch ─▶ gate  ══ interrupt ══▶  human decides item by item
                            │
                          commit ─ apply exactly what was approved            [lock]
```

Everything is a thin adapter over `app/domain/`: the REST API, the MCP server and
the React interface call the same functions. Behaviour 4 is a consequence of that,
not a feature bolted on at the end.

### Layout

```
app/
  graph/      nodes, conditional edges, checkpointing, idempotency
  domain/     services — the one implementation all three surfaces call
  extract/    typed schemas the model fills
  render/     fact store → register (deterministic, no model)
  retrieval/  hybrid search, RRF fusion, family scoping
  rules/      pack loader, deterministic checks, the three examine passes
  llm/        provider interface, gemini, fixture, cassette, the call wrapper
  security/   prompt-injection scanner and source wrapping
  api/ mcp_server/ watch/   the three faces and the watcher
corpus/       synthetic families; _arrivals, _injected and _edge are held back
rules/        YAML rule packs
tests/        behaviour suite + fixtures
web/          React review interface
```

### Retrieval

Hybrid: pgvector cosine over 384-dimension chunk embeddings, fused by reciprocal
rank with a Postgres full-text stage. Two deliberate choices:

- **OR semantics on the lexical side.** `websearch_to_tsquery` ANDs its terms, so
  one absent word returns nothing — the opposite of what a recall stage should do.
- **A dependency-free hashing embedder by default.** It keeps the image small, the
  suite keyless and every run reproducible, at the cost of semantic recall. Hybrid
  search leans on full-text for precision, which is the right bias for contract
  work where clause numbers, defined terms and exact amounts matter more than
  paraphrase. Set `EMBED_BACKEND=gemini` for real embeddings when a key is present;
  the code falls back to hashing if that call fails, rather than failing the run.

Every query is scoped by `family_id`, enforced in the repository layer and tested
directly.

---

## Declared scope

Say what you accept, then behave that way.

- **Formats accepted:** `.md`, `.txt`, `.csv`, `.eml`, `.pdf` (text layer), `.docx`.
  Anything else is rejected at intake with a named reason. A scanned PDF with no
  text layer is refused explicitly rather than ingested as an empty document — OCR
  is out of scope and absent-and-declared beats present-and-unreliable.
- **Document types recognised:** master agreement, amendment, order form, SOW,
  invoice, credit note. Anything else classifies as `unknown` and escalates.
- **Tracked terms:** the fourteen in `app/extract/schemas.py::TERM_KEYS`.
- **A second run means different documents inside that set.** `northwind-v1` is
  exactly that: a different family, different parties, different numbers, same
  declared formats, and it runs clean.

---

## Decisions and assumptions

Logged as they were made. `PROGRESS.md` carries the running version.

1. **Register, not brief or report.** The brief allows any of the three. A register
   is the one whose rows have stable identity, which is what makes byte-identity
   provable at row granularity.
2. **Schema migrations are `create_all`, not Alembic.** Single-version schema, no
   production upgrade path in scope. A deliberate simplification, stated rather
   than hidden.
3. **The fixture provider is keyed on `(task, document filename)`, not on a prompt
   hash.** Prompt-hash cassettes exist too (`MODEL_BACKEND=cassette`, recorded by
   `make record`), but they go stale the moment a prompt is edited, which turns
   behaviour tests into prompt-diff tests. Two fixtures are wrong on purpose so
   span verification and the escalation path have something real to catch.
4. **Fault injection over kill-racing.** `CRASH_AFTER_NODE=<node>` sends a real
   SIGKILL to the process's own pid the instant that node commits. Deterministic,
   and still uncatchable — an exception the process could tidy up after would
   prove nothing.
5. **Documents are immutable.** Blocks are computed once; a document that already
   has verified facts is never re-extracted. This is what makes an incremental run
   cheap, and it is why re-dropping the same bytes is a no-op.
6. **Conflicts are never auto-resolved.** Two sources of equal authority with
   different values produce a `contested` row and a conflict record. The system
   picks the later document by precedence for the *displayed* current value, and
   says loudly that it is contested.
7. **Costs are estimates.** Token counts are real; prices come from a table in
   `app/config.py`. Behaviour 10 asks the system to know what it cost, not to be a
   billing system.

---

## What I cut, and why

The brief invites cuts among behaviours six to ten, defended rather than hidden.

- **The cost waterfall as a chart.** The API returns per-stage spend and time and
  the interface tabulates it; there is no rendered chart. Behaviour 10 asks the
  system to *know* what it cost.
- **DOCX export of the register.** JSON and Markdown only. The fidelity of a
  generated DOCX is not what is being graded here, and a half-good one is worse
  than an honest absence.
- **OCR for scanned PDFs.** Declared out of scope and refused explicitly at intake.
- **Alembic migration history.** See decision 2.

Not cut, and not cuttable: behaviours 1–5, kill-and-resume, and the keyless suite.

---

## Where it fails

Breaking your own build is a credibility move, so:

- **The hashing embedder is weak at paraphrase.** A rule phrased in words that share
  no stems with the clause it targets leans entirely on dense retrieval, which is
  the weaker half here. On a large corpus this would need real embeddings; on this
  one, full-text carries it.
- **Amendment→parent linking is heuristic.** Reference-string match, then a
  preference for the master agreement when several candidates remain. It escalates
  rather than guessing when that is not decisive, but a corpus with two master
  agreements sharing a reference prefix would escalate every amendment.
- **`link` and `reconcile` do not use the model at all.** Amendment ordering is
  effective-date plus document-type precedence. A "notwithstanding clause 4.2"
  cross-reference that reorders precedence would not be understood.
- **Concurrency is coarse.** One advisory lock per family, held across extract,
  verify, reconcile and commit. Correct, and it serialises more than it strictly
  must. Row-level locking would be finer and was not worth the complexity here.
- **The grounded stage judges one rule against up to six retrieved chunks.** A rule
  whose answer is spread across a dozen clauses will be judged on a partial view,
  and will report low confidence and escalate rather than guess — but it escalates
  more often than a system with a smarter retrieval budget would.
- **The register tracks fourteen terms.** Adding the fifteenth is a data change,
  but nothing automatically discovers a term nobody listed.

---

## Working conventions

`TASK.md` says how to work in this repository. `PROGRESS.md` is a dated assumption
log. Both exist because the brief suggests them and because they turned out to be
the fastest way to keep a long build honest.

Secrets stay out of the code. `.env` is gitignored; `.env.example` documents every
variable. No key is written to a log, a commit, or a shell history.
