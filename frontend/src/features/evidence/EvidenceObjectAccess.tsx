import { useState } from "react";
import { ApiError, apiDownload, apiMutation } from "../../api/client";
import type { EvidenceIntegrityVerification, EvidenceObject } from "../../api/types";
import { Button, INTEGRITY_RESULT, StatusBadge, Tag, Timestamp } from "../../design-system";

export const NOT_AUTHENTICITY = "Integrity verification is not an authenticity determination.";

/** How long the transient object URL used to hand the bytes to the browser stays valid. */
const OBJECT_URL_LIFETIME_MS = 10_000;

type RetrieveState =
  | { phase: "ready" }
  | { phase: "retrieving" }
  | { phase: "completed"; filename: string }
  | { phase: "unavailable"; message: string; requestId: string | null };

type VerifyState =
  | { phase: "idle" }
  | { phase: "verifying" }
  | { phase: "result"; verification: EvidenceIntegrityVerification }
  | { phase: "failed"; message: string; requestId: string | null };

/** The visible retrieval states. */
const RETRIEVE_STATUS: Record<RetrieveState["phase"], string> = {
  ready: "Ready",
  retrieving: "Retrieving…",
  completed: "Completed",
  unavailable: "Retrieval unavailable",
};

/** apiMutation trusts the server's shape; a non-JSON 200 must not reach the renderer as a result. */
function isVerification(value: unknown): value is EvidenceIntegrityVerification {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.result === "string" &&
    Object.hasOwn(INTEGRITY_RESULT, record.result) &&
    typeof record.message === "string" &&
    typeof record.sha256 === "string" &&
    typeof record.sha512 === "string" &&
    typeof record.byte_size === "number"
  );
}

function describe(cause: unknown): { message: string; requestId: string | null } {
  return cause instanceof ApiError
    ? { message: cause.message, requestId: cause.requestId }
    : { message: "The operation could not be completed.", requestId: null };
}

/**
 * Hands the retrieved bytes to the browser's own download mechanism. The Blob exists only for
 * the duration of this call plus a short-lived object URL: it is never put in component state.
 */
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  link.style.display = "none";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), OBJECT_URL_LIFETIME_MS);
}

function RequestRef({ requestId }: { requestId: string | null }) {
  if (!requestId) return null;
  return <span className="text-fg-subtle"> · Request <span className="mono-id">{requestId}</span></span>;
}

function Comparison({ verification: v }: { verification: EvidenceIntegrityVerification }) {
  const rows = [
    {
      label: "Byte size",
      recorded: v.expected_byte_size?.toLocaleString(),
      computed: v.computed_byte_size?.toLocaleString(),
      differs: v.expected_byte_size !== v.computed_byte_size,
    },
    { label: "SHA-256", recorded: v.expected_sha256, computed: v.computed_sha256, differs: v.expected_sha256 !== v.computed_sha256 },
    { label: "SHA-512", recorded: v.expected_sha512, computed: v.computed_sha512, differs: v.expected_sha512 !== v.computed_sha512 },
  ];
  return (
    <dl className="space-y-3 rounded border border-line bg-ink-900/40 p-3 text-xs">
      {rows.map((row) => (
        <div key={row.label}>
          <dt className="flex items-center gap-2 text-fg-subtle">
            {row.label}
            <Tag tone={row.differs ? "warn" : "ok"}>{row.differs ? "Differs" : "Equal"}</Tag>
          </dt>
          <dd className="mt-1 space-y-0.5">
            <div><span className="text-fg-subtle">Recorded at intake · </span><span className="mono-id break-all text-fg">{row.recorded}</span></div>
            <div><span className="text-fg-subtle">Recomputed now · </span><span className="mono-id break-all text-fg">{row.computed}</span></div>
          </dd>
        </div>
      ))}
    </dl>
  );
}

function VerificationResult({ verification: v }: { verification: EvidenceIntegrityVerification }) {
  return (
    <div role="status" className="space-y-2 rounded border border-line p-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge semantics={INTEGRITY_RESULT[v.result]} />
        <span className="text-xs text-fg-subtle">
          Verification run <Timestamp iso={v.verified_at} /> by <span className="mono-id text-fg-muted">{v.verified_by}</span>
        </span>
      </div>
      <p className="text-xs text-fg-muted">{v.message}</p>
      {v.result === "MATCH" && (
        <dl className="space-y-2 rounded border border-line bg-ink-900/40 p-3 text-xs">
          <div><dt className="text-fg-subtle">Byte size · recomputed and equal to the recorded value</dt><dd className="mono-id text-fg">{v.byte_size.toLocaleString()}</dd></div>
          <div><dt className="text-fg-subtle">SHA-256 · recomputed and equal to the recorded value</dt><dd className="mono-id break-all text-fg">{v.sha256}</dd></div>
          <div><dt className="text-fg-subtle">SHA-512 · recomputed and equal to the recorded value</dt><dd className="mono-id break-all text-fg">{v.sha512}</dd></div>
        </dl>
      )}
      {v.result === "MISMATCH" && (
        <>
          <Comparison verification={v} />
          <p className="text-xs text-fg-subtle">
            The recomputed values differ from the values recorded at intake. Verification reports the difference only; it does not
            determine why. The recorded values are unchanged and retrieval remains available.
          </p>
        </>
      )}
      {v.result === "UNAVAILABLE" && (
        <p className="text-xs text-fg-subtle">This is not a mismatch: nothing about the stored bytes was established. The recorded values are unchanged.</p>
      )}
    </div>
  );
}

/** Retrieve and independently verify the preserved bytes of one PRESERVED EvidenceObject. */
export function PreservedObjectAccess({ caseId, item }: { caseId: string; item: EvidenceObject }) {
  const base = `/api/v1/cases/${caseId}/evidence/${item.evidence_id}/objects/${item.id}`;
  const [retrieve, setRetrieve] = useState<RetrieveState>({ phase: "ready" });
  const [verify, setVerify] = useState<VerifyState>({ phase: "idle" });

  async function onRetrieve() {
    setRetrieve({ phase: "retrieving" });
    try {
      const { blob, filename } = await apiDownload(`${base}/content`);
      const name = filename ?? `${item.id}.bin`;
      saveBlob(blob, name);
      setRetrieve({ phase: "completed", filename: name });
    } catch (cause) {
      setRetrieve({ phase: "unavailable", ...describe(cause) });
    }
  }

  async function onVerify() {
    setVerify({ phase: "verifying" });
    try {
      const verification = await apiMutation<unknown>("POST", `${base}/verify`);
      if (!isVerification(verification)) {
        setVerify({ phase: "failed", message: "The server returned an unexpected verification response.", requestId: null });
        return;
      }
      setVerify({ phase: "result", verification });
    } catch (cause) {
      setVerify({ phase: "failed", ...describe(cause) });
    }
  }

  return (
    <div role="group" aria-label={`Preserved bytes of ${item.id}`} className="space-y-3 border-t border-line pt-3">
      <div>
        <h4 className="text-xs font-semibold text-fg">Retrieve and verify</h4>
        <p className="mt-1 text-xs text-fg-subtle">{NOT_AUTHENTICITY} Each retrieval and each verification is recorded in the audit trail.</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" disabled={retrieve.phase === "retrieving"} onClick={() => void onRetrieve()}>
          Retrieve
        </Button>
        <span role="status" className={retrieve.phase === "unavailable" ? "text-xs text-risk" : "text-xs text-fg-muted"}>
          {RETRIEVE_STATUS[retrieve.phase]}
        </span>
      </div>
      {retrieve.phase === "completed" && (
        <p className="text-xs text-fg-subtle">
          Saved as <span className="mono-id text-fg-muted">{retrieve.filename}</span>
        </p>
      )}
      {retrieve.phase === "unavailable" && (
        <p role="alert" className="text-xs text-risk">
          {retrieve.message}
          <RequestRef requestId={retrieve.requestId} />
        </p>
      )}
      <div>
        <Button size="sm" variant="quiet" disabled={verify.phase === "verifying"} onClick={() => void onVerify()}>
          {verify.phase === "verifying" ? "Verifying…" : "Verify"}
        </Button>
      </div>
      {verify.phase === "result" && <VerificationResult verification={verify.verification} />}
      {verify.phase === "failed" && (
        <p role="alert" className="text-xs text-risk">
          Verification could not be completed. {verify.message}
          <RequestRef requestId={verify.requestId} />
        </p>
      )}
    </div>
  );
}
