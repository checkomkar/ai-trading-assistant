import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "./auth";
import { LoginPage } from "./pages/LoginPage";
import { PrivacyPage, TermsPage } from "./pages/LegalPage";

/** Shows the login screen when the server requires an account and there is no session; otherwise the app. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  if (me === null) return <div className="grid min-h-dvh place-items-center text-sm text-ink-400">Loading…</div>;
  // The legal pages are readable without an account (the sign-up form links to them).
  if (hash === "#/terms" || hash === "#/privacy") {
    return <div className="flex min-h-dvh flex-col">{hash === "#/terms" ? <TermsPage /> : <PrivacyPage />}</div>;
  }
  if (me.auth_enabled && !me.signed_in) return <LoginPage />;
  return <>{children}</>;
}
