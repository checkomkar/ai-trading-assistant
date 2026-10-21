import { useEffect, useRef, useState } from "react";
import { getJSON, postAudio } from "../api";

/** Push-to-talk: record with the browser mic, send to /speech/transcribe (Deepgram), hand the text back. */
export function MicButton({ onText, onError, disabled, compact }: { onText: (t: string) => void; onError: (m: string) => void; disabled?: boolean; compact?: boolean }) {
  const [state, setState] = useState<"idle" | "recording" | "transcribing">("idle");
  const [configured, setConfigured] = useState<boolean | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const supported = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";

  useEffect(() => {
    getJSON<{ configured: boolean }>("/speech/status").then((s) => setConfigured(s.configured)).catch(() => setConfigured(false));
    return () => rec.current?.stream.getTracks().forEach((t) => t.stop());
  }, []);

  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const r = new MediaRecorder(stream);
      chunks.current = [];
      r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      r.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setState("transcribing");
        try {
          const blob = new Blob(chunks.current, { type: r.mimeType || "audio/webm" });
          const { text } = await postAudio<{ text: string }>("/speech/transcribe", blob);
          if (text) onText(text);
          else onError("Nothing was heard. Try again a little closer to the mic.");
        } catch (e) {
          onError((e as Error).message);
        } finally {
          setState("idle");
        }
      };
      rec.current = r;
      r.start();
      setState("recording");
    } catch {
      onError("Microphone unavailable. Allow mic access for this page in the browser.");
    }
  };

  const stop = () => rec.current?.state === "recording" && rec.current.stop();

  if (!supported) return null;
  const title = configured === false ? "Voice input needs DEEPGRAM_API_KEY in .env" : state === "recording" ? "Stop and transcribe" : "Describe by voice";
  return (
    <button
      type="button"
      onClick={state === "recording" ? stop : start}
      disabled={disabled || state === "transcribing" || configured === false}
      title={title}
      aria-label={title}
      className={
        compact
          ? `grid h-9 w-9 place-items-center rounded-full disabled:opacity-40 ${
              state === "recording" ? "animate-pulse bg-down/20 text-down" : "text-ink-300 hover:bg-ink-800 hover:text-ink-100"
            }`
          : `flex items-center gap-1 rounded-md border px-2.5 py-1.5 disabled:opacity-50 ${
              state === "recording" ? "border-down bg-down/15 text-down" : "border-ink-700 text-ink-300 hover:text-ink-100"
            }`
      }
    >
      <svg width={compact ? 18 : 13} height={compact ? 18 : 13} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
        <rect x="9" y="3" width="6" height="11" rx="3" />
        <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
      </svg>
      {!compact && (state === "recording" ? "Stop" : state === "transcribing" ? "Transcribing…" : "Speak")}
    </button>
  );
}
