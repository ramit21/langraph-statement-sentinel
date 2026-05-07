import React from "react";

// Renders an inline PII pill. ``last4`` optional.
export function PIIPill({ last4 = "" }) {
  return (
    <span className="pii-pill" data-testid="pii-pill">
      <span className="dots">••••</span>
      {last4 || ""}
    </span>
  );
}

// Replaces redaction tokens like [REDACTED_CC_1] inside a string with pills.
// Returns an array of React nodes safe to embed inside any inline parent.
export function renderWithRedactions(text) {
  if (!text) return null;
  const parts = String(text).split(/(\[REDACTED_(?:CC|ACCT|SSN)_\d+\])/g);
  return parts.map((p, i) => {
    if (/^\[REDACTED_(CC|ACCT|SSN)_\d+\]$/.test(p)) {
      const kind = p.match(/^\[REDACTED_(CC|ACCT|SSN)_/)[1];
      return (
        <span key={i} className="pii-pill mx-1" data-testid="pii-pill-inline">
          <span className="dots">••••</span>
          {kind}
        </span>
      );
    }
    return <React.Fragment key={i}>{p}</React.Fragment>;
  });
}
