import { useEffect, useRef, useState } from "react";
import { getJSON, type Profile } from "../api";
import { useAuth } from "../auth";
import { STATUS } from "../pages/ProfilePage";

/** Header: who is signed in, their plan and its status, AI generations left, and the account menu. */
export function UserMenu({ active }: { active: "pricing" | "profile" | null }) {
  const { me, usage, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const shut = () => setOpen(false);
    document.addEventListener("mousedown", close);
    window.addEventListener("hashchange", shut);
    return () => {
      document.removeEventListener("mousedown", close);
      window.removeEventListener("hashchange", shut);
    };
  }, []);
  useEffect(() => {
    if (me?.signed_in) getJSON<Profile>("/auth/profile").then(setProfile).catch(() => setProfile(null));
  }, [me?.signed_in, me?.id, usage?.plan.id]);

  if (!me?.signed_in) return null;
  const local = !me.auth_enabled;
  const name = local ? "Local" : me.name || me.email || "Account";
  const st = STATUS[profile?.status ?? (local ? "local" : "trial")] ?? STATUS.trial;
  const plan = local ? "No limits" : usage?.plan.name ?? profile?.plan.name ?? me.plan ?? "…";
  const gens = usage?.usage.generations;
  const low = gens && gens.remaining !== null && gens.remaining <= 1;
  return (
    <div className="flex items-center gap-2" ref={box}>
      {!local && gens && (
        <a href="#/pricing" title="AI strategy generations left in your plan"
          className={`hidden rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 sm:inline ${low ? "bg-down/10 text-down ring-down/40" : "bg-ink-800 text-ink-300 ring-ink-600"}`}>
          {gens.limit === null ? "Unlimited AI" : <><span className="num">{gens.remaining}</span> AI left</>}
        </a>
      )}
      <div className="relative">
        <button onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} aria-label="Account menu"
          className={`flex items-center gap-2 rounded-full py-0.5 pr-2.5 pl-0.5 ring-1 hover:bg-ink-800 ${active === "profile" ? "bg-ink-800 ring-ink-600" : "ring-ink-700"}`}>
          <span className="grid h-7 w-7 place-items-center rounded-full bg-gradient-to-br from-accent to-amber-600 text-xs font-bold uppercase text-ink-950">
            {name.slice(0, 1)}
          </span>
          <span className="hidden max-w-[140px] flex-col items-start leading-tight md:flex">
            <span className="w-full truncate text-left text-[11px] font-medium text-ink-100">{name}</span>
            <span className="text-[10px] text-ink-400">{plan}</span>
          </span>
          <span className={`h-1.5 w-1.5 rounded-full ${profile?.status === "lapsed" ? "bg-down" : profile?.status === "ending" || profile?.status === "trial" ? "bg-accent" : "bg-up"}`} title={st.label} />
        </button>
        {open && (
          <div role="menu" className="ov-pop absolute right-0 z-30 mt-2 w-64 rounded-lg border border-ink-700 bg-ink-900 p-1 text-xs shadow-xl">
            <div className="px-3 py-2.5">
              <p className="truncate font-medium text-ink-100">{local ? "Local user" : name}</p>
              {!local && <p className="truncate text-ink-400">{me.email}</p>}
              <div className="mt-2 flex items-center gap-2">
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1 ${st.tone}`}>{st.label}</span>
                <span className="text-ink-300">{plan}</span>
              </div>
              {profile && !local && <p className="mt-1.5 text-[11px] text-ink-500">{st.note(profile)}</p>}
            </div>
            <div className="my-1 border-t border-ink-800" />
            <a role="menuitem" href="#/profile" onClick={() => setOpen(false)} className="block rounded px-3 py-2 text-ink-100 hover:bg-ink-800">Profile and security</a>
            {!local && <a role="menuitem" href="#/pricing" onClick={() => setOpen(false)} className="block rounded px-3 py-2 text-ink-100 hover:bg-ink-800">Plan and usage</a>}
            {!local && <button role="menuitem" onClick={() => signOut()} className="block w-full rounded px-3 py-2 text-left text-ink-100 hover:bg-ink-800">Sign out</button>}
          </div>
        )}
      </div>
    </div>
  );
}
