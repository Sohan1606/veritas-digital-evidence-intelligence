import { useState } from "react";
import { ApiError, apiMutation } from "../../api/client";
import type { AnalysisRun, Evidence, ExaminationMethod, ListResponse, SystemInfo } from "../../api/types";
import { useResource } from "../../api/useResource";
import { ApiErrorView, Button, DemoNotice, PageHeader, Panel, PanelHeader, StateView } from "../../design-system";
import { useSession } from "../auth/AuthContext";
import { caseEyebrow, useCase } from "../cases/CaseLayout";
import { ReservedCapability } from "../reserved/ReservedCapability";
import { IntentKeys, eligibilityOf, methodRef } from "./examinationModel";
import { MethodPanel } from "./MethodPanel";
import { ObjectPicker, type PickedObject } from "./ObjectPicker";
import { RunsPanel } from "./RunsPanel";

const RECORD_TYPES: { term: string; detail: string }[] = [
  { term: "Observation", detail: "What a Method measured or a person noted about Evidence. An examination publishes these." },
  { term: "Finding", detail: "A reasoned statement, with its method and limitations, recorded by a person from Observations. Never created here." },
  { term: "Claim", detail: "A statement that is tested against Findings. Never created here." },
  { term: "Assessment", detail: "A person's evaluation of a Claim. VERITAS never authors Assessments." },
];

function RecordTypes() {
  return (
    <Panel labelledBy="record-types-title">
      <PanelHeader id="record-types-title" eyebrow="Review" title="Observation, Finding, Claim, Assessment" />
      <div className="space-y-3 px-4 py-4">
        <dl className="space-y-2 text-xs">
          {RECORD_TYPES.map((item) => (
            <div key={item.term}>
              <dt className="font-semibold text-fg">{item.term}</dt>
              <dd className="text-fg-muted">{item.detail}</dd>
            </div>
          ))}
        </dl>
        <p className="text-2xs text-fg-subtle">
          Turning Observations into Findings, and reviewing them, is a human step that is not part of this version. This page
          creates Observations only.
        </p>
      </div>
    </Panel>
  );
}

function StartPanel({
  caseId,
  method,
  picked,
  canExecute,
  demonstration,
  onStarted,
}: {
  caseId: string;
  method: ExaminationMethod | undefined;
  picked: PickedObject | null;
  canExecute: boolean;
  demonstration: boolean;
  onStarted: (run: AnalysisRun) => void;
}) {
  const [keys] = useState(() => new IntentKeys());
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ message: string; requestId: string | null } | null>(null);
  const eligibility = eligibilityOf({ canExecute, demonstration, method, evidence: picked?.evidence, object: picked?.object });

  async function start() {
    if (!eligibility.eligible || !method || !picked) return;
    const intent = `start:${methodRef(method)}:${picked.object.id}`;
    setBusy(true);
    setFailure(null);
    try {
      const run = await apiMutation<AnalysisRun>("POST", `/api/v1/cases/${caseId}/analysis-runs`, {
        evidence_id: picked.evidence.id,
        evidence_object_id: picked.object.id,
        method_key: method.key,
        method_version: method.version,
        parameters: {},
        idempotency_key: keys.keyFor(intent),
      });
      keys.settle(intent);
      onStarted(run);
    } catch (cause) {
      // A definite answer settles the intent. If the server could not be reached the outcome is
      // unknown, so the same key is kept and a second click can never start a second run.
      if (cause instanceof ApiError && cause.status !== null) keys.settle(intent);
      setFailure({
        message: cause instanceof ApiError ? cause.message : "The examination could not be started.",
        requestId: cause instanceof ApiError ? cause.requestId : null,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel labelledBy="start-title">
      <PanelHeader id="start-title" eyebrow="Examine" title="Start examination" />
      <div className="space-y-3 px-4 py-4">
        <p id="start-reason" role="status" className={`text-xs ${eligibility.eligible ? "text-fg-muted" : "text-fg-subtle"}`}>
          {eligibility.eligible && method && picked
            ? `Ready: ${methodRef(method)} will read the preserved bytes of ${picked.object.id} (${picked.evidence.id}) and publish Observations only.`
            : eligibility.eligible
              ? ""
              : eligibility.reason}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            disabled={!eligibility.eligible || busy}
            aria-describedby="start-reason"
            onClick={() => void start()}
          >
            {busy ? "Starting…" : "Start examination"}
          </Button>
          <span className="text-2xs text-fg-subtle">Every examination is recorded in the audit trail.</span>
        </div>
        {failure && (
          <p role="alert" className="text-xs text-risk">
            {failure.message}
            {failure.requestId && (
              <span className="text-fg-subtle"> · Request <span className="mono-id">{failure.requestId}</span></span>
            )}
          </p>
        )}
      </div>
    </Panel>
  );
}

/** The investigator's examination workstation: choose, examine, trace. */
export function ExaminationPage() {
  const c = useCase();
  const session = useSession();
  const system = useResource<SystemInfo>("/api/v1/system");
  const available =
    system.status === "ready" && system.data.capabilities.some((capability) => capability.key === "examination" && capability.status === "available");
  const permissions =
    session.state.status === "ready" ? (session.state.session.case_capabilities[c.id] ?? session.state.session.case_capabilities["*"] ?? []) : [];
  const canExecute = permissions.includes("examination:execute") && !c.demonstration;

  const base = `/api/v1/cases/${c.id}`;
  const methods = useResource<ListResponse<ExaminationMethod>>(available ? `${base}/examination/methods` : null);
  const evidence = useResource<ListResponse<Evidence>>(available && !c.demonstration ? `${base}/evidence` : null);
  const [chosenMethod, setChosenMethod] = useState<string | null>(null);
  const [picked, setPicked] = useState<PickedObject | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [focusedRun, setFocusedRun] = useState<string | null>(null);
  const selectRun = (id: string, focus = false) => {
    setSelectedRun(id);
    setFocusedRun(focus ? id : null);
  };

  const header = (
    <PageHeader
      eyebrow={caseEyebrow(c.id, "Examination")}
      title="Examination"
      description="Run a versioned, deterministic Method against the preserved bytes of one EvidenceObject. Each execution is an Analysis Run that records exactly which object, Method and version it used, and publishes Observations. A result is a measurement, not a conclusion."
    />
  );
  if (!available) {
    return (
      <>
        {header}
        <ReservedCapability capability="examination" subject="Examination" />
      </>
    );
  }

  const enabled = methods.status === "ready" ? methods.data.items.filter((method) => method.enabled) : [];
  const selectedMethod =
    methods.status === "ready"
      ? (methods.data.items.find((method) => methodRef(method) === chosenMethod) ?? (enabled.length === 1 ? enabled[0] : undefined))
      : undefined;

  return (
    <>
      {header}
      {c.demonstration && (
        <div className="mb-6">
          <DemoNotice compact />
        </div>
      )}
      <div className="grid gap-6 xl:grid-cols-2">
        <div className="min-w-0 space-y-6">
          {methods.status === "loading" && <StateView state="loading" title="Loading registered Methods…" />}
          {methods.status === "error" && <ApiErrorView error={methods.error} subject="Methods" onRetry={methods.reload} />}
          {methods.status === "ready" && methods.data.count === 0 && (
            <StateView state="empty" title="No Methods are registered." />
          )}
          {methods.status === "ready" && methods.data.count > 0 && (
            <MethodPanel
              methods={methods.data.items}
              selected={selectedMethod ? methodRef(selectedMethod) : null}
              onSelect={setChosenMethod}
            />
          )}
          {c.demonstration ? (
            <StateView state="unavailable" title="Demonstration cases never execute Methods.">
              Demonstration records have no stored evidence bytes. Methods can be read about here, and run only on a non-demonstration
              Case.
            </StateView>
          ) : (
            <>
              {evidence.status === "loading" && <StateView state="loading" title="Loading evidence…" />}
              {evidence.status === "error" && <ApiErrorView error={evidence.error} subject="Evidence" onRetry={evidence.reload} />}
              {evidence.status === "ready" && (
                <ObjectPicker caseId={c.id} evidence={evidence.data} picked={picked?.object.id ?? null} onPick={setPicked} />
              )}
            </>
          )}
          <StartPanel
            caseId={c.id}
            method={selectedMethod}
            picked={picked}
            canExecute={canExecute}
            demonstration={c.demonstration}
            onStarted={(run) => selectRun(run.id, true)}
          />
        </div>
        <div className="min-w-0 space-y-6">
          <RunsPanel
            caseId={c.id}
            canExecute={canExecute}
            selected={selectedRun}
            focusSelected={focusedRun !== null && focusedRun === selectedRun}
            onSelect={selectRun}
          />
          <RecordTypes />
        </div>
      </div>
    </>
  );
}
