import { useState, type FormEvent } from "react";
import { apiMutation, apiUpload, ApiError } from "../../api/client";
import type {
  Evidence,
  EvidenceCustodyEvent,
  EvidenceDetail,
  EvidenceObject,
  EvidenceType,
  ListResponse,
} from "../../api/types";
import { useResource } from "../../api/useResource";
import { Button, Panel, RefId, StateView, Tag, Timestamp } from "../../design-system";

const EVIDENCE_TYPES: EvidenceType[] = ["image", "video", "audio", "document", "email", "message_export", "other"];
const FIELD_CLASS = "mt-1.5 min-h-10 w-full rounded-sm border border-line bg-ink-950 px-3 py-2 text-xs text-fg outline-none focus:border-signal";

function failureMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "The evidence operation could not be completed.";
}

function evidencePath(caseId: string, evidenceId: string) {
  return `/api/v1/cases/${caseId}/evidence/${evidenceId}`;
}

async function uploadAndMaybeFinalize(
  caseId: string,
  evidenceId: string,
  item: EvidenceObject,
  file: File,
  canFinalize: boolean,
): Promise<EvidenceObject> {
  const base = `${evidencePath(caseId, evidenceId)}/objects/${item.id}`;
  const uploaded = await apiUpload<EvidenceObject>(`${base}/content`, file);
  if (!canFinalize) return uploaded;
  return apiMutation<EvidenceObject>("POST", `${base}/finalize`);
}

export function EvidenceIntakeForm({
  caseId,
  canIntake,
  canFinalize,
  onCreated,
  onChanged,
}: {
  caseId: string;
  canIntake: boolean;
  canFinalize: boolean;
  onCreated: (evidenceId: string) => void;
  onChanged: () => void;
}) {
  const [label, setLabel] = useState("");
  const [evidenceType, setEvidenceType] = useState<EvidenceType>("document");
  const [description, setDescription] = useState("");
  const [declaredMediaType, setDeclaredMediaType] = useState("text/plain");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!canIntake) return null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError("Select a file before registering evidence.");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    let createdEvidenceId: string | null = null;
    try {
      const detail = await apiMutation<EvidenceDetail>("POST", `/api/v1/cases/${caseId}/evidence/intake`, {
        label: label.trim() || file.name,
        evidence_type: evidenceType,
        description: description.trim() || null,
        original_filename: file.name,
        declared_media_type: declaredMediaType.trim().toLowerCase(),
      });
      createdEvidenceId = detail.evidence.id;
      onCreated(detail.evidence.id);
      onChanged();
      const initial = detail.objects[0];
      if (!initial) throw new Error("The server did not return the quarantined evidence object.");
      const finalized = await uploadAndMaybeFinalize(caseId, detail.evidence.id, initial, file, canFinalize);
      setMessage(
        finalized.state === "PRESERVED"
          ? `${finalized.id} passed basic signature validation and was preserved.`
          : `${finalized.id} is uploaded in quarantine and awaits Custodian finalization.`,
      );
      setLabel("");
      setDescription("");
      setFile(null);
      onChanged();
    } catch (cause) {
      setError(failureMessage(cause));
      if (createdEvidenceId) onCreated(createdEvidenceId);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel labelledBy="evidence-intake-title" as="section" className="mb-5">
      <header className="border-b border-line px-4 py-3">
        <div className="eyebrow">Authorized intake</div>
        <h2 id="evidence-intake-title" className="mt-1 text-sm font-semibold text-fg">Register and stream evidence</h2>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-fg-subtle">
          File bytes stream to private quarantine. Filename and media type are submitter-declared metadata;
          hashes and the coarse detected type are server-computed. No file preview or download is provided.
        </p>
      </header>
      <form onSubmit={(event) => void submit(event)} className="grid gap-3 px-4 py-4 md:grid-cols-2">
        <label className="grid gap-1 text-xs text-fg-muted">
          Evidence label
          <input required maxLength={200} value={label} onChange={(event) => setLabel(event.target.value)} className={FIELD_CLASS} placeholder="Case item label" />
        </label>
        <label className="grid gap-1 text-xs text-fg-muted">
          Logical evidence type
          <select value={evidenceType} onChange={(event) => setEvidenceType(event.target.value as EvidenceType)} className={FIELD_CLASS}>
            {EVIDENCE_TYPES.map((type) => <option key={type} value={type}>{type.replaceAll("_", " ")}</option>)}
          </select>
        </label>
        <label className="grid gap-1 text-xs text-fg-muted">
          Declared media type
          <input required maxLength={127} value={declaredMediaType} onChange={(event) => setDeclaredMediaType(event.target.value)} className={`${FIELD_CLASS} font-mono`} placeholder="application/pdf" />
        </label>
        <label className="grid gap-1 text-xs text-fg-muted">
          File
          <input
            required
            type="file"
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null;
              setFile(selected);
              if (selected && !label) setLabel(selected.name);
              if (selected?.type) setDeclaredMediaType(selected.type.toLowerCase());
            }}
            className={`${FIELD_CLASS} file:mr-3 file:rounded-sm file:border-0 file:bg-ink-700 file:px-2 file:py-1 file:text-xs file:text-fg`}
          />
        </label>
        <label className="grid gap-1 text-xs text-fg-muted md:col-span-2">
          Description <span className="text-fg-faint">(optional)</span>
          <textarea maxLength={2000} value={description} onChange={(event) => setDescription(event.target.value)} className={`${FIELD_CLASS} min-h-16 resize-y`} />
        </label>
        {error && <p role="alert" className="text-xs text-risk md:col-span-2">{error}</p>}
        {message && <p role="status" className="text-xs text-fg-muted md:col-span-2">{message}</p>}
        <div className="flex flex-wrap items-center gap-3 md:col-span-2">
          <Button type="submit" disabled={busy || !file}>{busy ? "Streaming…" : "Register & upload"}</Button>
          <span className="text-xs text-fg-subtle">
            {canFinalize ? "This role can finalize a validated object." : "Preservation requires a Custodian with custody:write."}
          </span>
        </div>
      </form>
    </Panel>
  );
}

export function EvidenceObjectsPanel({
  caseId,
  evidence,
  permissions,
  onChanged,
}: {
  caseId: string;
  evidence: Evidence;
  permissions: string[];
  onChanged: () => void;
}) {
  const detail = useResource<EvidenceDetail>(`${evidencePath(caseId, evidence.id)}/intake`);
  const canIntake = permissions.includes("evidence:intake");
  const canFinalize = canIntake && permissions.includes("custody:write");
  const canReadCustody = permissions.includes("custody:read");

  return (
    <section aria-labelledby="stored-objects-title" className="mt-5 space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="eyebrow">EvidenceObject</div>
          <h2 id="stored-objects-title" className="mt-1 text-sm font-semibold text-fg">Intake, integrity & custody</h2>
        </div>
        <RefId id={evidence.id} />
      </header>
      {detail.status === "loading" && <StateView state="loading" title="Loading stored-object records…" />}
      {detail.status === "error" && <div role="alert" className="rounded border border-risk/30 p-3 text-xs text-risk">{detail.error.message}</div>}
      {detail.status === "ready" && detail.data.objects.length === 0 && (
        <p className="surface px-4 py-3 text-xs text-fg-subtle">No stored acquisitions are registered for this logical Evidence item.</p>
      )}
      {detail.status === "ready" && detail.data.objects.map((item) => (
        <EvidenceObjectCard
          key={item.id}
          caseId={caseId}
          item={item}
          canIntake={canIntake}
          canFinalize={canFinalize}
          canReadCustody={canReadCustody}
          onChanged={() => {
            detail.reload();
            onChanged();
          }}
        />
      ))}
      {detail.status === "ready" && canIntake && (
        <NewAcquisitionForm
          caseId={caseId}
          evidenceId={evidence.id}
          canFinalize={canFinalize}
          onChanged={() => {
            detail.reload();
            onChanged();
          }}
        />
      )}
    </section>
  );
}

function EvidenceObjectCard({
  caseId,
  item,
  canIntake,
  canFinalize,
  canReadCustody,
  onChanged,
}: {
  caseId: string;
  item: EvidenceObject;
  canIntake: boolean;
  canFinalize: boolean;
  canReadCustody: boolean;
  onChanged: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const custody = useResource<ListResponse<EvidenceCustodyEvent>>(
    canReadCustody ? `${evidencePath(caseId, item.evidence_id)}/objects/${item.id}/custody` : null,
  );
  const objectBase = `${evidencePath(caseId, item.evidence_id)}/objects/${item.id}`;

  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await uploadAndMaybeFinalize(caseId, item.evidence_id, item, file, canFinalize);
      setFile(null);
      onChanged();
    } catch (cause) {
      setError(failureMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function finalize() {
    setBusy(true);
    setError(null);
    try {
      await apiMutation<EvidenceObject>("POST", `${objectBase}/finalize`);
      onChanged();
    } catch (cause) {
      setError(failureMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel labelledBy={`object-${item.id}`} as="article">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2"><RefId id={item.id} /><Tag tone={item.state === "PRESERVED" ? "ok" : item.state === "REJECTED" ? "risk" : "warn"}>{item.state}</Tag></div>
          <h3 id={`object-${item.id}`} className="mt-2 break-all font-mono text-xs text-fg">{item.original_filename}</h3>
          <p className="mt-1 text-xs text-fg-subtle">Declared media type: <span className="font-mono">{item.declared_media_type}</span> · {item.byte_size.toLocaleString()} bytes</p>
        </div>
        <span className="text-right text-2xs text-fg-subtle">Received <Timestamp iso={item.acquired_at} /><br />by <span className="mono-id">{item.acquired_by}</span></span>
      </header>
      <div className="space-y-3 px-4 py-4">
        {item.detected_media_type && <p className="text-xs text-fg-muted">Detected media type: <span className="font-mono text-fg">{item.detected_media_type}</span></p>}
        {item.validation_note && <p className={item.state === "REJECTED" ? "text-xs text-risk" : "text-xs text-fg-muted"}>{item.validation_note}</p>}
        {(item.sha256 || item.sha512) && (
          <dl className="space-y-2 rounded border border-line bg-ink-900/40 p-3 text-xs">
            <div><dt className="text-fg-subtle">SHA-256{item.state === "QUARANTINED" ? " · quarantined upload" : ""}</dt><dd className="mono-id break-all text-fg">{item.sha256}</dd></div>
            <div><dt className="text-fg-subtle">SHA-512{item.state === "QUARANTINED" ? " · quarantined upload" : ""}</dt><dd className="mono-id break-all text-fg">{item.sha512}</dd></div>
          </dl>
        )}
        {item.state === "PRESERVED" && item.preserved_at && (
          <p className="text-xs text-fg-subtle">Preserved <Timestamp iso={item.preserved_at} /> by <span className="mono-id text-fg-muted">{item.preserved_by}</span>. These hashes do not establish authenticity.</p>
        )}
        {item.state === "QUARANTINED" && item.upload_completed_at === null && canIntake && (
          <div className="flex flex-wrap items-end gap-2">
            <label className="grid gap-1 text-xs text-fg-muted">Select content for this registered object
              <input type="file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} className={FIELD_CLASS} />
            </label>
            <Button size="sm" disabled={busy || !file} onClick={() => void upload()}>{busy ? "Streaming…" : "Upload to quarantine"}</Button>
          </div>
        )}
        {item.state === "QUARANTINED" && item.upload_completed_at !== null && canFinalize && (
          <Button size="sm" disabled={busy} onClick={() => void finalize()}>{busy ? "Validating…" : "Validate & preserve"}</Button>
        )}
        {item.state === "QUARANTINED" && item.upload_completed_at !== null && !canFinalize && (
          <p className="text-xs text-fg-subtle">Awaiting a Custodian with <code>custody:write</code> to finalize.</p>
        )}
        {item.state === "REJECTED" && <p className="text-xs text-fg-subtle">This acquisition is closed. A new acquisition must create a new EvidenceObject.</p>}
        {error && <p role="alert" className="text-xs text-risk">{error}</p>}
        {canReadCustody && (
          <div className="border-t border-line pt-3">
            <h4 className="text-xs font-semibold text-fg">Custody history</h4>
            {custody.status === "loading" && <p className="mt-2 text-xs text-fg-subtle">Loading custody events…</p>}
            {custody.status === "error" && <p role="alert" className="mt-2 text-xs text-risk">{custody.error.message}</p>}
            {custody.status === "ready" && custody.data.items.length === 0 && <p className="mt-2 text-xs text-fg-subtle">No custody events recorded.</p>}
            {custody.status === "ready" && custody.data.items.length > 0 && (
              <ol className="mt-2 space-y-2">
                {custody.data.items.map((event) => (
                  <li key={event.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 text-xs">
                    <Tag tone={event.event_type === "PRESERVED" ? "ok" : "neutral"}>{event.event_type}</Tag>
                    <span className="text-fg-muted">{event.from_state ?? "new"} → {event.to_state}</span>
                    <span className="text-fg-subtle"><Timestamp iso={event.occurred_at} /> · {event.actor}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </div>
    </Panel>
  );
}

function NewAcquisitionForm({
  caseId,
  evidenceId,
  canFinalize,
  onChanged,
}: {
  caseId: string;
  evidenceId: string;
  canFinalize: boolean;
  onChanged: () => void;
}) {
  const [declaredMediaType, setDeclaredMediaType] = useState("text/plain");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const item = await apiMutation<EvidenceObject>("POST", `${evidencePath(caseId, evidenceId)}/objects`, {
        original_filename: file.name,
        declared_media_type: declaredMediaType.trim().toLowerCase(),
      });
      onChanged();
      const final = await uploadAndMaybeFinalize(caseId, evidenceId, item, file, canFinalize);
      setMessage(final.state === "PRESERVED" ? `${final.id} preserved.` : `${final.id} is quarantined and awaits Custodian finalization.`);
      setFile(null);
      onChanged();
    } catch (cause) {
      setError(failureMessage(cause));
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel labelledBy="new-acquisition-title" as="section">
      <form onSubmit={(event) => void submit(event)} className="grid gap-3 p-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
        <div className="sm:col-span-3">
          <div className="eyebrow">Re-acquisition</div>
          <h3 id="new-acquisition-title" className="mt-1 text-xs font-semibold text-fg">Add a new EvidenceObject</h3>
          <p className="mt-1 text-xs text-fg-subtle">A replacement or new acquisition is a separate immutable object; the logical Evidence record remains unchanged.</p>
        </div>
        <label className="grid gap-1 text-xs text-fg-muted">Declared media type
          <input required value={declaredMediaType} onChange={(event) => setDeclaredMediaType(event.target.value)} className={`${FIELD_CLASS} font-mono`} />
        </label>
        <label className="grid gap-1 text-xs text-fg-muted">File
          <input required type="file" onChange={(event) => {
            const selected = event.target.files?.[0] ?? null;
            setFile(selected);
            if (selected?.type) setDeclaredMediaType(selected.type.toLowerCase());
          }} className={FIELD_CLASS} />
        </label>
        <Button type="submit" disabled={busy || !file}>{busy ? "Streaming…" : "Add acquisition"}</Button>
        {message && <p role="status" className="text-xs text-fg-muted sm:col-span-3">{message}</p>}
        {error && <p role="alert" className="text-xs text-risk sm:col-span-3">{error}</p>}
      </form>
    </Panel>
  );
}
