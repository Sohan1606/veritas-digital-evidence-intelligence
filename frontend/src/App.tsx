import { Suspense, lazy } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { AppShell } from "./app-shell/AppShell";
import { ErrorBoundary } from "./app-shell/ErrorBoundary";
import { StateView } from "./design-system";
import { AuditPage } from "./features/audit/AuditPage";
import { CaseLayout } from "./features/cases/CaseLayout";
import { CaseOverview } from "./features/cases/CaseOverview";
import { CasesPage } from "./features/cases/CasesPage";
import { ClaimsPage } from "./features/claims/ClaimsPage";
import { EvidencePage } from "./features/evidence/EvidencePage";
import { ExaminationPage } from "./features/examination/ExaminationPage";
import { FindingsPage } from "./features/findings/FindingsPage";
import { GraphPage } from "./features/graph/GraphPage";
import { MyWorkPage, NotFoundPage, ReportsPage, TimelinePage } from "./features/reserved/ReservedPages";
import { ReviewPage } from "./features/review/ReviewPage";

// The showcase carries the canvas sequence; the investigator shell never loads it.
const ShowcasePage = lazy(() => import("./showcase/ShowcasePage"));

export function AppRoutes() {
  return (
    <Routes>
      <Route
        path="/"
        element={
          <Suspense fallback={<StateView state="loading" title="Loading…" />}>
            <ShowcasePage />
          </Suspense>
        }
      />
      <Route path="/app" element={<AppShell />}>
        <Route index element={<Navigate to="cases" replace />} />
        <Route path="my-work" element={<MyWorkPage />} />
        <Route path="cases" element={<CasesPage />} />
        <Route path="cases/:caseId" element={<CaseLayout />}>
          <Route index element={<CaseOverview />} />
          <Route path="evidence" element={<EvidencePage />} />
          <Route path="evidence/:evidenceId" element={<EvidencePage />} />
          <Route path="examination" element={<ExaminationPage />} />
          <Route path="findings" element={<FindingsPage />} />
          <Route path="findings/:findingId" element={<FindingsPage />} />
          <Route path="claims" element={<ClaimsPage />} />
          <Route path="timeline" element={<TimelinePage />} />
          <Route path="graph" element={<GraphPage />} />
          <Route path="review" element={<ReviewPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="audit" element={<AuditPage />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

export function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </ErrorBoundary>
  );
}
