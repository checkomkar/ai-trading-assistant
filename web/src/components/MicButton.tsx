import { useEffect, useRef, useState } from "react";
import { getJSON, postAudio } from "../api";

// The browser's own speech recognition (Chrome, Edge, Safari): used when the server has no Deepgram key.
type Recognition = { lang: string; interimResults: boolean; continuous: boolean; start(): void; stop(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null; onend: (() => void) | null };
const BrowserSR: (new () => Recognition) | undefined =
  typeof window !== "undefined" ? ((window as unknown as Record<string, unknown>).SpeechRecognition ?? (window as unknown as Record<string, unknown>).webkitSpeechRecognition) as never : undefined;

/** Push-to-talk: record with the browser mic, send to /speech/transcribe (Deepgram), hand the text back. Without a
 *  Deepgram key on the server, the browser's built-in speech recognition is used where it exists. */
export function MicButton({ onText, onError, disabled, compact }: { onText: (t: string) => void; onError: (m: string) => void; disabled?: boolean; compact?: boolean }) {
  const [state, setState] = useState<"idle" | "recording" | "transcribing">("idle");
  const [configured, setConfigured] = useState<boolean | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const sr = useRef<Recognition | null>(null);
  const supported = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";

  useEffect(() => {
    getJSON<{ configured: boolean }>("/speech/status").then((s) => setConfigured(s.configured)).catch(() => setConfigured(false));
    return () => {
      rec.current?.stream.getTracks().forEach((t) => t.stop());
      sr.current?.stop();
    };
  }, []);

  const startBrowser = () => {
    if (!BrowserSR) return;
    const r = new BrowserSR();
    r.lang = "en-IN";
    r.interimResults = false;
    r.continuous = true;
    let heard = "";
    r.onresult = (e) => {
      heard = Array.from(e.results).map((x) => x[0].transcript).join(" ").trim();
    };
    r.onerror = (e) => {
      if (e.error !== "no-speech" && e.error !== "aborted") onError(e.error === "not-allowed" ? "Microphone unavailable. Allow mic access for this page in the browser." : `Voice input failed (${e.error}).`);
    };
    r.onend = () => {
      setState("idle");
      sr.current = null;
      if (heard) onText(heard);
    };
    sr.current = r;
    r.start();
    setState("recording");
  };
  const browserMode = configured === false && !!BrowserSR;

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

  const stop = () => {
    if (sr.current) return sr.current.stop();
    if (rec.current?.state === "recording") rec.current.stop();
  };

  if (!supported && !BrowserSR) return null;
  const title = configured === false && !browserMode ? "Voice input is not available in this browser" : state === "recording" ? "Stop and transcribe" : "Speak instead of typing";
  return (
    <button
      type="button"
      onClick={state === "recording" ? stop : browserMode ? startBrowser : start}
      disabled={disabled || state === "transcribing" || (configured === false && !browserMode)}
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
