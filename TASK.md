# How to work in this repository

Written for whoever picks this up next, human or agent.

## Ground rules

1. **The register is a projection.** No model output is ever written into the
   deliverable. If you find yourself asking a model to phrase a register row, stop —
   the row is a function of `fact` and `fact_span`, and that is what makes byte
   identity provable.
2. **Every fact carries a span, and the span is verified.** `verify_spans` re-matches
   the quote against the document text. Do not make that matcher fuzzy. Tolerance
   there is how bluffing gets back in.
3. **Write the rule before the code.** For anything in the hard part, write down
   what the system must never do, then the test that proves it, then the code. The
   behaviour suite is organised that way on purpose.
4. **Nodes commit before they return.** Each graph node opens its own session and
   commits. That ordering is the reason a kill cannot land between the domain write
   and the checkpoint.
5. **Domain logic lives in `app/domain/`.** Route handlers and MCP tools are thin
   adapters. If a capability exists in one surface and not the other, it is in the
   wrong place.

## Adding things

| You want to | Do this | Do not |
| --- | --- | --- |
| Track a new contract term | Add to `TERM_KEYS` and `TERM_LABELS` | Touch the graph |
| Add a compliance rule | Add a YAML entry in `rules/` | Add an `if` to `examine` |
| Add a new kind of check | One function in `app/rules/deterministic.py` with `@register` | Special-case a term |
| Support a new file format | One branch in `app/ingest/loader.py` + update `ACCEPTED` | Silently accept and produce empty text |
| Add an MCP tool | Wrap an existing `app/domain/` function | Put logic in the tool |

## Running things

```bash
make up        # full stack, seeded
make test      # behaviour suite, no API key
make demo      # one family end to end in the terminal
make psql      # a shell on the database
make clean     # down, and drop the volume
```

Useful environment switches:

- `MODEL_BACKEND=fixture|cassette|gemini|record`
- `CRASH_AFTER_NODE=<node>` — fault injection, used by the resume test
- `MAX_LLM_CALLS_PER_RUN` — the budget ceiling; a run that hits it fails loudly

## Verification convention

At the end of a milestone, hand a fresh session only the README and the tests and
ask it to run the system and report what it cannot do. A verifier that is not the
implementer catches what the author cannot. What it finds goes into `PROGRESS.md`
before the next milestone starts.

## Things that will bite you

- **Content hashes must not include surrogate keys.** `fact_id` and `document_id` are
  regenerated per run. Hashing them makes every row look changed on every run and
  silently destroys the incremental guarantee. This has already happened once.
- **Facts are per document, not per run.** Deleting only the current run's facts
  leaves the previous run's copies in the chain and doubles every value.
- **The advisory lock is transaction-scoped.** It releases on commit *or* rollback,
  which is what stops a killed process stranding it. Do not switch to a
  session-scoped lock.
- **`websearch_to_tsquery` ANDs.** Use the OR builder in `app/retrieval/search.py`.
- **Never hold an open transaction across schema setup.** `PostgresSaver.setup()`
  runs `CREATE INDEX CONCURRENTLY`, which waits on every concurrent transaction.
  A transaction-scoped advisory lock around it deadlocks the entire stack with no
  error message. Use `schema_lock()`, which is session-scoped and autocommit.
- **The suite runs on its own database.** `make test` points `DATABASE_URL` at
  `standing_register_test` so truncation does not fight the running watcher.
