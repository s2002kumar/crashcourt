"use client";

// The keyboard list, in a native modal <dialog>: the browser makes the rest of the page inert, closes it
// on Escape, and returns focus to the opener, so every page gets it without a dialog library.

import { useEffect, useRef } from "react";

const SHORTCUTS: readonly [keys: string[], action: string][] = [
  [["Tab"], "Move to the next control or link"],
  [["Shift", "Tab"], "Move back"],
  [["Enter"], "Follow a link or press a button"],
  [["?"], "Open this list"],
  [["Esc"], "Close it"],
];

function typingInto(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
  );
}

export function Shortcuts() {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey && !typingInto(e.target)) {
        e.preventDefault();
        if (!dialog.current?.open) dialog.current?.showModal();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <button
        type="button"
        aria-haspopup="dialog"
        onClick={() => dialog.current?.showModal()}
        className="label flex items-center gap-1.5 rounded-panel border border-rule px-2 py-1 text-ink-muted hover:text-ink"
      >
        Keys
        <kbd className="rounded-[2px] border border-rule px-1 normal-case text-ink">?</kbd>
      </button>
      <dialog
        ref={dialog}
        aria-labelledby="shortcuts-title"
        aria-describedby="shortcuts-description"
        onClick={(e) => {
          if (e.target === e.currentTarget) e.currentTarget.close(); // a click on the backdrop
        }}
        className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-panel border border-rule-strong bg-panel p-0 text-ink shadow-2xl backdrop:bg-bg/70 backdrop:backdrop-blur-[2px]"
      >
        <div className="p-6">
          <h2 id="shortcuts-title" className="label">
            Keyboard
          </h2>
          <p id="shortcuts-description" className="mt-2 text-sm text-ink-muted">
            Every page works without a mouse. Pages with timelines and grids add their own keys.
          </p>
          <dl className="mt-5 grid gap-2.5">
            {SHORTCUTS.map(([keys, action]) => (
              <div key={action} className="grid grid-cols-[7rem_1fr] items-center gap-3 text-sm">
                <dt className="flex gap-1">
                  {keys.map((k) => (
                    <kbd
                      key={k}
                      className="readout rounded-[2px] border border-rule-strong bg-raised px-1.5 py-0.5 text-xs"
                    >
                      {k}
                    </kbd>
                  ))}
                </dt>
                <dd className="text-ink-muted">{action}</dd>
              </div>
            ))}
          </dl>
          <form method="dialog">
            <button
              type="submit"
              className="label mt-6 rounded-panel border border-rule px-3 py-1.5 text-ink hover:bg-raised"
            >
              Close
            </button>
          </form>
        </div>
      </dialog>
    </>
  );
}
