import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { getJSON, postJSON, type Me, type Usage } from "./api";

interface AuthState {
  me: Me | null; // null while the first check is running
  usage: Usage | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, name: string, inviteCode: string) => Promise<void>;
  signOut: () => Promise<void>;
  refreshUsage: () => void;
}

const Ctx = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);

  const loadMe = useCallback(
    () => getJSON<Me>("/auth/me").then(setMe).catch(() => setMe({ signed_in: false, auth_enabled: true })),
    [],
  );
  useEffect(() => {
    loadMe();
  }, [loadMe]);

  const refreshUsage = useCallback(() => {
    getJSON<Usage>("/billing/usage").then(setUsage).catch(() => setUsage(null));
  }, []);

  const signedIn = !!me?.signed_in;
  useEffect(() => {
    if (signedIn) refreshUsage();
    else setUsage(null);
  }, [signedIn, refreshUsage]);

  useEffect(() => {
    // Any successful AI/backtest call, and any quota refusal, makes the meter re-read (debounced).
    let t: ReturnType<typeof setTimeout> | undefined;
    const bump = () => {
      clearTimeout(t);
      t = setTimeout(refreshUsage, 400);
    };
    const unauth = () => setMe((m) => (m && m.auth_enabled && m.signed_in ? { ...m, signed_in: false } : m));
    window.addEventListener("striktek:usage", bump);
    window.addEventListener("striktek:quota", bump);
    window.addEventListener("striktek:unauth", unauth);
    return () => {
      clearTimeout(t);
      window.removeEventListener("striktek:usage", bump);
      window.removeEventListener("striktek:quota", bump);
      window.removeEventListener("striktek:unauth", unauth);
    };
  }, [refreshUsage]);

  const value: AuthState = {
    me,
    usage,
    refreshUsage,
    signIn: async (email, password) => {
      await postJSON("/auth/login", { email, password });
      await loadMe();
    },
    signUp: async (email, password, name, invite_code) => {
      await postJSON("/auth/signup", { email, password, name, invite_code });
      await loadMe();
    },
    signOut: async () => {
      await postJSON("/auth/logout", {});
      setUsage(null);
      await loadMe();
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
