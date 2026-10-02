"""Canonical role-to-capability map shared by authorization and seed/provisioning."""

from __future__ import annotations

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "INVESTIGATOR": frozenset(
        {
            "case:read",
            "evidence:read",
            "evidence:intake",
            "custody:read",
            "findings:read",
            "claims:read",
            "graph:read",
            "case_audit:read",
            "examination:read",
            "examination:execute",
        }
    ),
    "REVIEWER": frozenset(
        {
            "case:read",
            "evidence:read",
            "findings:read",
            "claims:read",
            "graph:read",
            "case_audit:read",
            "review:read",
            "examination:read",
        }
    ),
    "CUSTODIAN": frozenset(
        {
            "case:read",
            "evidence:read",
            "evidence:intake",
            "case_audit:read",
            "custody:read",
            "custody:write",
        }
    ),
    "ADMINISTRATOR": frozenset(
        {"users:read", "roles:assign", "users:manage", "security_audit:read", "identity:read"}
    ),
    "AUDITOR": frozenset(
        {
            "case:read",
            "evidence:read",
            "findings:read",
            "claims:read",
            "graph:read",
            "case_audit:read",
            "security_audit:read",
            "case:read:any",
            "examination:read",
        }
    ),
    "RESEARCHER": frozenset(
        {
            "case:read",
            "evidence:read",
            "findings:read",
            "claims:read",
            "graph:read",
            "examination:read",
            "examination:execute",
        }
    ),
}

# API role-management cannot grant these high-impact roles. They are provisioned
# out-of-band by an operator; this avoids self-service privilege escalation.
PRIVILEGED_ROLES = frozenset({"ADMINISTRATOR", "AUDITOR"})
