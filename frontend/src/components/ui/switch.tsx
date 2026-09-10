"use client";

import { cn } from "@/lib/utils";

export function Switch({
  checked,
  onCheckedChange,
  disabled,
  "aria-label": ariaLabel,
}: {
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  disabled?: boolean;
  "aria-label": string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full before:absolute before:h-5 before:w-9 before:rounded-full before:transition-colors disabled:opacity-50",
        checked ? "before:bg-primary" : "before:bg-muted",
      )}
    >
      <span
        className={cn(
          "absolute inline-block h-4 w-4 rounded-full bg-background shadow transition-transform",
          checked ? "translate-x-2" : "-translate-x-2",
        )}
      />
    </button>
  );
}
