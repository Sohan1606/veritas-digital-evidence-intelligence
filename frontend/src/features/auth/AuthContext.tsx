import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { apiGet, apiMutation, ApiError } from "../../api/client";
import { setResourceIdentityScope } from "../../api/useResource";
import type { SessionInfo } from "../../api/types";
import { clearActiveCase } from "../../app-shell/navigation";

export type AuthState =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "signed-out" }
  | { status: "ready"; session: SessionInfo };

interface AuthContextValue {
  state: AuthState;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  reload: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);
let bootstrap: Promise<SessionInfo> | null = null;

function asApiError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError("server", "Unexpected client error.", null, null, null);
}

function resourceScope(session: SessionInfo): string {
  if (session.authenticated) return `session:${session.session_id ?? session.user_id ?? "unknown"}`;
  return session.demonstration ? "demonstration" : "signed-out";
}

async function fetchSession(): Promise<SessionInfo> {
  if (!bootstrap) {
    bootstrap = apiGet<SessionInfo>("/api/v1/auth/session").finally(() => {
      bootstrap = null;
    });
  }
  return bootstrap;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const currentIdentity = useRef<string | null>(null);

  const applySession = useCallback((session: SessionInfo) => {
    const nextIdentity = resourceScope(session);
    if (currentIdentity.current !== null && currentIdentity.current !== nextIdentity) {
      clearActiveCase();
    }
    if (!session.authenticated && !session.demonstration) clearActiveCase();
    currentIdentity.current = nextIdentity;
    // Call before publishing the new session to the tree: no new principal can render
    // while the previous principal's cached GET responses are still reachable.
    setResourceIdentityScope(nextIdentity);
    setState(session.authenticated || session.demonstration ? { status: "ready", session } : { status: "signed-out" });
  }, []);

  const reload = useCallback(async () => {
    setState({ status: "loading" });
    try {
      applySession(await fetchSession());
    } catch (error) {
      currentIdentity.current = null;
      clearActiveCase();
      setResourceIdentityScope("session-unavailable");
      setState({ status: "error", error: asApiError(error) });
    }
  }, [applySession]);

  useEffect(() => {
    let active = true;
    // The shell stays unmounted during bootstrap. Isolate any public/showcase entries
    // before resolving a possibly authenticated session.
    setResourceIdentityScope("session-checking");
    fetchSession()
      .then((session) => {
        if (active) applySession(session);
      })
      .catch((error: unknown) => {
        if (active) {
          currentIdentity.current = null;
          clearActiveCase();
          setResourceIdentityScope("session-unavailable");
          setState({ status: "error", error: asApiError(error) });
        }
      });
    return () => {
      active = false;
    };
  }, [applySession]);

  const signIn = useCallback(async (username: string, password: string) => {
    clearActiveCase();
    currentIdentity.current = "authenticating";
    setResourceIdentityScope("authenticating");
    try {
      const session = await apiMutation<SessionInfo>("POST", "/api/v1/auth/login", { username, password });
      applySession(session);
    } catch (error) {
      currentIdentity.current = "signed-out";
      setResourceIdentityScope("signed-out");
      throw asApiError(error);
    }
  }, [applySession]);

  const signOut = useCallback(async () => {
    // Fail closed immediately, before awaiting the network. A new login always gets a
    // fresh resource namespace even if logout returns an error or an old request settles.
    clearActiveCase();
    currentIdentity.current = "signing-out";
    setResourceIdentityScope("signing-out");
    setState({ status: "loading" });
    try {
      await apiMutation<{ status: string }>("POST", "/api/v1/auth/logout");
      currentIdentity.current = "signed-out";
      setResourceIdentityScope("signed-out");
      setState({ status: "signed-out" });
    } catch (error) {
      currentIdentity.current = null;
      setResourceIdentityScope("session-unavailable");
      setState({ status: "error", error: asApiError(error) });
      throw asApiError(error);
    }
  }, []);

  const value = useMemo(() => ({ state, signIn, signOut, reload }), [state, signIn, signOut, reload]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useSession() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}
