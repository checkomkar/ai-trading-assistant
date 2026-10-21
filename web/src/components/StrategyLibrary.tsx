import { useCallback, useEffect, useState, type DragEvent } from "react";
import { del, getJSON, postJSON, sendJSON, type SavedStrategy, type StrategyFolder } from "../api";

const Icon = ({ d, size = 14 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d={d} />
  </svg>
);
const I = {
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  folderPlus: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 10v6M9 13h6",
  chevron: "M9 6l6 6-6 6",
  pencil: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
  trash: "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6",
  grip: "M9 6h.01M9 12h.01M9 18h.01M15 6h.01M15 12h.01M15 18h.01",
};

const DRAG_TYPE = "application/x-strategy-name";
const OPEN_KEY = "strategy-folders-open";

function loadOpen(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(OPEN_KEY) ?? "{}");
  } catch {
    return {};
  }
}

interface Props {
  saved: SavedStrategy[];
  current: SavedStrategy | null;
  onOpen: (s: SavedStrategy) => void;
  onChanged: () => void; // re-fetch the strategy list after a move
}

/** Saved strategies, filed into folders. Drag a strategy onto a folder (or onto "Ungrouped"), or use its Move menu. */
export function StrategyLibrary({ saved, current, onOpen, onChanged }: Props) {
  const [folders, setFolders] = useState<StrategyFolder[]>([]);
  const [open, setOpen] = useState<Record<string, boolean>>(loadOpen);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null); // folder id, or "none" for the ungrouped area
  const [error, setError] = useState<string | null>(null);

  const loadFolders = useCallback(
    () => getJSON<StrategyFolder[]>("/strategies/folders").then(setFolders).catch((e) => setError((e as Error).message)),
    [],
  );
  useEffect(() => {
    loadFolders();
  }, [loadFolders, saved]);

  const toggle = (id: string, value?: boolean) =>
    setOpen((o) => {
      const next = { ...o, [id]: value ?? !(o[id] ?? true) };
      try {
        localStorage.setItem(OPEN_KEY, JSON.stringify(next));
      } catch {
        /* storage blocked: the state just won't persist */
      }
      return next;
    });

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const create = () =>
    run(async () => {
      if (!draft.trim()) return setCreating(false);
      const f = await postJSON<StrategyFolder>("/strategies/folders", { name: draft });
      setDraft("");
      setCreating(false);
      toggle(f.id, true);
      await loadFolders();
    });

  const rename = (id: string, name: string) =>
    run(async () => {
      setRenaming(null);
      if (name.trim() && name.trim() !== folders.find((f) => f.id === id)?.name) {
        await sendJSON("PATCH", `/strategies/folders/${id}`, { name });
        await loadFolders();
      }
    });

  const remove = (f: StrategyFolder) =>
    run(async () => {
      if (!window.confirm(`Delete the folder "${f.name}"? Its ${f.count} strateg${f.count === 1 ? "y is" : "ies are"} kept and become ungrouped.`)) return;
      await del(`/strategies/folders/${f.id}`);
      await loadFolders();
      onChanged();
    });

  const move = (strategyName: string, folderId: string | null) =>
    run(async () => {
      await sendJSON("PUT", "/strategies/folder-assignment", { strategy_name: strategyName, folder_id: folderId });
      if (folderId) toggle(folderId, true);
      onChanged();
    });

  // ---- drag and drop ----
  const dropProps = (target: string | null) => ({
    onDragOver: (e: DragEvent) => {
      if (e.dataTransfer.types.includes(DRAG_TYPE)) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setOver(target ?? "none");
      }
    },
    onDragLeave: (e: DragEvent) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(null);
    },
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      setOver(null);
      const name = e.dataTransfer.getData(DRAG_TYPE);
      if (name) move(name, target);
    },
  });

  const row = (s: SavedStrategy) => (
    <li
      key={s.id}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_TYPE, s.name);
        e.dataTransfer.effectAllowed = "move";
      }}
      onDragEnd={() => setOver(null)}
      className={`group flex items-center border-b border-ink-800/60 hover:bg-ink-850 ${current?.name === s.name ? "bg-ink-850" : ""}`}
    >
      <span className="cursor-grab pl-2 text-ink-600 group-hover:text-ink-400" title="Drag onto a folder"><Icon d={I.grip} size={12} /></span>
      <button onClick={() => onOpen(s)} className="flex min-w-0 flex-1 items-center justify-between gap-2 px-2 py-2 text-left">
        <span className="truncate">{s.name}</span>
        <span className="num shrink-0 text-[10px] text-ink-400">v{s.version}</span>
      </button>
      <select
        value={s.folder_id ?? ""}
        onChange={(e) => move(s.name, e.target.value || null)}
        aria-label={`Move ${s.name} to a folder`}
        title="Move to…"
        className="mr-1 w-5 cursor-pointer appearance-none rounded bg-transparent py-1 text-center text-[11px] text-ink-400 opacity-0 hover:text-ink-100 focus:opacity-100 group-hover:opacity-100"
      >
        <option value="">Ungrouped</option>
        {folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
      </select>
    </li>
  );

  const ungrouped = saved.filter((s) => !s.folder_id || !folders.some((f) => f.id === s.folder_id));

  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900 text-xs">
      <div className="flex items-center gap-2 border-b border-ink-800 px-3 py-2">
        <h2 className="flex-1 text-sm font-semibold">Saved strategies</h2>
        <button onClick={() => { setCreating(true); setDraft(""); }} title="New folder" aria-label="New folder"
          className="flex items-center gap-1 rounded-md border border-ink-700 px-2 py-1 text-ink-300 hover:text-ink-100">
          <Icon d={I.folderPlus} /> Folder
        </button>
      </div>

      {error && <p className="border-b border-ink-800 px-3 py-2 text-down" role="alert">{error}</p>}

      {creating && (
        <form className="flex gap-1 border-b border-ink-800 p-2" onSubmit={(e) => { e.preventDefault(); create(); }}>
          <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setCreating(false)}
            onBlur={() => !draft.trim() && setCreating(false)} maxLength={60} placeholder="Folder name…"
            className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-850 px-2 py-1 text-ink-100 outline-none focus:border-accent" />
          <button className="rounded bg-accent px-2 font-semibold text-ink-950">Create</button>
        </form>
      )}

      {saved.length === 0 && folders.length === 0 && <p className="px-3 py-3 text-ink-400">None yet.</p>}

      {folders.map((f) => {
        const items = saved.filter((s) => s.folder_id === f.id);
        const expanded = open[f.id] ?? true;
        return (
          <div key={f.id} {...dropProps(f.id)} className={over === f.id ? "bg-accent/10 ring-1 ring-inset ring-accent" : ""}>
            <div className="group flex items-center gap-1 border-b border-ink-800 bg-ink-850/60 px-2 py-1.5">
              <button onClick={() => toggle(f.id)} aria-expanded={expanded} aria-label={`${expanded ? "Collapse" : "Expand"} ${f.name}`} className="text-ink-400 hover:text-ink-100">
                <span className={`inline-block transition-transform ${expanded ? "rotate-90" : ""}`}><Icon d={I.chevron} size={12} /></span>
              </button>
              <Icon d={I.folder} size={14} />
              {renaming === f.id ? (
                <input autoFocus defaultValue={f.name} maxLength={60}
                  onKeyDown={(e) => { if (e.key === "Enter") rename(f.id, e.currentTarget.value); if (e.key === "Escape") setRenaming(null); }}
                  onBlur={(e) => rename(f.id, e.currentTarget.value)}
                  className="min-w-0 flex-1 rounded border border-accent bg-ink-850 px-1.5 py-0.5 text-ink-100 outline-none" />
              ) : (
                <button onClick={() => toggle(f.id)} onDoubleClick={() => setRenaming(f.id)} title="Double-click to rename"
                  className="min-w-0 flex-1 truncate text-left font-medium text-ink-100">{f.name}</button>
              )}
              <span className="num text-[10px] text-ink-400">{items.length}</span>
              <button onClick={() => setRenaming(f.id)} aria-label={`Rename ${f.name}`} className="hidden rounded p-1 text-ink-400 hover:bg-ink-700 hover:text-ink-100 group-hover:block"><Icon d={I.pencil} size={12} /></button>
              <button onClick={() => remove(f)} aria-label={`Delete ${f.name}`} className="hidden rounded p-1 text-ink-400 hover:bg-ink-700 hover:text-down group-hover:block"><Icon d={I.trash} size={12} /></button>
            </div>
            {expanded && (
              <ul className="pl-3">
                {items.map(row)}
                {items.length === 0 && <li className="border-b border-ink-800/60 px-3 py-2 text-ink-400">Drag strategies here.</li>}
              </ul>
            )}
          </div>
        );
      })}

      {(folders.length > 0 || ungrouped.length > 0) && (
        <div {...dropProps(null)} className={over === "none" ? "bg-accent/10 ring-1 ring-inset ring-accent" : ""}>
          {folders.length > 0 && <div className="border-b border-ink-800 px-3 py-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-400">Ungrouped</div>}
          <ul>
            {ungrouped.map(row)}
            {ungrouped.length === 0 && folders.length > 0 && <li className="px-3 py-2 text-ink-400">Drop here to remove from a folder.</li>}
          </ul>
        </div>
      )}
    </section>
  );
}
