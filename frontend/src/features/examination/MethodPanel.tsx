import type { ExaminationMethod } from "../../api/types";
import { Panel, PanelHeader, Tag } from "../../design-system";
import { formatBytes, methodRef } from "./examinationModel";

function Explanation({ method }: { method: ExaminationMethod }) {
  const parameters = Object.keys(method.parameters.properties ?? {});
  return (
    <details className="border-t border-line px-3 py-2 text-xs">
      <summary className="cursor-pointer text-fg-muted">Explain {method.name}</summary>
      <div className="mt-3 space-y-3 text-fg-muted">
        <section aria-label={`What ${method.name} publishes`}>
          <h3 className="font-semibold text-fg">What it publishes</h3>
          <dl className="mt-1.5 space-y-1.5">
            {method.outputs.map((output) => (
              <div key={output.key}>
                <dt className="text-fg">{output.label}</dt>
                <dd className="text-fg-subtle">{output.definition}</dd>
              </div>
            ))}
          </dl>
        </section>
        <section aria-label={`What ${method.name} needs`}>
          <h3 className="font-semibold text-fg">What it needs</h3>
          <ul className="mt-1.5 list-disc space-y-1 pl-4">
            {method.input_requirements.map((requirement) => (
              <li key={requirement}>{requirement}</li>
            ))}
          </ul>
          <p className="mt-1.5">
            Parameters: {parameters.length === 0 ? "none" : parameters.join(", ")}. Supported evidence types:{" "}
            {method.supported_evidence_types.join(", ")}.
          </p>
        </section>
        <section aria-label={`What ${method.name} does not determine`}>
          <h3 className="font-semibold text-fg">Limitations</h3>
          <ul className="mt-1.5 list-disc space-y-1 pl-4">
            {method.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </section>
        <p className="text-fg-subtle">
          Bounded to {formatBytes(method.resource_limits.max_object_bytes)} per object, read in{" "}
          {formatBytes(method.resource_limits.chunk_bytes)} chunks, at most {method.resource_limits.max_runtime_seconds} seconds.
        </p>
      </div>
    </details>
  );
}

/** The Methods the backend registry offers. Nothing about a Method is defined in this view. */
export function MethodPanel({
  methods,
  selected,
  onSelect,
}: {
  methods: ExaminationMethod[];
  selected: string | null;
  onSelect: (reference: string) => void;
}) {
  return (
    <Panel labelledBy="methods-title">
      <PanelHeader id="methods-title" eyebrow="Explain" title="Methods" />
      <fieldset className="px-4 py-4">
        <legend className="sr-only">Examination Method</legend>
        <ul className="space-y-3">
          {methods.map((method) => {
            const reference = methodRef(method);
            const active = selected === reference;
            return (
              <li
                key={reference}
                className={`rounded-md border ${active ? "border-line-strong bg-ink-750" : "border-line bg-ink-850"} ${method.enabled ? "" : "opacity-60"}`}
              >
                <div className="flex items-start gap-3 p-3">
                  <input
                    id={`method-${reference}`}
                    type="radio"
                    name="examination-method"
                    value={reference}
                    checked={active}
                    disabled={!method.enabled}
                    aria-describedby={`method-purpose-${reference}`}
                    onChange={() => onSelect(reference)}
                    className="mt-1 h-4 w-4 accent-[var(--color-signal)]"
                  />
                  <div className="min-w-0 flex-1">
                    <label htmlFor={`method-${reference}`} className="block cursor-pointer text-[0.8125rem] font-medium text-fg">
                      {method.name}
                    </label>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      <span className="mono-id break-all text-2xs text-fg-muted">{reference}</span>
                      {method.deterministic && <Tag>deterministic</Tag>}
                      {!method.enabled && <Tag tone="warn">disabled</Tag>}
                    </div>
                    <p id={`method-purpose-${reference}`} className="mt-1.5 text-xs leading-relaxed text-fg-muted">
                      {method.purpose}
                    </p>
                  </div>
                </div>
                <Explanation method={method} />
              </li>
            );
          })}
        </ul>
      </fieldset>
    </Panel>
  );
}
