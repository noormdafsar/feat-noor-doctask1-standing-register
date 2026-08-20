import { createContext, useCallback, useContext, useState } from "react";

type Kind = "ok" | "warn" | "error";

export type ToastInput = {
  kind: Kind;
  title: string;
  detail?: string;
  /** Errors stay until dismissed; anything else clears itself. */
  sticky?: boolean;
};

type Toast = ToastInput & { id: number; sticky: boolean };

const ToastCtx = createContext<(t: ToastInput) => void>(() => {});

export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setItems((xs) => xs.filter((x) => x.id !== id));
  }, []);

  const push = useCallback((t: ToastInput) => {
    const id = Date.now() + Math.random();
    // An error that vanishes on a timer is an error nobody read.
    const sticky = t.sticky ?? t.kind === "error";
    setItems((xs) => [...xs, { ...t, id, sticky }]);
    if (!sticky) window.setTimeout(() => dismiss(id), 6000);
  }, [dismiss]);

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={"toast " + t.kind}>
            <div className="toastbody">
              <strong>{t.title}</strong>
              {t.detail && <p>{t.detail}</p>}
            </div>
            <button
              className="toastclose"
              aria-label="Dismiss"
              onClick={() => dismiss(t.id)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
