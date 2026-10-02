/**
 * Examination view logic that is not React: which states are final, whether a Method and an
 * EvidenceObject can be paired, and how a retry-safe idempotency key is kept.
 *
 * Nothing here decides authorization or eligibility. The backend is authoritative and re-checks
 * every request; this only explains, before a request is sent, why it would be refused.
 */
import type {
  AnalysisRun,
  AnalysisRunState,
  Evidence,
  EvidenceObject,
  ExaminationMethod,
} from "../../api/types";

export const POLL_MS = 2000;

const FINAL_STATES: ReadonlySet<AnalysisRunState> = new Set(["completed", "failed", "cancelled"]);
const RETRYABLE_STATES: ReadonlySet<AnalysisRunState> = new Set(["failed", "cancelled"]);

/** A finished run is history: it never changes again. */
export const isFinal = (state: AnalysisRunState) => FINAL_STATES.has(state);
export const isActive = (run: Pick<AnalysisRun, "state">) => !isFinal(run.state);
export const isRetryable = (run: Pick<AnalysisRun, "state">) => RETRYABLE_STATES.has(run.state);
/** Cancellation can be requested while a run is queued or running and not already requested. */
export const canRequestCancel = (run: Pick<AnalysisRun, "state" | "cancel_requested_at">) =>
  isActive(run) && run.cancel_requested_at === null;

export const methodRef = (method: Pick<ExaminationMethod, "key" | "version">) => `${method.key}@${method.version}`;

export interface PairingInput {
  canExecute: boolean;
  demonstration: boolean;
  method: ExaminationMethod | undefined;
  evidence: Evidence | undefined;
  object: EvidenceObject | undefined;
}

export type Eligibility = { eligible: true } | { eligible: false; reason: string };

const blocked = (reason: string): Eligibility => ({ eligible: false, reason });

/** Why the selected pair could not be examined, or that nothing visible prevents it. */
export function eligibilityOf(input: PairingInput): Eligibility {
  const { method, evidence, object } = input;
  if (input.demonstration) return blocked("Demonstration cases never execute examination Methods.");
  if (!input.canExecute) return blocked("This identity can read examinations but cannot start them.");
  if (!method) return blocked("Select a Method.");
  if (!method.enabled) return blocked(`${methodRef(method)} is disabled.`);
  if ((method.parameters.required ?? []).length > 0) {
    return blocked(`${methodRef(method)} needs parameters this view cannot supply.`);
  }
  if (!object || !evidence) return blocked("Select a preserved EvidenceObject.");
  if (object.state !== "PRESERVED") {
    return blocked(`Only a PRESERVED EvidenceObject can be examined. ${object.id} is ${object.state}.`);
  }
  if (!method.supported_evidence_types.includes(evidence.evidence_type)) {
    return blocked(`${methodRef(method)} does not support ${evidence.evidence_type} evidence.`);
  }
  if (object.byte_size > method.resource_limits.max_object_bytes) {
    return blocked(`${object.id} is larger than the ${methodRef(method)} size limit.`);
  }
  return { eligible: true };
}

/** A new, unguessable idempotency key. Uniqueness is all that matters; it carries no meaning. */
export function newIdempotencyKey(): string {
  return `ui-${crypto.randomUUID()}`;
}

/**
 * Keeps ONE idempotency key per user intent. The key is reused only while the outcome of the
 * previous attempt is unknown (the connection failed), so a second click cannot start a second
 * run; once the server has answered either way, the next click is a new intent with a new key.
 */
export class IntentKeys {
  private readonly pending = new Map<string, string>();

  keyFor(intent: string): string {
    const existing = this.pending.get(intent);
    if (existing) return existing;
    const created = newIdempotencyKey();
    this.pending.set(intent, created);
    return created;
  }

  /** The server answered (success or a definite refusal): the intent is settled. */
  settle(intent: string): void {
    this.pending.delete(intent);
  }
}

export function formatBytes(bytes: number): string {
  return `${bytes.toLocaleString()} bytes`;
}
