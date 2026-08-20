import { useEffect, useState } from "react";
import { api, ChainLink } from "./api";

/**
 * The screen where provenance stops being a claim.
 *
 * Clicking a register value opens the document it came from, scrolled to the
 * exact character range, with that range highlighted. Nothing here is a summary
 * of the source; it is the source.
 */
export function SourcePane({ slug, termKey }: { slug: string; termKey: string }) {
  const [chain, setChain] = useState<ChainLink[]>([]);
  const [label, setLabel] = useState("");
  const [conflicts, setConflicts] = useState<any[]>([]);
  const [active, setActive] = useState(0);
  const [doc, setDoc] = useState<{ filename: string; text: string } | null>(null);

  useEffect(() => {
    api.provenance(slug, termKey).then((p) => {
      setChain(p.chain);
      setLabel(p.label);
      setConflicts(p.conflicts ?? []);
      setActive(Math.max(0, p.chain.length - 1));
    });
  }, [slug, termKey]);

  const link = chain[active];

  useEffect(() => {
    if (!link) return setDoc(null);
    api.documentText(link.document_id).then(setDoc);
  }, [link?.document_id]);

  if (!chain.length) return <div className="empty">No source supports this term.</div>;

  return (
    <div className="source">
      <h4>{label}</h4>
      {conflicts.length > 0 && (
        <div className="banner warn">
          Contested: {conflicts[0].values?.join(" vs ")} on {conflicts[0].effective_from}, across{" "}
          {conflicts[0].documents?.join(", ")}. Surfaced, not resolved.
        </div>
      )}
      <ol className="chainnav">
        {chain.map((c, n) => (
          <li key={n}>
            <button className={n === active ? "on" : ""} onClick={() => setActive(n)}>
              <span className="eff">{c.effective_from ?? "—"}</span>
              <span className="v">{c.value}</span>
              <span className="src">{c.filename}</span>
            </button>
          </li>
        ))}
      </ol>
      {doc && link && <Highlight text={doc.text} start={link.char_start} end={link.char_end} />}
    </div>
  );
}

function Highlight({ text, start, end }: { text: string; start: number; end: number }) {
  const before = text.slice(Math.max(0, start - 600), start);
  const hit = text.slice(start, end);
  const after = text.slice(end, end + 600);
  return (
    <pre className="doc">
      <span className="dim">{before}</span>
      <mark>{hit}</mark>
      <span className="dim">{after}</span>
    </pre>
  );
}
