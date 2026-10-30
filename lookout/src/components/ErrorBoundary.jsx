import { Component } from "react";

// One bad alert (an unexpected cues/trigger shape from the backend, say) must not unmount the whole
// dashboard. React has no hook for this: only a class component can catch a render error in its subtree.
export class ErrorBoundary extends Component {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error, info) {
    console.error(`[${this.props.label || "render"}] crashed:`, error, info?.componentStack);
  }

  componentDidUpdate(prev) {
    // A fresh value for `resetKey` (e.g. the alert changed on the next poll) gets another try.
    if (this.state.failed && prev.resetKey !== this.props.resetKey) this.setState({ failed: false });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return this.props.fallback ?? null;
  }
}
