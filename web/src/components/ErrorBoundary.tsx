import { Component, type ReactNode } from "react";

/** Keeps one broken panel from blanking the whole app. */
export class ErrorBoundary extends Component<{ children: ReactNode; label: string }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="m-4 rounded-lg border border-down/40 bg-down/10 p-4 text-sm sm:m-6" role="alert">
        <p className="font-semibold text-down">{this.props.label} failed to render</p>
        <p className="num mt-1 text-xs text-ink-300">{this.state.error.message}</p>
        <button onClick={() => this.setState({ error: null })} className="mt-3 rounded bg-ink-700 px-3 py-1 text-xs hover:bg-ink-600">
          Try again
        </button>
      </div>
    );
  }
}
