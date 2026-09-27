import { useId, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { useResource } from "../api/useResource";
import type { CaseSummary, Claim, Evidence, Finding, ListResponse } from "../api/types";
import { Dialog, Icon, Kbd, type IconName } from "../design-system";
import { CASE_NAV, WORKSPACE_NAV, casePath } from "./navigation";

interface Command {
  id: string;
  group: string;
  label: string;
  ref?: string;
  icon: IconName;
  to: string;
}

/**
 * Global search / quick navigation. Searches navigation targets and records that are
 * already loadable through the read API (cases, and the active case's evidence,
 * findings and claims). Matching is literal — no ranking model, no remote search.
 */
export function CommandPalette({ open, onClose, activeCaseId }: { open: boolean; onClose: () => void; activeCaseId: string | null }) {
  return (
    <Dialog open={open} onClose={onClose} title="Search VERITAS" hideTitle placement="top">
      {/* Dialog mounts children only while open, so query and selection reset on every open. */}
      <PaletteBody onClose={onClose} activeCaseId={activeCaseId} />
    </Dialog>
  );
}

function PaletteBody({ onClose, activeCaseId }: { onClose: () => void; activeCaseId: string | null }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();

  const casePrefix = activeCaseId ? `/api/v1/cases/${activeCaseId}` : null;
  const cases = useResource<ListResponse<CaseSummary>>("/api/v1/cases");
  const evidence = useResource<ListResponse<Evidence>>(casePrefix && `${casePrefix}/evidence`);
  const findings = useResource<ListResponse<Finding>>(casePrefix && `${casePrefix}/findings`);
  const claims = useResource<ListResponse<Claim>>(casePrefix && `${casePrefix}/claims`);

  const commands = useMemo<Command[]>(() => {
    const list: Command[] = WORKSPACE_NAV.map((n) => ({ id: n.key, group: "Navigate", label: n.label, icon: n.icon, to: n.to }));
    if (activeCaseId) {
      list.push({ id: "overview", group: "Navigate", label: `${activeCaseId} overview`, icon: "cases", to: casePath(activeCaseId) });
      CASE_NAV.forEach((n) =>
        list.push({ id: `nav-${n.key}`, group: "Navigate", label: n.label, ref: activeCaseId, icon: n.icon, to: casePath(activeCaseId, n.segment) }),
      );
    }
    if (cases.status === "ready") {
      cases.data.items.forEach((c) => list.push({ id: c.id, group: "Cases", label: c.title, ref: c.id, icon: "cases", to: casePath(c.id) }));
    }
    if (activeCaseId && evidence.status === "ready") {
      evidence.data.items.forEach((e) =>
        list.push({ id: e.id, group: "Evidence", label: e.label, ref: e.id, icon: "evidence", to: casePath(activeCaseId, `evidence/${e.id}`) }),
      );
    }
    if (activeCaseId && findings.status === "ready") {
      findings.data.items.forEach((f) =>
        list.push({ id: f.id, group: "Findings", label: f.title, ref: f.id, icon: "finding", to: casePath(activeCaseId, `findings/${f.id}`) }),
      );
    }
    if (activeCaseId && claims.status === "ready") {
      claims.data.items.forEach((c) =>
        list.push({ id: c.id, group: "Claims", label: c.statement, ref: c.id, icon: "claim", to: `${casePath(activeCaseId, "claims")}#${c.id}` }),
      );
    }
    return list;
  }, [activeCaseId, cases, evidence, findings, claims]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => `${c.ref ?? ""} ${c.label} ${c.group}`.toLowerCase().includes(q));
  }, [commands, query]);

  const headers = useMemo(() => results.map((command, index) => (index === 0 || results[index - 1]?.group !== command.group ? command.group : null)), [results]);

  const choose = (command: Command | undefined) => {
    if (!command) return;
    onClose();
    navigate(command.to);
  };

  const activeId = results[active] ? `${listId}-${active}` : undefined;
  return (
    <>
      <div className="flex items-center gap-2 border-b border-line px-4">
        <Icon name="search" className="text-fg-subtle" />
        <input
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={activeId}
          aria-label="Search cases, records and sections"
          placeholder="Search by identifier, label or section…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setActive((i) => Math.min(i + 1, results.length - 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setActive((i) => Math.max(i - 1, 0));
            } else if (event.key === "Enter") {
              event.preventDefault();
              choose(results[active]);
            }
          }}
          className="h-12 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-fg-faint"
        />
        <Kbd>Esc</Kbd>
      </div>
      <ul id={listId} role="listbox" aria-label="Results" className="max-h-[52vh] overflow-y-auto py-2">
        {results.length === 0 && (
          <li className="px-4 py-6 text-[0.8125rem] text-fg-muted" role="presentation">
            No matching sections or records.
          </li>
        )}
        {results.map((command, index) => {
          const header = headers[index];
          return (
            <li key={`${command.group}-${command.id}`} role="presentation">
              {header && <div className="eyebrow px-4 pb-1 pt-3" role="presentation">{header}</div>}
              <div
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === active}
                tabIndex={-1}
                onMouseEnter={() => setActive(index)}
                onClick={() => choose(command)}
                onKeyDown={(event) => event.key === "Enter" && choose(command)}
                className={`mx-2 flex cursor-pointer items-center gap-3 rounded-sm px-2.5 py-2 text-[0.8125rem] ${
                  index === active ? "bg-ink-700 text-fg" : "text-fg-muted"
                }`}
              >
                <Icon name={command.icon} className="shrink-0 text-fg-subtle" />
                {command.ref && <span className="mono-id shrink-0 text-fg-subtle">{command.ref}</span>}
                <span className="truncate">{command.label}</span>
                {index === active && <Icon name="arrowRight" size={14} className="ml-auto shrink-0 text-signal" />}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="flex items-center gap-3 border-t border-line px-4 py-2 text-2xs text-fg-subtle">
        <span className="flex items-center gap-1"><Kbd>↑</Kbd><Kbd>↓</Kbd> move</span>
        <span className="flex items-center gap-1"><Kbd>↵</Kbd> open</span>
        <span className="ml-auto">Literal match over loaded records</span>
      </div>
    </>
  );
}
