/**
 * The single navigation definition. Sidebar, breadcrumbs and the command palette all
 * read from here, so a section can never exist under two names.
 */
import { useEffect } from "react";
import { useMatch } from "react-router";
import type { CaseCounts } from "../api/types";
import type { IconName } from "../design-system";

export interface NavItem {
  key: string;
  label: string;
  icon: IconName;
  /** Capability key from /api/v1/system; when reserved the item is marked as such. */
  capability?: string;
  count?: (counts: CaseCounts) => number;
}

export interface WorkspaceNavItem extends NavItem {
  to: string;
}

export interface CaseNavItem extends NavItem {
  /** Path segment below /app/cases/:caseId ("" = case overview). */
  segment: string;
}

export const WORKSPACE_NAV: WorkspaceNavItem[] = [
  { key: "my-work", label: "My Work", icon: "mywork", to: "/app/my-work", capability: "identity" },
  { key: "cases", label: "Cases", icon: "cases", to: "/app/cases" },
];

export const CASE_NAV: CaseNavItem[] = [
  { key: "evidence", label: "Evidence", icon: "evidence", segment: "evidence", count: (c) => c.evidence },
  { key: "examination", label: "Examination", icon: "examination", segment: "examination", capability: "examination", count: (c) => c.analysis_runs },
  { key: "findings", label: "Findings", icon: "finding", segment: "findings", count: (c) => c.findings },
  { key: "claims", label: "Claims", icon: "claim", segment: "claims", count: (c) => c.claims },
  { key: "timeline", label: "Timeline", icon: "timeline", segment: "timeline", capability: "timeline" },
  { key: "graph", label: "Graph", icon: "graph", segment: "graph" },
  { key: "review", label: "Review", icon: "review", segment: "review", capability: "review", count: (c) => c.findings_awaiting_review },
  { key: "reports", label: "Reports", icon: "report", segment: "reports", capability: "report" },
  { key: "audit", label: "Audit", icon: "audit", segment: "audit" },
];

export function casePath(caseId: string, segment = ""): string {
  return segment ? `/app/cases/${caseId}/${segment}` : `/app/cases/${caseId}`;
}

const STORAGE_KEY = "veritas.activeCase";
const CASE_ID = /^CASE-\d{3,9}$/;

function readStored(): string | null {
  try {
    const value = window.sessionStorage.getItem(STORAGE_KEY);
    return value && CASE_ID.test(value) ? value : null;
  } catch {
    return null;
  }
}

/**
 * The case the investigator is working in: taken from the URL, otherwise the last
 * case opened in this browser session (so the case sections stay reachable).
 */
export function useActiveCaseId(): string | null {
  const match = useMatch("/app/cases/:caseId/*");
  const fromUrl = match?.params.caseId && CASE_ID.test(match.params.caseId) ? match.params.caseId : null;

  useEffect(() => {
    if (!fromUrl) return;
    try {
      window.sessionStorage.setItem(STORAGE_KEY, fromUrl);
    } catch {
      /* storage unavailable: URL remains the source */
    }
  }, [fromUrl]);

  return fromUrl ?? readStored();
}
