/** The landing page is static HTML; once loaded it asks the API who is signed in and, if someone is, swaps the
 *  header's "Sign in" button for their avatar chip (name, plan, status) with a small menu. Signed out, or if the API
 *  cannot be reached, the page stays as it is. Plain DOM: no framework on this page. */

interface Me {
  signed_in: boolean;
  auth_enabled: boolean;
  name?: string;
  email?: string;
}
interface Profile {
  name: string;
  email: string | null;
  phone: string | null;
  plan: { name: string };
  status: string;
}

const STATUS: Record<string, [string, string]> = {
  trial: ["Free trial", "lp-st-amber"],
  active: ["Active", "lp-st-green"],
  ending: ["Not renewing", "lp-st-amber"],
  lapsed: ["Expired", "lp-st-red"],
  owner: ["Owner", "lp-st-violet"],
};

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

async function get<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`/api${path}`, { credentials: "same-origin" });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

export async function showAccount() {
  const btn = document.querySelector<HTMLAnchorElement>("[data-account]");
  if (!btn) return;
  const me = await get<Me>("/auth/me");
  if (!me?.signed_in) return;
  const foot = document.querySelector<HTMLAnchorElement>("[data-account-foot]");
  if (!me.auth_enabled) {
    // single-user server: nobody to show, just a direct way in
    btn.textContent = "Open the app";
    if (foot) foot.textContent = "Open the app";
    return;
  }
  const p = await get<Profile>("/auth/profile");
  const name = p?.name || me.name || me.email || "Account";
  const contact = p?.email ?? p?.phone ?? me.email ?? "";
  const [label, tone] = STATUS[p?.status ?? "trial"] ?? STATUS.trial;
  const plan = p?.plan.name ?? "";

  const wrap = document.createElement("div");
  wrap.className = "lp-acct";
  wrap.innerHTML =
    `<button type="button" class="lp-acct-chip" aria-haspopup="menu" aria-expanded="false" aria-label="Account menu">` +
    `<span class="lp-acct-av">${esc(name.slice(0, 1).toUpperCase())}</span>` +
    `<span class="lp-acct-txt"><b>${esc(name)}</b><small>${esc(plan)}</small></span>` +
    `<span class="lp-acct-dot ${tone}" title="${esc(label)}"></span></button>` +
    `<div class="lp-acct-menu" role="menu" hidden>` +
    `<p class="lp-acct-who"><b>${esc(name)}</b><span>${esc(contact)}</span></p>` +
    `<p class="lp-acct-plan"><span class="lp-acct-pill ${tone}">${esc(label)}</span>${esc(plan)}</p>` +
    `<a role="menuitem" href="/app/#/strategies" class="lp-acct-primary">Open the app</a>` +
    `<a role="menuitem" href="/app/#/profile">Profile and security</a>` +
    `<a role="menuitem" href="/app/#/pricing">Plan and usage</a>` +
    `<button type="button" role="menuitem" data-signout>Sign out</button></div>`;
  btn.replaceWith(wrap);
  if (foot) foot.textContent = "Open the app";

  const chip = wrap.querySelector<HTMLButtonElement>(".lp-acct-chip")!;
  const menu = wrap.querySelector<HTMLElement>(".lp-acct-menu")!;
  const setOpen = (open: boolean) => {
    menu.hidden = !open;
    chip.setAttribute("aria-expanded", String(open));
  };
  chip.addEventListener("click", () => setOpen(menu.hidden));
  document.addEventListener("mousedown", (e) => !wrap.contains(e.target as Node) && setOpen(false));
  document.addEventListener("keydown", (e) => e.key === "Escape" && setOpen(false));
  wrap.querySelector("[data-signout]")!.addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => undefined);
    location.reload();
  });
}
