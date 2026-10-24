import { useLayoutEffect, useRef, type ReactNode } from "react";
import { MicButton } from "../components/MicButton";
import { toast } from "./feedback";

/** A text area that grows with its text (up to `maxRows`, then scrolls), with voice input. Enter sends when `onSubmit`
 *  is given; Shift+Enter starts a new line. */
export function PromptBox({
  value,
  onChange,
  onSubmit,
  placeholder,
  label,
  disabled,
  minRows = 2,
  maxRows = 10,
  actions,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit?: () => void;
  placeholder?: string;
  label: string;
  disabled?: boolean;
  minRows?: number;
  maxRows?: number;
  actions?: ReactNode;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const line = parseFloat(getComputedStyle(el).lineHeight) || 18;
    el.style.height = "auto";
    const pad = el.offsetHeight - el.clientHeight + 16; // borders + vertical padding
    el.style.height = `${Math.min(Math.max(el.scrollHeight, line * minRows + pad), line * maxRows + pad)}px`;
  }, [value, minRows, maxRows]);
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-850 focus-within:border-accent">
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (onSubmit && e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (value.trim()) onSubmit();
          }
        }}
        placeholder={placeholder}
        aria-label={label}
        disabled={disabled}
        rows={minRows}
        className="block w-full resize-none bg-transparent px-3 py-2 text-xs leading-relaxed text-ink-100 outline-none placeholder:text-ink-400 disabled:opacity-60"
      />
      <div className="flex items-center gap-2 px-2 pb-2">
        <MicButton
          compact
          disabled={disabled}
          onError={(m) => toast.error(m)}
          onText={(t) => onChange(value.trim() ? `${value.trim()} ${t}` : t)}
        />
        {onSubmit && <span className="hidden text-[10px] text-ink-500 sm:inline">Enter to send · Shift+Enter for a new line</span>}
        <div className="ml-auto flex items-center gap-2">{actions}</div>
      </div>
    </div>
  );
}
