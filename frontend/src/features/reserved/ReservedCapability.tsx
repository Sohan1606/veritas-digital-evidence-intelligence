import type { ReactNode } from "react";
import type { SystemInfo } from "../../api/types";
import { useResource } from "../../api/useResource";
import { StateView, Tag } from "../../design-system";

/**
 * Honest placeholder for a capability the backend declares as reserved. The wording
 * comes from /api/v1/system, so availability has exactly one owner (the backend).
 */
export function ReservedCapability({ capability, subject, children }: { capability: string; subject: string; children?: ReactNode }) {
  const system = useResource<SystemInfo>("/api/v1/system");
  const entry = system.status === "ready" ? system.data.capabilities.find((c) => c.key === capability) : undefined;

  if (system.status === "loading") return <StateView state="loading" title="Loading capability state…" />;
  if (entry?.status === "available") return null;

  return (
    <StateView state="unavailable" title={`${subject} is not available in this version.`}>
      <p>{entry?.note ?? "Capability state could not be determined; treating it as unavailable."}</p>
      {children && <div className="mt-3">{children}</div>}
      <div className="mt-3">
        <Tag>reserved</Tag>
      </div>
    </StateView>
  );
}
