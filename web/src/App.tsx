import { useCallback, useEffect, useMemo, useState } from "react";
import { api, Batch, ChainLink, Family, Item, Row, Run } from "./api";
import { SourcePane } from "./SourcePane";
import { Documents } from "./Documents";
import { useToast } from "./Toast";

type Tab = "documents" | "runs" | "register" | "review" | "findings" | "changelog";

export default function App() {
  const [families, setFamilies] = useState<Family[]>([]);
  const [slug, setSlug] = useState<string>("");
  const [tab, setTab] = useState<Tab>("review");
  const [runs, setRuns] = useState<Run[]>([]);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api.families().then((f) => {
      setFamilies(f);
      if (f.length && !slug) setSlug(f[0].slug);
    }).catch((e) =>
      toast({
        kind: "error",
        title: "Cannot reach the API",
        detail: `${e.message}. Is it running? Check with: docker compose ps`,
      })
    );
  }, []);

  const refreshRuns = useCallback(() => {
    if (!slug) return;
    api.runs(slug).then(setRuns).catch((e) =>
      toast({ kind: "error", title: "Could not load runs", detail: e.message })
    );
  }, [slug]);

  useEffect(refreshRuns, [refreshRuns]);

  const openRun = useMemo(
    () => runs.find((r) => r.status === "awaiting_approval") ?? runs[0],
    [runs]
  );

  async function newRun(mode: "full" | "incremental") {
    setBusy(true);
    try {
      const out = await api.startRun(slug, mode);
      refreshRuns();
      setTab("review");
      const s = out.stats ?? {};
      toast({
        kind: "ok",
        title: mode === "full" ? "Run complete" : "Incremental update complete",
        detail:
          s.rows_changed !== undefined
            ? `${s.rows_changed} row(s) changed, ${s.rows_byte_identical} byte-identical. Waiting for your review.`
            : "Waiting for your review.",
      });
    } catch (e: any) {
      toast({ kind: "error", title: "Run failed", detail: e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="shell">
      <header>
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <div>
            <h1>Standing Register</h1>
            <p>Contracts, amendments and invoices — one register, kept current.</p>
          </div>
        </div>
        <div className="controls">
          <label>
            Family
            <select value={slug} onChange={(e) => setSlug(e.target.value)}>
              {families.map((f) => (
                <option key={f.id} value={f.slug}>
                  {f.slug} ({f.documents} docs)
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => newRun("full")} disabled={busy || !slug}>
            {busy ? "Running…" : "Run"}
          </button>
          <button className="ghost" onClick={() => newRun("incremental")} disabled={busy || !slug}>
            Update
          </button>
        </div>
      </header>

      <nav className="tabs">
        {(["documents", "review", "register", "runs", "findings", "changelog"] as Tab[]).map((t) => (
          <button key={t} className={t === tab ? "tab active" : "tab"} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>

      <main>
        {tab === "documents" && slug && <Documents slug={slug} onIngested={refreshRuns} />}
        {tab === "review" && <Review run={openRun} onDone={refreshRuns} />}
        {tab === "register" && slug && <Register slug={slug} />}
        {tab === "runs" && <Runs runs={runs} />}
        {tab === "findings" && slug && <Findings slug={slug} />}
        {tab === "changelog" && slug && <Changelog slug={slug} />}
      </main>
    </div>
  );
}

/* ------------------------------------------------------------------ review */

function Review({ run, onDone }: { run?: Run; onDone: () => void }) {
  const [batch, setBatch] = useState<Batch | null>(null);
  const [choices, setChoices] = useState<Record<string, "accept" | "reject">>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  useEffect(() => {
    setChoices({});
    if (!run) return setBatch(null);
    api.approvals(run.run_id).then(setBatch).catch(() => setBatch(null));
  }, [run?.run_id]);

  if (!run) return <Empty>No runs yet. Press <b>Run</b> to build the register.</Empty>;
  if (run.status !== "awaiting_approval")
    return (
      <Empty>
        Run <code>{run.run_id}</code> is <b>{run.status}</b>. Nothing is waiting on a decision.
      </Empty>
    );
  if (!batch?.items.length) return <Empty>Nothing to review.</Empty>;

  const undecided = batch.items.filter((i) => !choices[i.item_id]).length;

  async function commit() {
    if (!batch?.batch_id) return;
    setBusy(true);
    try {
      await api.decide(
        batch.batch_id,
        batch.items.map((i) => ({
          item_id: i.item_id,
          decision: choices[i.item_id],
          reason: reasons[i.item_id] || null,
        }))
      );
      const after = await api.resume(run!.run_id);
      const accepted = Object.values(choices).filter((c) => c === "accept").length;
      toast({
        kind: "ok",
        title: "Decisions committed",
        detail: `${accepted} accepted, ${Object.keys(choices).length - accepted} rejected. Run is ${after.status}.`,
      });
      onDone();
    } catch (e: any) {
      toast({ kind: "error", title: "Could not commit decisions", detail: e.message });
    } finally {
      setBusy(false);
    }
  }

  const groups: Item["kind"][] = ["escalation", "conflict", "finding", "row_update"];

  return (
    <section className="review">
      <div className="gatebar">
        <div>
          <strong>{batch.items.length}</strong> items need a decision
          {undecided > 0 && <span className="muted"> · {undecided} still untouched</span>}
        </div>
        <div className="gateactions">
          <button
            className="ghost small"
            onClick={() =>
              setChoices(Object.fromEntries(batch.items.map((i) => [i.item_id, "accept" as const])))
            }
          >
            accept all
          </button>
          <button disabled={undecided > 0 || busy} onClick={commit}>
            {busy ? "Committing…" : "Commit decisions"}
          </button>
        </div>
      </div>
      {undecided > 0 && (
        <p className="hint">
          Every item must be explicitly accepted or rejected. Rejecting one leaves the rest alone.
        </p>
      )}

      {groups.map((kind) => {
        const items = batch.items.filter((i) => i.kind === kind);
        if (!items.length) return null;
        return (
          <div key={kind}>
            <h3 className="grouphead">{kind.replace("_", " ")} <span>{items.length}</span></h3>
            {items.map((i) => (
              <article key={i.item_id} className={`item ${choices[i.item_id] ?? ""}`}>
                <div className="itemhead">
                  <span className={`chip ${i.kind}`}>{i.kind.replace("_", " ")}</span>
                  <h4>{i.title}</h4>
                  <div className="verdict">
                    <button
                      className={choices[i.item_id] === "accept" ? "yes on" : "yes"}
                      onClick={() => setChoices({ ...choices, [i.item_id]: "accept" })}
                    >
                      accept
                    </button>
                    <button
                      className={choices[i.item_id] === "reject" ? "no on" : "no"}
                      onClick={() => setChoices({ ...choices, [i.item_id]: "reject" })}
                    >
                      reject
                    </button>
                  </div>
                </div>
                <ItemBody item={i} />
                {choices[i.item_id] === "reject" && (
                  <input
                    className="reason"
                    placeholder="Why? (recorded against this item)"
                    value={reasons[i.item_id] ?? ""}
                    onChange={(e) => setReasons({ ...reasons, [i.item_id]: e.target.value })}
                  />
                )}
              </article>
            ))}
          </div>
        );
      })}
    </section>
  );
}

function ItemBody({ item }: { item: Item }) {
  if (item.kind === "row_update") {
    const before = item.payload.before?.display ?? "—";
    const after = item.payload.after?.display ?? "—";
    const chain: ChainLink[] = item.payload.chain ?? [];
    return (
      <>
        <div className="diff">
          <span className="was">{before}</span>
          <span className="arrow">→</span>
          <span className="now">{after}</span>
        </div>
        {chain.length > 0 && (
          <ol className="chain">
            {chain.map((c, n) => (
              <li key={n}>
                <span className="src">{c.filename}</span>
                <span className="eff">{c.effective_from ?? "no date"}</span>
                <q>{c.quote}</q>
              </li>
            ))}
          </ol>
        )}
      </>
    );
  }
  if (item.kind === "finding") {
    return (
      <>
        <p className="detail">{item.payload.detail}</p>
        <p className="meta">
          {item.payload.rule_id} · stage {item.payload.stage} · {item.payload.kind}
        </p>
        <ol className="chain">
          {(item.payload.evidence ?? []).map((e: any, n: number) => (
            <li key={n}>
              <span className="src">{e.filename}</span>
              <q>{e.quote}</q>
            </li>
          ))}
        </ol>
      </>
    );
  }
  return <pre className="payload">{JSON.stringify(item.payload, null, 2)}</pre>;
}

/* ---------------------------------------------------------------- register */

function Register({ slug }: { slug: string }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [absent, setAbsent] = useState<string[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api.register(slug).then((r) => {
      setRows(r.rows);
      setAbsent(r.unsupported_terms);
    });
    setOpen(null);
  }, [slug]);

  if (!rows.length)
    return <Empty>Nothing published yet. The register appears once a human approves a run.</Empty>;

  return (
    <section className="split">
      <table className="register">
        <thead>
          <tr>
            <th>Term</th>
            <th>Current value</th>
            <th>Status</th>
            <th>v</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.term_key}
              className={open === r.term_key ? "sel" : ""}
              onClick={() => setOpen(r.term_key)}
            >
              <td>{r.label}</td>
              <td className="val">{r.value.display ?? "—"}</td>
              <td>
                <span className={`status ${r.status}`}>{r.status}</span>
              </td>
              <td className="num">{r.version}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div>
        {open ? (
          <SourcePane slug={slug} termKey={open} />
        ) : (
          <Empty>Pick a term to see exactly where its value came from.</Empty>
        )}
        {absent.length > 0 && (
          <div className="absent">
            <h4>Not stated in sources</h4>
            <p>Looked for, genuinely absent, reported rather than inferred.</p>
            <ul>
              {absent.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

/* -------------------------------------------------------------------- runs */

function Runs({ runs }: { runs: Run[] }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Run | null>(null);
  const [cost, setCost] = useState<any>(null);

  useEffect(() => {
    if (!openId) return;
    api.run(openId).then(setDetail);
    api.cost(openId).then(setCost);
  }, [openId]);

  if (!runs.length) return <Empty>No runs yet.</Empty>;

  return (
    <section className="split">
      <table className="register">
        <thead>
          <tr>
            <th>Run</th>
            <th>Mode</th>
            <th>Status</th>
            <th>Changed</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id} className={openId === r.run_id ? "sel" : ""} onClick={() => setOpenId(r.run_id)}>
              <td className="mono">{r.run_id.slice(4, 14)}</td>
              <td>{r.mode}</td>
              <td>
                <span className={`status ${r.status}`}>{r.status}</span>
              </td>
              <td className="num">
                {r.stats?.rows_changed ?? "—"}
                {r.stats?.rows_byte_identical !== undefined && (
                  <span className="muted"> / {r.stats.rows_byte_identical} same</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div>
        {detail ? (
          <>
            {cost && (
              <div className="cost">
                <h4>Where the time and money went</h4>
                <table className="mini">
                  <tbody>
                    {cost.by_stage.map((s: any) => (
                      <tr key={s.node}>
                        <td>{s.node}</td>
                        <td className="num">{s.ms} ms</td>
                        <td className="num">{s.tokens_in + s.tokens_out} tok</td>
                        <td className="num">${s.usd.toFixed(6)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="meta">{cost.note}</p>
              </div>
            )}
            <h4 className="grouphead">Stage decisions</h4>
            <ol className="timeline">
              {detail.steps.map((s) => (
                <li key={s.seq} className={s.decision.includes("escalat") ? "esc" : ""}>
                  <span className="node">{s.node}</span>
                  <span className="dec">{s.decision}</span>
                  {s.confidence !== null && <span className="conf">{s.confidence.toFixed(2)}</span>}
                  {s.replayed && <span className="chip small">cached</span>}
                </li>
              ))}
            </ol>
          </>
        ) : (
          <Empty>Pick a run to see every decision it took.</Empty>
        )}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- findings */

function Findings({ slug }: { slug: string }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    api.findings(slug).then(setData);
  }, [slug]);
  if (!data) return <Empty>Loading…</Empty>;
  if (!data.count)
    return (
      <div className="clean">
        <h3>No findings</h3>
        <p>{data.message}</p>
      </div>
    );
  return (
    <section>
      {data.findings.map((f: any) => (
        <article key={f.id} className="item">
          <div className="itemhead">
            <span className={`chip sev-${f.severity}`}>{f.severity}</span>
            <h4>{f.title}</h4>
            <span className={`status ${f.status}`}>{f.status}</span>
          </div>
          <p className="detail">{f.detail}</p>
          <p className="meta">
            {f.rule_id} · {f.rule_pack} · stage {f.stage} · {f.kind}
          </p>
          <ol className="chain">
            {f.evidence.map((e: any, n: number) => (
              <li key={n}>
                <span className="src">{e.filename}</span>
                <q>{e.quote}</q>
              </li>
            ))}
          </ol>
        </article>
      ))}
    </section>
  );
}

/* --------------------------------------------------------------- changelog */

function Changelog({ slug }: { slug: string }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    api.changelog(slug).then(setData);
  }, [slug]);
  if (!data?.entries?.length) return <Empty>Nothing has changed yet.</Empty>;
  return (
    <table className="register">
      <thead>
        <tr>
          <th>Term</th>
          <th>v</th>
          <th>Value</th>
          <th>Because of</th>
          <th>When</th>
        </tr>
      </thead>
      <tbody>
        {data.entries.map((e: any, n: number) => (
          <tr key={n}>
            <td>{e.label}</td>
            <td className="num">{e.version}</td>
            <td className="val">{e.value?.display ?? "—"}</td>
            <td className="mono">{e.because_of ?? "—"}</td>
            <td className="mono">{e.at?.slice(0, 19).replace("T", " ")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}
