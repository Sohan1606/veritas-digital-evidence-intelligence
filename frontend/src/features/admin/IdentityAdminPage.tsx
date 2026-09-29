import { useState } from "react";
import type { FormEvent } from "react";
import { apiMutation, ApiError } from "../../api/client";
import type { IdentityRole, IdentityUser, ListResponse } from "../../api/types";
import { useResource } from "../../api/useResource";
import { Button, DataTable, PageHeader, Panel, StateView } from "../../design-system";

export function IdentityAdminPage() {
  const users = useResource<ListResponse<IdentityUser>>("/api/v1/admin/users");
  const roles = useResource<ListResponse<IdentityRole>>("/api/v1/admin/roles");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setNotice(null);
    try {
      await action();
      setNotice("Identity change recorded.");
      users.reload();
    } catch (error) {
      setNotice(error instanceof ApiError ? error.message : "Identity change failed.");
    } finally {
      setBusy(false);
    }
  }

  async function assign(event: FormEvent<HTMLFormElement>, user: IdentityUser) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await run(() => apiMutation("POST", `/api/v1/admin/users/${user.id}/roles`, {
      role_id: String(form.get("role")), case_id: String(form.get("case_id")),
    }));
  }

  const assignable = roles.status === "ready" ? roles.data.items.filter((role) => role.assignable_in_console) : [];

  return (
    <>
      <PageHeader eyebrow="Organization access" title="Identity management" description="Review provisioned Users, case-scoped role assignments, and account status. Privileged Administrator and Auditor assignments require out-of-band operator provisioning." />
      {notice && <p role="status" className="mb-4 border-l-2 border-signal bg-ink-850 px-3 py-2 text-sm text-fg-muted">{notice}</p>}
      {users.status === "loading" && <StateView state="loading" title="Loading provisioned Users…" />}
      {users.status === "error" && <StateView state="error" title="User directory unavailable" action={<Button onClick={users.reload}>Retry</Button>}>{users.error.message}</StateView>}
      {users.status === "ready" && users.data.count === 0 && <StateView state="empty" title="No Users are provisioned." />}
      {users.status === "ready" && users.data.count > 0 && (
        <Panel>
          <DataTable
            caption="Users and access assignments"
            rows={users.data.items}
            rowKey={(user) => user.id}
            columns={[
              { key: "identity", header: "User", render: (user) => <div><span className="text-fg">{user.display_name}</span><span className="mt-0.5 block mono-id text-fg-subtle">{user.id} · {user.username}</span></div> },
              { key: "status", header: "Status", render: (user) => <span className={user.status === "active" ? "text-ok" : "text-risk"}>{user.status}</span> },
              { key: "roles", header: "Roles / case scope", render: (user) => <div className="space-y-1">{user.case_assignments.length ? user.case_assignments.map((assignment) => <div key={`${assignment.role_id}-${assignment.case_id}`} className="flex flex-wrap items-center gap-2"><span className="mono-id text-fg-muted">{assignment.role}{assignment.case_id ? ` · ${assignment.case_id}` : " · Organization"}</span>{assignment.case_id && <button type="button" disabled={busy} onClick={() => void run(() => apiMutation("DELETE", `/api/v1/admin/users/${user.id}/roles/${assignment.role_id}?case_id=${assignment.case_id}`))} className="text-xs text-fg-subtle underline hover:text-risk">Remove</button>}</div>) : <span className="text-fg-subtle">No assignments</span>}</div> },
              { key: "actions", header: "Access", render: (user) => <div className="min-w-52 space-y-2">{user.status === "active" ? <Button size="sm" variant="quiet" disabled={busy} onClick={() => void run(() => apiMutation("PATCH", `/api/v1/admin/users/${user.id}/status`, { status: "disabled" }))}>Disable</Button> : <Button size="sm" variant="quiet" disabled={busy} onClick={() => void run(() => apiMutation("PATCH", `/api/v1/admin/users/${user.id}/status`, { status: "active" }))}>Enable</Button>}
                <form onSubmit={(event) => void assign(event, user)} className="flex flex-wrap gap-1.5">
                  <label className="sr-only" htmlFor={`role-${user.id}`}>Role for {user.display_name}</label>
                  <select id={`role-${user.id}`} name="role" required className="h-8 max-w-32 rounded-sm border border-line bg-ink-950 px-2 text-xs text-fg">{assignable.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}</select>
                  <label className="sr-only" htmlFor={`case-${user.id}`}>Case identifier for {user.display_name}</label>
                  <input id={`case-${user.id}`} name="case_id" required pattern="CASE-[0-9]{3,9}" defaultValue="CASE-001" className="h-8 w-24 rounded-sm border border-line bg-ink-950 px-2 font-mono text-xs text-fg" />
                  <Button size="sm" type="submit" disabled={busy || assignable.length === 0}>Assign</Button>
                </form>
              </div> },
            ]}
          />
        </Panel>
      )}
      {roles.status === "ready" && <p className="mt-3 text-xs leading-relaxed text-fg-subtle">Assignable roles are scoped to the entered Case. Administrator and Auditor grants are intentionally excluded from this console.</p>}
    </>
  );
}
