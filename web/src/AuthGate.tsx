import type { ReactNode } from "react";
import { useAuth } from "./auth";
import { LoginPage } from "./pages/LoginPage";

/** Shows the login screen when the server requires an account and there is no session; otherwise the app. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  if (me === null) return <div className="grid min-h-dvh place-items-center text-sm text-ink-400">Loading…</div>;
  if (me.auth_enabled && !me.signed_in) return <LoginPage />;
  return <>{children}</>;
}
