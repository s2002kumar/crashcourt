"use client";

// A native radio group: arrow keys, focus, and the checked state come from the platform, at no cost to
// the bundle every page loads.

import { useEffect, useState } from "react";

import { THEME_KEY } from "@/lib/theme";

type Choice = "system" | "dark" | "light";

const CHOICES: readonly { value: Choice; label: string }[] = [
  { value: "system", label: "Auto" },
  { value: "dark", label: "Dark" },
  { value: "light", label: "Light" },
];

function apply(choice: Choice): void {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
  try {
    if (choice === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, choice);
  } catch {
    // Storage can be unavailable (private windows); the choice still applies to this page.
  }
}

export function ThemeToggle() {
  const [choice, setChoice] = useState<Choice>("system");

  useEffect(() => {
    const t = document.documentElement.dataset.theme;
    if (t === "dark" || t === "light") setChoice(t);
  }, []);

  return (
    <fieldset className="flex rounded-panel border border-rule p-0.5">
      <legend className="sr-only">Color theme</legend>
      {CHOICES.map((c) => (
        <label
          key={c.value}
          className="label cursor-pointer rounded-[2px] px-2 py-1 text-ink-muted transition-colors duration-(--motion-fast) hover:text-ink has-checked:bg-raised has-checked:text-ink has-focus-visible:outline-2 has-focus-visible:outline-offset-1 has-focus-visible:outline-focus"
        >
          <input
            type="radio"
            name="theme"
            value={c.value}
            checked={choice === c.value}
            onChange={() => {
              setChoice(c.value);
              apply(c.value);
            }}
            className="sr-only"
          />
          {c.label}
        </label>
      ))}
    </fieldset>
  );
}
