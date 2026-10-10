// Page scaffolding: the page header, instrument panels, readouts, and empty states.

import { useId } from "react";

export function PageHeader({
  experience,
  route,
  title,
  children,
}: {
  experience: string;
  route: string;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="grid gap-4 border-b border-rule pb-8">
      <p className="label flex flex-wrap gap-x-3">
        <span>{experience}</span>
        <span aria-hidden="true" className="text-ink-faint">
          /
        </span>
        <span className="min-w-0 normal-case tracking-normal wrap-anywhere text-ink-faint">
          {route}
        </span>
      </p>
      <h1 className="display max-w-[22ch] text-4xl text-ink sm:text-5xl">{title}</h1>
      {children ? <div className="max-w-[68ch] text-lg text-ink-muted">{children}</div> : null}
    </header>
  );
}

export function Panel({
  label,
  children,
  className,
  headingLevel = 2,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
  headingLevel?: 2 | 3;
}) {
  const id = useId();
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section aria-labelledby={id} className={`panel p-5 ${className ?? ""}`}>
      <Heading id={id} className="label mb-4">
        {label}
      </Heading>
      {children}
    </section>
  );
}

/** A named value in mono. `null` renders as an explicit dash: no value, never a zero. */
export function Readout({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="grid gap-1">
      <dt className="label">{label}</dt>
      <dd className="readout text-lg wrap-anywhere text-ink">
        {value ?? (
          <>
            <span aria-hidden="true" className="text-ink-faint">
              &mdash;
            </span>
            <span className="sr-only">none</span>
          </>
        )}
      </dd>
    </div>
  );
}

/** What a panel shows before any artifact exists: a flat line and one plain sentence. */
export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid gap-3">
      <svg
        aria-hidden="true"
        viewBox="0 0 240 24"
        className="h-6 w-full max-w-60"
        preserveAspectRatio="none"
      >
        <path d="M0 12h240" stroke="var(--rule-strong)" strokeWidth="1" strokeDasharray="2 4" />
      </svg>
      <p className="max-w-[60ch] text-sm text-ink-muted">{children}</p>
    </div>
  );
}
