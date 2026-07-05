import type { QuoteResult } from 'costql';
import React from 'react';

const CONF_COLOR: Record<string, string> = {
  high: 'var(--costql-safe, #2e7d52)',
  medium: 'var(--costql-flag, #a8710e)',
  low: '#b3452e',
  exact: 'var(--costql-accent, #0c7c8c)',
};

export function CostPanel({ quote, error }: { quote: QuoteResult | null; error: string | null }) {
  if (error) {
    return (
      <div className="cq-panel">
        <div className="cq-error">
          <strong>Can’t price this query</strong>
          <div>{error}</div>
          <div className="cq-hint">
            v0.1 parser: no fragments, aliases not resolved — see the docs’ limitations page.
          </div>
        </div>
      </div>
    );
  }
  if (!quote) return <div className="cq-panel cq-empty">Type a query to see its price.</div>;

  const breakdown = [...(quote.breakdown ?? [])].sort(
    (a, b) => (b.cost as number) - (a.cost as number),
  );
  return (
    <div className="cq-panel">
      <div className="cq-price-row">
        <div>
          <div className="cq-price">
            {(quote.price as number).toFixed(1)}
            <span className="cq-unit"> {quote.currency}</span>
          </div>
          <div className="cq-price-label">safe billable ceiling</div>
        </div>
        <div className="cq-side">
          <div className="cq-typical">
            typical {quote.typical_price != null ? (quote.typical_price as number).toFixed(1) : 'n/a'}
          </div>
          <span className="cq-badge" style={{ background: CONF_COLOR[quote.confidence] }}>
            {quote.confidence} confidence
          </span>
          <div className="cq-meta">
            {quote.tier} · {quote.basis} · contract v{quote.contract_version}
          </div>
        </div>
      </div>

      {quote.caveats.length > 0 && (
        <div className="cq-caveat">
          <strong>flagged:</strong> {quote.caveats[0]}
        </div>
      )}

      {breakdown.length > 0 && (
        <div className="cq-section">
          <h4>Cost drivers</h4>
          <table>
            <tbody>
              {breakdown.slice(0, 8).map((b) => (
                <tr key={b.resolver_id as string}>
                  <td className="cq-mono">{b.resolver_id as string}</td>
                  <td className="cq-num">{(b.cost as number).toFixed(2)}</td>
                  <td className="cq-dim">×{String(b.invocations)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(quote.sharing?.length ?? 0) > 0 && (
        <div className="cq-section">
          <h4>Sharing observed (T3)</h4>
          {quote.sharing!.map((s: any) => (
            <div key={s.loader} className="cq-share">
              <span className="cq-mono">{s.folds.join(', ')}</span> fold onto{' '}
              <span className="cq-mono cq-safe">{s.loader}</span> — counted once
            </div>
          ))}
        </div>
      )}

      {(quote.external_costs?.length ?? 0) > 0 && (
        <div className="cq-section">
          <h4>External / paid calls</h4>
          {quote.external_costs!.map((e: any) => (
            <div key={e.resolver_id} className="cq-ext">
              <span className="cq-mono">{e.resolver_id}</span> →{' '}
              <span className="cq-mono">{e.host}</span> (authored fee {e.authored_fee}, not
              measured)
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
