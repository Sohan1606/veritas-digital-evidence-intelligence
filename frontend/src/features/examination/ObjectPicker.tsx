import { Link } from "react-router";
import type { Evidence, EvidenceDetail, EvidenceObject, ListResponse } from "../../api/types";
import { useResource } from "../../api/useResource";
import { ApiErrorView, EVIDENCE_TYPE, OBJECT_STATE_TONE, Panel, PanelHeader, RefId, StateView, Tag } from "../../design-system";
import { casePath } from "../../app-shell/navigation";
import { formatBytes } from "./examinationModel";

export interface PickedObject {
  evidence: Evidence;
  object: EvidenceObject;
}

function ObjectRow({
  evidence,
  object,
  picked,
  onPick,
}: {
  evidence: Evidence;
  object: EvidenceObject;
  picked: boolean;
  onPick: (value: PickedObject) => void;
}) {
  const selectable = object.state === "PRESERVED";
  const reasonId = `object-reason-${object.id}`;
  const hasDigests = object.sha256 !== null && object.sha512 !== null;
  // Only a PRESERVED object's digests are the record intake made; the others are upload digests.
  const integrity = !hasDigests
    ? "No integrity values recorded"
    : object.state === "PRESERVED"
      ? "SHA-256 and SHA-512 recorded at intake"
      : "SHA-256 and SHA-512 computed on the upload (not preserved)";
  return (
    <li className={`rounded-md border ${picked ? "border-line-strong bg-ink-750" : "border-line bg-ink-850"} ${selectable ? "" : "opacity-70"}`}>
      <label className={`flex items-start gap-3 p-3 ${selectable ? "cursor-pointer" : "cursor-not-allowed"}`}>
        <input
          type="radio"
          name="examination-object"
          value={object.id}
          checked={picked}
          disabled={!selectable}
          aria-describedby={selectable ? undefined : reasonId}
          onChange={() => onPick({ evidence, object })}
          className="mt-1 h-4 w-4 accent-[var(--color-signal)]"
        />
        <span className="min-w-0 flex-1 space-y-1.5">
          <span className="flex flex-wrap items-center gap-2">
            <RefId id={evidence.id} />
            <RefId id={object.id} />
            <Tag tone={OBJECT_STATE_TONE[object.state]}>{object.state}</Tag>
          </span>
          <span className="block break-all font-mono text-xs text-fg">{object.original_filename}</span>
          <span className="block text-2xs text-fg-subtle">
            {EVIDENCE_TYPE[evidence.evidence_type].label} · {formatBytes(object.byte_size)} · {integrity}
          </span>
          {!selectable && (
            <span id={reasonId} className="block text-2xs text-warn">
              Only PRESERVED objects can be examined.
            </span>
          )}
        </span>
      </label>
    </li>
  );
}

function EvidenceGroup({
  caseId,
  evidence,
  picked,
  onPick,
}: {
  caseId: string;
  evidence: Evidence;
  picked: string | null;
  onPick: (value: PickedObject) => void;
}) {
  const detail = useResource<EvidenceDetail>(`/api/v1/cases/${caseId}/evidence/${evidence.id}/intake`);
  return (
    <section aria-label={`${evidence.id} ${evidence.label}`} className="space-y-2">
      <h3 className="flex flex-wrap items-baseline gap-2 text-xs font-semibold text-fg">
        <span className="break-all">{evidence.label}</span>
        <span className="mono-id font-normal text-fg-subtle">{evidence.id}</span>
      </h3>
      {detail.status === "loading" && <StateView state="loading" title={`Loading objects of ${evidence.id}…`} compact />}
      {detail.status === "error" && <ApiErrorView error={detail.error} subject={`Objects of ${evidence.id}`} onRetry={detail.reload} compact />}
      {detail.status === "ready" && detail.data.objects.length === 0 && (
        <p className="text-xs text-fg-subtle">{evidence.id} has no EvidenceObjects yet.</p>
      )}
      {detail.status === "ready" && detail.data.objects.length > 0 && (
        <ul className="space-y-2">
          {detail.data.objects.map((object) => (
            <ObjectRow key={object.id} evidence={evidence} object={object} picked={picked === object.id} onPick={onPick} />
          ))}
        </ul>
      )}
    </section>
  );
}

/** Choose the EvidenceObject to examine. An examination always names exactly one object. */
export function ObjectPicker({
  caseId,
  evidence,
  picked,
  onPick,
}: {
  caseId: string;
  evidence: ListResponse<Evidence>;
  picked: string | null;
  onPick: (value: PickedObject) => void;
}) {
  return (
    <Panel labelledBy="objects-title">
      <PanelHeader id="objects-title" eyebrow="Examine" title="EvidenceObject" />
      <fieldset className="space-y-4 px-4 py-4">
        <legend className="sr-only">EvidenceObject to examine</legend>
        {evidence.count === 0 && (
          <StateView state="empty" title={`No evidence registered in ${caseId}.`} compact>
            Register and preserve evidence first; only PRESERVED EvidenceObjects can be examined.
          </StateView>
        )}
        {evidence.items.map((item) => (
          <EvidenceGroup key={item.id} caseId={caseId} evidence={item} picked={picked} onPick={onPick} />
        ))}
        {evidence.count > 0 && (
          <p className="text-2xs text-fg-subtle">
            Integrity is verified on the{" "}
            <Link className="text-signal underline-offset-2 hover:underline" to={casePath(caseId, "evidence")}>
              Evidence page
            </Link>
            . An examination reports characteristics of the preserved bytes; it does not replace verification.
          </p>
        )}
      </fieldset>
    </Panel>
  );
}
