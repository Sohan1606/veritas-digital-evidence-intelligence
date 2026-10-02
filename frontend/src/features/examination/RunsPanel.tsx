import { Fragment, useEffect, useRef, useState } from "react";
import { ApiError, apiMutation } from "../../api/client";
import type { AnalysisRun, AnalysisRunDetail, ListResponse } from "../../api/types";
import { useResource } from "../../api/useResource";
import {
  ApiErrorView,
  Button,
  DataTable,
  FactList,
  Panel,
  PanelHeader,
  RUN_STATE,
  RefId,
  StateView,
  StatusBadge,
  Tag,
  Timestamp,
} from "../../design-system";
import { casePath } from "../../app-shell/navigation";
import { IntentKeys, POLL_MS, canRequestCancel, isActive, isRetryable, methodRef } from "./examinationModel";

/**
 * A Method reference that may wrap after ".", "_" and "@" instead of forcing a narrow table column
 * wider than its panel. ``<wbr>`` adds break opportunities only; the text content is unchanged.
 */
function BreakableRef({ text }: { text: string }) {
  const parts = text.split(/(?<=[._@])/);
  return (
    <>
      {parts.map((part, index) => (
        <Fragment key={index}>
          {part}
          {index < parts.length - 1 && <wbr />}
        </Fragment>
      ))}
    </>
  );
}

function actionMessage(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : "The request could not be completed.";
}

/** Newest first. The API lists runs oldest first; the list is only re-ordered for display. */
export function RunList({
  runs,
  selected,
  onSelect,
}: {
  runs: AnalysisRun[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <DataTable
      caption="Analysis runs, newest first"
      rows={[...runs].reverse()}
      rowKey={(run) => run.id}
      isSelected={(run) => run.id === selected}
      columns={[
        {
          key: "run",
          header: "Run",
          render: (run) => (
            <div className="min-w-0 space-y-1">
              <button
                type="button"
                aria-pressed={run.id === selected}
                aria-label={`Open run ${run.id}`}
                onClick={() => onSelect(run.id)}
                className="mono-id text-signal underline-offset-2 hover:underline"
              >
                {run.id}
              </button>
              <span className="mono-id block break-words text-2xs text-fg-subtle">
                <BreakableRef text={methodRef({ key: run.method_key, version: run.method_version })} />
              </span>
            </div>
          ),
        },
        { key: "state", header: "State", render: (run) => <StatusBadge semantics={RUN_STATE[run.state]} /> },
        { key: "object", header: "EvidenceObject", render: (run) => <RefId id={run.evidence_object_id} /> },
      ]}
    />
  );
}

function StateNarrative({ run }: { run: AnalysisRunDetail }) {
  if (run.state === "queued") {
    return <p className="text-xs text-fg-muted">Waiting for a worker to claim this run. Cancelling now takes effect immediately.</p>;
  }
  if (run.state === "running") {
    return (
      <p className="text-xs text-fg-muted">
        A worker is executing this run.
        {run.last_heartbeat_at && (
          <>
            {" "}Last heartbeat <Timestamp iso={run.last_heartbeat_at} />.
          </>
        )}
        {run.cancel_requested_at && (
          <>
            {" "}Cancellation was requested <Timestamp iso={run.cancel_requested_at} />; the worker stops at its next checkpoint.
          </>
        )}
      </p>
    );
  }
  if (run.state === "failed") {
    return (
      <div className="space-y-1.5 text-xs">
        <p className="flex flex-wrap items-center gap-2 text-fg-muted">
          <Tag tone="risk">{run.failure_code ?? "failed"}</Tag>
          <span>{run.failure_message}</span>
        </p>
        <p className="text-fg-subtle">No Observations were published. Retrying creates a new Analysis Run; this one stays as history.</p>
      </div>
    );
  }
  if (run.state === "cancelled") {
    return <p className="text-xs text-fg-muted">This run was cancelled and published no Observations. Retrying creates a new Analysis Run.</p>;
  }
  return null;
}

function ObservationList({ run }: { run: AnalysisRunDetail }) {
  if (run.state !== "completed") return null;
  return (
    <section aria-labelledby={`observations-${run.id}`} className="space-y-2">
      <h3 id={`observations-${run.id}`} className="text-xs font-semibold text-fg">
        Observations ({run.observations.length})
      </h3>
      <p className="text-2xs text-fg-subtle">
        An Observation states what the Method measured. It is not a Finding, a Claim or an Assessment, and it does not determine
        authenticity.
      </p>
      {run.observations.length === 0 && <p className="text-xs text-fg-subtle">This run published no Observations.</p>}
      <ol className="space-y-1.5">
        {run.observations.map((observation) => (
          <li key={observation.id} className="rounded border border-line bg-ink-900/40 px-3 py-2">
            <p className="text-[0.8125rem] text-fg">{observation.statement}</p>
            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-fg-subtle">
              <RefId id={observation.id} />
              <span>origin: {observation.origin.replace("_", " ")}</span>
              <span>
                recorded <Timestamp iso={observation.created_at} /> by <span className="mono-id">{observation.recorded_by}</span>
              </span>
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** One run: exact provenance (TRACE), its real state, its Observations, and what can be done next. */
export function RunDetail({
  caseId,
  runId,
  canExecute,
  focusHeading,
  onChanged,
  onRetried,
}: {
  caseId: string;
  runId: string;
  canExecute: boolean;
  focusHeading: boolean;
  onChanged: () => void;
  onRetried: (run: AnalysisRun) => void;
}) {
  const base = `/api/v1/cases/${caseId}/analysis-runs/${runId}`;
  const detail = useResource<AnalysisRunDetail>(base, { refreshMs: POLL_MS, refreshWhile: isActive });
  const [busy, setBusy] = useState<"cancel" | "retry" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [keys] = useState(() => new IntentKeys());
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (focusHeading && detail.status === "ready") heading.current?.focus();
  }, [focusHeading, detail.status, runId]);

  if (detail.status === "loading") return <StateView state="loading" title={`Loading ${runId}…`} compact />;
  if (detail.status === "error") return <ApiErrorView error={detail.error} subject={`Run ${runId}`} onRetry={detail.reload} compact />;
  const run = detail.data;
  const state = RUN_STATE[run.state];

  async function cancel() {
    setBusy("cancel");
    setError(null);
    try {
      await apiMutation<AnalysisRun>("POST", `${base}/cancel`);
      detail.refresh();
      onChanged();
    } catch (cause) {
      setError(actionMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  async function retry() {
    const intent = `retry:${runId}`;
    setBusy("retry");
    setError(null);
    try {
      const created = await apiMutation<AnalysisRun>("POST", `${base}/retry`, { idempotency_key: keys.keyFor(intent) });
      keys.settle(intent);
      onChanged();
      onRetried(created);
    } catch (cause) {
      // A definite answer settles the intent; an unreachable server keeps the key so a second
      // click cannot create a second run.
      if (cause instanceof ApiError && cause.status !== null) keys.settle(intent);
      setError(actionMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  return (
    <article aria-labelledby={`run-title-${run.id}`} className="space-y-4 px-4 py-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h3 id={`run-title-${run.id}`} ref={heading} tabIndex={-1} className="flex flex-wrap items-center gap-2 text-[0.8125rem] font-semibold text-fg outline-none">
          <span className="mono-id">{run.id}</span>
          <StatusBadge semantics={state} />
        </h3>
        {canExecute && (
          <div className="flex flex-wrap items-center gap-2">
            {canRequestCancel(run) && (
              <Button size="sm" variant="quiet" disabled={busy !== null} onClick={() => void cancel()}>
                {busy === "cancel" ? "Cancelling…" : run.state === "queued" ? "Cancel run" : "Request cancellation"}
              </Button>
            )}
            {isRetryable(run) && (
              <Button size="sm" disabled={busy !== null} onClick={() => void retry()}>
                {busy === "retry" ? "Retrying…" : "Retry as a new run"}
              </Button>
            )}
          </div>
        )}
      </header>
      <p role="status" className="sr-only">{`${run.id} is ${state.label.toLowerCase()}`}</p>
      {error && <p role="alert" className="text-xs text-risk">{error}</p>}
      {detail.refreshError && (
        <p role="status" className="text-xs text-warn">
          The latest state could not be refreshed ({detail.refreshError.message}). The state shown is the last one received.
        </p>
      )}
      <StateNarrative run={run} />
      <section aria-label={`Provenance of ${run.id}`}>
        <FactList
          items={[
            { term: "Method", detail: <span className="mono-id break-all">{methodRef({ key: run.method_key, version: run.method_version })}</span> },
            { term: "Evidence", detail: <RefId id={run.evidence_id} to={casePath(caseId, `evidence/${run.evidence_id}`)} /> },
            { term: "EvidenceObject", detail: <RefId id={run.evidence_object_id} /> },
            {
              term: "Parameters",
              detail: Object.keys(run.parameters).length === 0 ? "None" : <span className="mono-id break-all">{JSON.stringify(run.parameters)}</span>,
            },
            { term: "Requested by", detail: <span className="mono-id">{run.created_by}</span> },
            { term: "Queued", detail: <Timestamp iso={run.created_at} /> },
            { term: "Started", detail: run.started_at ? <Timestamp iso={run.started_at} /> : "—" },
            { term: "Finished", detail: run.completed_at ? <Timestamp iso={run.completed_at} /> : "—" },
          ]}
        />
      </section>
      <ObservationList run={run} />
    </article>
  );
}

/** The runs of one Case, with the selected run's detail. State is whatever the backend reports. */
export function RunsPanel({
  caseId,
  canExecute,
  selected,
  focusSelected,
  onSelect,
}: {
  caseId: string;
  canExecute: boolean;
  selected: string | null;
  focusSelected: boolean;
  /** ``focus`` is true when the run was just created (started or retried), so focus follows it. */
  onSelect: (id: string, focus?: boolean) => void;
}) {
  const runs = useResource<ListResponse<AnalysisRun>>(`/api/v1/cases/${caseId}/analysis-runs`, {
    refreshMs: POLL_MS,
    refreshWhile: (data) => data.items.some(isActive),
  });
  // A run that was just started or retried is not in the cached list yet, and the list only
  // polls while a listed run is active. Selecting a run therefore re-reads the list once.
  const { refresh } = runs;
  useEffect(() => {
    if (selected) refresh();
  }, [selected, refresh]);
  return (
    <Panel labelledBy="runs-title">
      <PanelHeader id="runs-title" eyebrow="Trace" title={`Analysis runs in ${caseId}`} />
      {runs.status === "loading" && <StateView state="loading" title="Loading analysis runs…" compact />}
      {runs.status === "error" && <ApiErrorView error={runs.error} subject="Analysis runs" onRetry={runs.reload} compact />}
      {runs.status === "ready" && runs.refreshError && (
        <p role="status" className="border-b border-line px-4 py-2 text-xs text-warn">
          The run list could not be refreshed ({runs.refreshError.message}). It shows the last state received.
        </p>
      )}
      {runs.status === "ready" && runs.data.count === 0 && (
        <StateView state="empty" title={`No analysis runs recorded for ${caseId}.`} compact>
          Choose a Method and a preserved EvidenceObject, then start an examination.
        </StateView>
      )}
      {runs.status === "ready" && runs.data.count > 0 && (
        <RunList runs={runs.data.items} selected={selected} onSelect={onSelect} />
      )}
      {selected && (
        <div className="border-t border-line">
          <RunDetail
            key={selected}
            caseId={caseId}
            runId={selected}
            canExecute={canExecute}
            focusHeading={focusSelected}
            onChanged={runs.refresh}
            onRetried={(created) => {
              runs.refresh();
              onSelect(created.id, true);
            }}
          />
        </div>
      )}
    </Panel>
  );
}
