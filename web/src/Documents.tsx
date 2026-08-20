import { useCallback, useEffect, useState } from "react";
import { api, Doc, IngestResult } from "./api";
import { useToast } from "./Toast";

/**
 * The pile, and how things get into it.
 *
 * Three outcomes are reported separately rather than collapsed into "uploaded":
 * accepted, duplicate (identical bytes already present, so this is a no-op), and
 * rejected with the reason named. A rejection says which of two different things
 * went wrong -- wrong format, or right format and unreadable content -- because
 * those need different actions from whoever is holding the file.
 */
export function Documents({ slug, onIngested }: { slug: string; onIngested: () => void }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [results, setResults] = useState<IngestResult[]>([]);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const load = useCallback(() => {
    api.documents(slug)
      .then(setDocs)
      .catch((e) =>
        toast({ kind: "error", title: "Could not list documents", detail: e.message })
      );
  }, [slug, toast]);

  useEffect(() => {
    setResults([]);
    load();
  }, [load]);

  async function send(files: FileList | File[]) {
    const list = Array.from(files ?? []);
    if (list.length === 0) return;
    setBusy(true);
    try {
      const res = await api.upload(slug, list);
      setResults(res);
      load();
      onIngested();

      const accepted = res.filter((r) => r.status === "accepted").length;
      const duplicate = res.filter((r) => r.status === "duplicate").length;
      const rejected = res.filter((r) => r.status === "rejected");

      if (rejected.length) {
        // One toast per rejection: a count tells you something failed, the reason
        // tells you what to do about it.
        for (const r of rejected) {
          toast({ kind: "error", title: `Rejected: ${r.filename}`, detail: r.reason });
        }
      }
      if (accepted) {
        toast({
          kind: "ok",
          title: `${accepted} file${accepted > 1 ? "s" : ""} accepted`,
          detail: "Press Run to classify and extract, or Update for an incremental pass.",
        });
      }
      if (duplicate) {
        toast({
          kind: "warn",
          title: `${duplicate} duplicate${duplicate > 1 ? "s" : ""} skipped`,
          detail: "Identical content was already ingested, so nothing was added.",
        });
      }
    } catch (e: any) {
      toast({
        kind: "error",
        title: "Upload failed",
        detail: `${e.message}. If this keeps happening, the API log has the detail (make logs).`,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div
        className={over ? "drop over" : "drop"}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          send(e.dataTransfer.files);
        }}
      >
        <p className="dropmain">
          {busy ? "Reading files..." : "Drop contracts, amendments or invoices here"}
        </p>
        <p className="dropsub">
          .md .txt .csv .eml .pdf .docx — anything else is rejected at intake with a
          reason, never silently ingested as an empty document.
        </p>
        <label className="filebtn">
          choose files
          <input
            type="file"
            multiple
            accept=".md,.txt,.csv,.eml,.pdf,.docx"
            onChange={(e) => {
              if (e.target.files) send(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
      </div>

      {results.length > 0 && (
        <div className="results">
          {results.map((r, n) => (
            <div key={n} className={"res " + r.status}>
              <span className={"status " + r.status}>{r.status}</span>
              <strong>{r.filename}</strong>
              {r.reason && <span className="why">{r.reason}</span>}
            </div>
          ))}
        </div>
      )}

      <h3 className="grouphead">
        the pile <span>{docs.length}</span>
      </h3>
      <table className="register">
        <thead>
          <tr>
            <th>File</th>
            <th>Type</th>
            <th>Conf</th>
            <th>Date</th>
            <th>sha256</th>
          </tr>
        </thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.id}>
              <td>
                {d.filename}
                {d.blocked_reason && <div className="why">{d.blocked_reason}</div>}
              </td>
              <td>
                <span className={"status " + (d.doc_type === "unknown" ? "blocked" : "settled")}>
                  {d.doc_type}
                </span>
              </td>
              <td className="num">{d.confidence ? d.confidence.toFixed(2) : "-"}</td>
              <td className="mono">{d.doc_date ?? "-"}</td>
              <td className="mono">{d.sha256}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
