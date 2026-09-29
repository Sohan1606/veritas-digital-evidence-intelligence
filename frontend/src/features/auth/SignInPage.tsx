import { useState } from "react";
import type { FormEvent } from "react";
import { useSession } from "./AuthContext";
import { Button, Panel, VeritasMark } from "../../design-system";

export function SignInPage() {
  const { signIn } = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(username, password);
      setPassword("");
    } catch {
      setError("Sign-in failed. Check the credentials or contact your administrator.");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center bg-ink-950 px-4 py-10">
      <section className="w-full max-w-md" aria-labelledby="sign-in-title">
        <div className="mb-6 flex items-center gap-3 text-fg">
          <VeritasMark />
          <span className="font-mono text-sm tracking-[0.3em]">VERITAS</span>
        </div>
        <Panel className="p-6 sm:p-8">
          <p className="eyebrow">Provisioned identity</p>
          <h1 id="sign-in-title" className="mt-2 font-display text-3xl text-fg">Sign in</h1>
          <p className="mt-2 text-sm leading-relaxed text-fg-muted">
            Access is limited to accounts provisioned by your Organization. There is no public sign-up.
          </p>
          <form onSubmit={(event) => void submit(event)} className="mt-6 space-y-4">
            <label className="block text-sm text-fg-muted">
              Username
              <input autoComplete="username" required maxLength={128} value={username} onChange={(event) => setUsername(event.target.value)} className="mt-1.5 h-10 w-full rounded-sm border border-line bg-ink-950 px-3 text-fg outline-none focus:border-signal" />
            </label>
            <label className="block text-sm text-fg-muted">
              Password
              <input autoComplete="current-password" type="password" required maxLength={1024} value={password} onChange={(event) => setPassword(event.target.value)} className="mt-1.5 h-10 w-full rounded-sm border border-line bg-ink-950 px-3 text-fg outline-none focus:border-signal" />
            </label>
            {error && <p role="alert" className="rounded-sm border border-risk/40 bg-risk/10 px-3 py-2 text-sm text-risk">{error}</p>}
            <Button type="submit" disabled={busy} className="w-full justify-center">{busy ? "Verifying…" : "Continue"}</Button>
          </form>
          <p className="mt-5 border-t border-line pt-4 text-xs leading-relaxed text-fg-subtle">
            Accounts are verified by the server. Sessions expire and can be invalidated; credentials are never returned to the browser.
          </p>
        </Panel>
      </section>
    </main>
  );
}
