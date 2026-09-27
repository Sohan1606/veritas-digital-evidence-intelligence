import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, StateView } from "../design-system";

interface State {
  incident: string | null;
}

/**
 * Contains render failures to one view. Logs an incident reference and the error
 * type only — never component data, which may include case content.
 */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  state: State = { incident: null };

  static getDerivedStateFromError(): State {
    const random = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now());
    return { incident: `UI-${random.slice(0, 8).toUpperCase()}` };
  }

  componentDidCatch(error: Error, _info: ErrorInfo) {
    console.error(`[veritas] view render failure ${this.state.incident ?? ""}: ${error.name}`);
  }

  componentDidUpdate(previous: { resetKey?: string }) {
    if (this.state.incident && previous.resetKey !== this.props.resetKey) this.setState({ incident: null });
  }

  render() {
    if (!this.state.incident) return this.props.children;
    return (
      <StateView
        state="error"
        title="This view failed to render."
        requestId={this.state.incident}
        action={<Button size="sm" onClick={() => this.setState({ incident: null })}>Reload view</Button>}
      >
        The failure was contained to this view. Other sections remain usable.
      </StateView>
    );
  }
}
