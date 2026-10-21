import { useCallback, useEffect, useState, type FormEvent } from "react";
import { del, getJSON, postJSON } from "../api";

interface BrokerField {
  key: string;
  label: string;
  secret: boolean;
}
interface BrokerInfo {
  id: string;
  name: string;
  status: "available" | "planned";
  data_note: string;
  docs_url: string;
  daily_login: boolean;
  fields: BrokerField[];
}
interface Connection {
  id: string;
  broker: string;
  name: string;
  public: Record<string, string>;
  status: string;
  created_at: string;
}

export function BrokersPage() {
  const [catalog, setCatalog] = useState<{ vault_ready: boolean; brokers: BrokerInfo[] } | null>(null);
  const [conns, setConns] = useState<Connection[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(() => {
    getJSON<{ vault_ready: boolean; brokers: BrokerInfo[] }>("/brokers/catalog").then(setCatalog).catch((e) => setMsg({ ok: false, text: (e as Error).message }));
    getJSON<Connection[]>("/brokers/connections").then(setConns).catch(() => setConns([]));
  }, []);
  useEffect(load, [load]);

  const save = async (e: FormEvent, b: BrokerInfo) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await postJSON("/brokers/connections", { broker: b.id, values });
      setEditing(null);
      setValues({});
      setMsg({ ok: true, text: `${b.name} keys saved (encrypted).` });
      load();
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (c: Connection) => {
    if (!window.confirm(`Remove your ${c.name} keys?`)) return;
    try {
      await del(`/brokers/connections/${c.id}`);
      load();
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message });
    }
  };

  const input = "w-full rounded-md border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-ink-100 outline-none focus:border-accent";
  return (
    <main className="mx-auto w-full max-w-4xl flex-1 space-y-6 p-4 sm:p-6">
      <header>
        <h1 className="text-xl font-semibold">Brokers</h1>
        <p className="mt-1 text-sm text-ink-400">
          Bring your own broker API keys. They are encrypted on the server, never shown again, and used only for your account.
          We never place orders on a pooled account, and this version does not place live orders at all.
        </p>
      </header>
      {catalog && !catalog.vault_ready && (
        <p className="rounded-md border border-down/40 bg-down/10 px-3 py-2 text-sm text-down" role="alert">
          Saving keys is switched off: the server has no CREDENTIAL_KEY yet.
        </p>
      )}
      {msg && <p className={`rounded-md border px-3 py-2 text-sm ${msg.ok ? "border-up/40 bg-up/10 text-up" : "border-down/40 bg-down/10 text-down"}`} role="status">{msg.text}</p>}

      <ul className="space-y-3">
        {catalog?.brokers.map((b) => {
          const c = conns.find((x) => x.broker === b.id);
          return (
            <li key={b.id} className="rounded-lg border border-ink-800 bg-ink-900 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold">{b.name}</h2>
                <span className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1 ${b.status === "available" ? "bg-up/10 text-up ring-up/30" : "bg-ink-800 text-ink-400 ring-ink-600"}`}>
                  {b.status === "available" ? "Supported" : "Keys can be saved · adapter coming"}
                </span>
                {c && <span className="rounded bg-accent/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-accent">Connected</span>}
                <a href={b.docs_url} target="_blank" rel="noreferrer" className="ml-auto text-xs text-ink-400 underline hover:text-ink-100">API docs</a>
              </div>
              <p className="mt-1 text-xs text-ink-400">{b.data_note} Check the broker's current pricing. {b.daily_login && "Sign-in is repeated daily, because the broker's token expires."}</p>
              {c && <p className="num mt-2 text-xs text-ink-300">{Object.entries(c.public).map(([k, v]) => `${k}: ${v}`).join(" · ")} · secrets saved</p>}

              {editing === b.id ? (
                <form onSubmit={(e) => save(e, b)} className="mt-3 grid gap-3 sm:grid-cols-2">
                  {b.fields.map((f) => (
                    <label key={f.key} className="block text-xs text-ink-300">
                      {f.label}
                      <input className={`${input} mt-1`} type={f.secret ? "password" : "text"} required autoComplete="off" spellCheck={false}
                        value={values[f.key] ?? ""} onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))} />
                    </label>
                  ))}
                  <div className="flex gap-2 sm:col-span-2">
                    <button disabled={busy} className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-ink-950 disabled:opacity-60">{busy ? "Saving…" : "Save keys"}</button>
                    <button type="button" onClick={() => { setEditing(null); setValues({}); }} className="rounded-md px-3 py-1.5 text-sm text-ink-300 hover:text-ink-100">Cancel</button>
                  </div>
                </form>
              ) : (
                <div className="mt-3 flex gap-2 text-xs">
                  <button disabled={!catalog.vault_ready} onClick={() => { setEditing(b.id); setValues({}); }}
                    className="rounded-md border border-ink-700 px-3 py-1.5 text-ink-100 hover:bg-ink-800 disabled:opacity-50">{c ? "Replace keys" : "Add keys"}</button>
                  {c && <button onClick={() => remove(c)} className="rounded-md px-3 py-1.5 text-ink-400 hover:text-down">Remove</button>}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
