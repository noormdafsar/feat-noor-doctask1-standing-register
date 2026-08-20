# Progress and assumption log

Dated, append-only. Assumptions logged as they were made rather than reconstructed
afterwards.

---

## 2026-08-17 — M1: skeleton, resume, concurrency

Built the parts that cannot be retrofitted first: the Postgres checkpointer, the
`node_result` and `model_call` idempotency caches, the fixture provider, and the
fault-injection hook — before there was any real work to lose.

- **Assumption:** the graded phrase "kill the process in the middle of a run" is
  satisfied by a real `SIGKILL`, and deterministic timing is worth more than a race
  between the parent's `kill` and the child's progress. Implemented as
  `CRASH_AFTER_NODE`, which sends `SIGKILL` to the process's own pid. Uncatchable
  either way.
- **Assumption:** "no finished work is lost" also implies no finished work is *paid
  for twice*. The resume test asserts the model-call count for pre-kill tasks is
  identical before and after resume, not merely that the run completes.
- **Decision:** schema via `create_all` plus explicit SQL for the pgvector extension
  and the two indexes. Alembic buys a production upgrade path this project does not
  have.

## 2026-08-17 — M2: understand

Intake → classify → segment → extract → verify spans → link → reconcile → project.

- **Assumption:** documents are immutable once ingested, keyed by content hash.
  Everything downstream leans on this: blocks are computed once, and a document
  with verified facts is never re-extracted.
- **Assumption:** where two sources of equal authority disagree, the *displayed*
  current value follows document-type precedence (amendment beats order form beats
  master agreement at the same effective date) and the row is marked `contested`.
  Choosing silently would be the failure the brief names; refusing to display
  anything would make the register useless. Displaying with a loud flag is the
  defensible middle.
- **Bug found and fixed:** `_extract_terms` re-queried the just-inserted fact by
  `ORDER BY id DESC`, but ids are random hex — spans attached to the wrong facts.
  Caught by `MultipleResultsFound` in `verify_spans`, which is the sort of failure
  that would have been invisible without a strict one-span-per-fact expectation.

## 2026-08-17 — M3: the gate and the machine face

Approval batches, `interrupt()`, resume, REST, MCP, React.

- **Decision:** `resume` refuses while any item is undecided, and names the count.
  A partially-decided batch committing "the decided ones" would be a system making
  a decision nobody made.
- **Decision:** no auto-approve anywhere on the API or MCP surfaces. `decide_all`
  exists only in the CLI, for `make demo`, and the demo says out loud that it is
  making the call.
- **Assumption:** escalations block the affected register row, not the run. Halting
  the run on one ambiguous amendment would make the remaining thirteen rows
  unobtainable and would make behaviour 9 unobservable.

## 2026-08-17 — M4: examine

Rule packs, three staged passes, injection scanner.

- **Decision:** a rule finding without evidence cannot be constructed — `_finding()`
  raises. Structural, so no future rule can quietly produce an uncited claim.
- **Decision:** a grounded verdict of `fail` whose quote does not verify against a
  retrieved document is downgraded to `unsupported`, not published. This is the
  line that stops a fluent model inventing a compliance failure.
- **Assumption:** the honest empty report needs a corpus that genuinely satisfies
  the pack, not a special case. `northwind-v1` is that corpus, and
  `test_the_same_corpus_under_a_stricter_playbook_does_find_something` proves the
  empty result is a property of the rules rather than a system incapable of
  reporting.

## 2026-08-17 — M5: stay alive

Watcher, incremental updates, changelog, cost.

- **Bug found and fixed (important one):** `content_hash` included `fact_id`, which
  is regenerated every run. Every row therefore looked changed on every run, which
  would have made the central claim of this system false while all the surrounding
  machinery looked fine. Now hashed over stable content only — filename, doc type,
  value, effective date, quote, offsets. Caught by the concurrency test comparing
  concurrent output against serial output.
- **Bug found and fixed:** extraction deleted only the current run's facts, so a
  second run stacked a second copy of every fact into the chain and changed every
  row. Facts are now cleared per document, and a document with verified facts is
  skipped entirely — which is also what makes an update cost like an update.
- **Bug found and fixed:** every amendment escalated as ambiguous, because order
  forms quote the master agreement's reference too. Now prefers the master
  agreement when the reference match leaves several candidates.
- **Change:** per-node wall clock moved into the graph wrapper rather than living
  inside model calls, so the cost breakdown reports where time went even when a
  stage calls no model at all. Segmentation and embedding are real work.

## 2026-08-17 — M5b: what the full stack found that the tests did not

The suite was green well before `docker compose up` was. Three failures only
appeared once four services started against one database at the same moment,
which is a useful reminder that a green suite is evidence, not proof.

- **Bug found and fixed (deadlock):** api and watcher both run schema setup on
  boot. Guarding it with `pg_advisory_xact_lock` produced a genuine deadlock,
  because LangGraph's `PostgresSaver.setup()` runs `CREATE INDEX CONCURRENTLY`,
  which waits for every concurrent transaction to finish — including the one
  holding the lock while waiting for that setup to return. The whole stack hung
  with no error. Now a session-scoped `pg_advisory_lock` on an AUTOCOMMIT
  connection, so no transaction is ever open across the DDL.
- **Bug found and fixed:** the build context included `web/node_modules`, created
  inside the bind mount by the web container. Docker choked on a dangling symlink
  in `.bin/`. Added `.dockerignore`.
- **Change:** the behaviour suite now runs against its own database
  (`standing_register_test`). Truncating between tests while the watcher polls the
  live database every three seconds meant tests and the watcher contended for
  `ACCESS EXCLUSIVE` locks. Isolating the test database is the honest fix; making
  the tests tolerate the contention would have been hiding it.

Verified on a clean volume: `make up` → seeded → REST run → 20 gate items → one
rejected → 13 rows published, the rejected term absent → amendment 3 dropped into
`watched/` → **1 row changed, 13 byte-identical**, reported by the system itself.

## Open, and honestly unfinished

- Amendment linking is heuristic and would escalate constantly on a corpus with
  several master agreements sharing a reference prefix.
- Cross-reference reasoning ("notwithstanding clause 4.2") is not attempted;
  ordering is effective date plus document-type precedence only.
- The lock is per family and held across four nodes. Correct but coarse.
- No DOCX export of the register. See README, "What I cut".

## 2026-08-17 — M6: what a live API key found that fixtures could not

The fixture suite was green throughout everything below. None of it was visible
until the system talked to the real Gemini API.

- **Bug: pinned model names had already retired.** `gemini-2.5-flash` returns 404
  "no longer available to new users" and `text-embedding-004` returns 404 outright.
  Now `gemini-flash-latest` / `gemini-pro-latest` / `gemini-embedding-001` —
  aliases rather than pins, because a pinned model retires and the repo stops
  working for whoever clones it next.
- **Bug: error handling named nothing.** A failed call raised
  `gemini call failed after retries: retryable`, which hid an HTTP 503 behind a
  word. It now reports status, model, the API's own message, and the command to
  list what a key can actually reach. The brief grades "error handling that names
  the cause and the fix"; the old message failed that on both counts.
- **Gap: degradation only went deep -> fast.** When the *primary* model returned
  503 ("experiencing high demand") the run died, which is precisely the failure
  graceful degradation is supposed to prevent. Now an ordered fallback chain,
  configurable via `MODEL_FALLBACKS`, logged as `model:degraded:a->b` so the step
  log always says which model actually answered.
- **Gap: no circuit breaker.** A model that is down cost the full retry backoff on
  *every* call; a 16-call run spent six minutes waiting on a model that answered
  503 the first time. Now a process-wide breaker opens for 120s after a model
  fails twice, so the chain falls through immediately.

Verified live: with `gemini-flash-latest` returning 503 and `gemini-3.6-flash`
also unavailable, a real classification call fell through to
`gemini-flash-lite-latest` and returned the correct answer — `amendment`,
reference `MSA-2024-0117` — with the fallback recorded in the step log.

Lesson worth keeping: a green suite against a fake provider says nothing about
whether the integration works. Both are needed, and only one of them was in place.

## 2026-08-17 — M7: upload, reported honestly

Uploading a PDF or a DOCX returned HTTP 500 with an empty body. The interface
showed nothing at all, so a user could not tell whether they had picked the wrong
file, whether the file was damaged, or whether the system was broken.

Three separate root causes, all one class of bug: **the ingest path only handled
the failure mode I had anticipated.** `UnsupportedFormat` was caught; everything
else propagated.

- `pdfminer.PDFSyntaxError` on a malformed PDF — uncaught.
- `zipfile.BadZipFile` on a malformed DOCX — uncaught.
- `psycopg.DataError: text fields cannot contain NUL bytes` — a single 0x00
  anywhere in a decoded file blew up at the database layer, several steps from
  the file that caused it.

Fixed as a class, not as three cases:

- `_guard()` wraps every parser, so whatever the next library raises becomes a
  named rejection rather than a 500.
- Control characters are stripped at normalisation, before anything reaches
  Postgres.
- `UnreadableDocument` is separate from `UnsupportedFormat` on purpose. "We do
  not accept .pptx" and "this .pdf is damaged" need different actions from
  whoever is holding the file, so the system says which one it is looking at.
- `ingest_bytes` isolates per file: one bad file in a batch of ten no longer
  takes the other nine down.
- A FastAPI exception handler is the last line of defence — no caller ever gets a
  bare "Internal Server Error" again; they get the exception class, the route,
  and where to find the traceback.
- Seven regression tests in `test_upload_robustness.py`. 33 tests green.

**Interface:** errors now surface as toasts, one per rejected file carrying its
reason, and an error toast stays until dismissed rather than clearing on a timer —
an error that vanishes on its own is an error nobody read. Run failures, gate
commits and API-unreachable all route through the same place.

**Also fixed while here:** the api container ran uvicorn without `--reload`
despite the source being bind-mounted, so code edits silently had no effect and I
spent a cycle debugging already-fixed code.

**Worth recording as a method note.** Two patches to `loader.py` silently failed
because the anchor text did not match, and a third wrote a real NUL byte into the
source where an escape sequence was intended — a file that then could not be
imported at all. Assert on every anchor before writing, and for anything involving
control characters, build them from `chr()` rather than trusting an escape to
survive the round trip. The code now does exactly that, and says why.
