import React, { useState } from 'react';

/** React twin of Receipt.astro: same classes, same markup. Used by the
 * playground to render live quotes. */

export interface ReceiptItem {
  label: string;
  value: string;
  muted?: boolean;
  indent?: boolean;
  /** Hover/tap explanation; the label becomes the dotted-underline trigger. */
  tip?: React.ReactNode;
}

function Row({ it }: { it: ReceiptItem }) {
  return (
    <div
      className={`cql-receipt__row${it.muted ? ' cql-receipt__row--muted' : ''}${it.indent ? ' cql-receipt__row--indent' : ''}`}
    >
      {it.tip ? (
        <span className="cql-tip cql-receipt__label">
          <button type="button" className="cql-tip__trigger">{it.label}</button>
          <span className="cql-tip__bubble" role="tooltip">{it.tip}</span>
        </span>
      ) : (
        <span className="cql-receipt__label">{it.label}</span>
      )}
      <span className="cql-receipt__leader" />
      <span className="cql-receipt__value">{it.value}</span>
    </div>
  );
}

/** One shared-loader group: several resolvers whose repeated work is billed once. */
export interface SharedGroup {
  resolvers: string[];
  loader: string;
}

export function ReceiptView({
  title = 'costql quote',
  meta,
  items,
  extras,
  shared,
  typical,
  typicalLabel = 'typical',
  total,
  totalLabel = 'work-time (ms)',
  totalFlagged = false,
  footer,
  width = 300,
  pulse = false,
}: {
  title?: string;
  meta?: string;
  items: ReceiptItem[];
  /** Whole-quote lines (per-field base, safety margin): rendered below the
   * scrolling items so their tooltips can't be clipped by the scroll box. */
  extras?: ReceiptItem[];
  shared?: SharedGroup[];
  typical?: string;
  typicalLabel?: string;
  total?: string;
  totalLabel?: string;
  totalFlagged?: boolean;
  footer?: React.ReactNode;
  width?: number;
  pulse?: boolean;
}) {
  const [sharedOpen, setSharedOpen] = useState(false);
  return (
    <div className="cql-receipt" style={{ width }}>
      <div className="cql-receipt__zig cql-receipt__zig--top" />
      <div className="cql-receipt__paper">
        <div className="cql-receipt__title">{title}</div>
        {meta && <div className="cql-receipt__meta">{meta}</div>}
        <div className="cql-receipt__rule" />
        {/* line items scroll so a big query can't run the receipt off the page */}
        <div className="cql-receipt__items">
          {items.map((it, i) => (
            <Row key={i} it={it} />
          ))}
        </div>
        {extras && extras.map((it, i) => <Row key={i} it={it} />)}
        {/* shared work: the T3 lines that used to just say "once", now a labeled
            section you can open, with an info tooltip explaining batching */}
        {shared && shared.length > 0 && (
          <div className="cql-receipt__shared" data-open={sharedOpen}>
            <div className="cql-receipt__shared-head">
              <button
                type="button"
                className="cql-receipt__shared-toggle"
                aria-expanded={sharedOpen}
                onClick={() => setSharedOpen((o) => !o)}
              >
                <span className="cql-receipt__chevron" aria-hidden="true">▸</span>
                <span>shared · counted once</span>
                <span className="cql-receipt__shared-count">({shared.length})</span>
              </button>
              <span className="cql-tip cql-tip--end">
                <button
                  type="button"
                  className="cql-tip__trigger cql-tip__trigger--icon"
                  aria-label="What does “counted once” mean?"
                >
                  i
                </button>
                <span className="cql-tip__bubble" role="tooltip">
                  Batching: when several fields hit the same loader, costQL counts that
                  shared work <strong>once</strong>, not once per row.
                  <a href="/docs/tiers/">Learn more &rarr;</a>
                </span>
              </span>
            </div>
            {sharedOpen && (
              <div className="cql-receipt__shared-body">
                {shared.map((g, i) => (
                  <div key={i} className="cql-receipt__row cql-receipt__row--muted">
                    <span className="cql-receipt__label">{g.resolvers.join(', ')} &rarr; {g.loader}</span>
                    <span className="cql-receipt__leader" />
                    <span className="cql-receipt__value">once</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {total != null && (
          <>
            <div className="cql-receipt__rule" />
            {typical != null && (
              <div className="cql-receipt__subtotal">
                <span>{typicalLabel}</span>
                <span>{typical}</span>
              </div>
            )}
            <div className={`cql-receipt__total${totalFlagged ? ' cql-receipt__total--flagged' : ''}`}>
              <span>{totalLabel}</span>
              <span className={`cql-receipt__total-value${pulse ? ' cql-receipt__total-value--pulse' : ''}`}>{total}</span>
            </div>
          </>
        )}
        {footer && <div className="cql-receipt__footer">{footer}</div>}
      </div>
      <div className="cql-receipt__zig cql-receipt__zig--bottom" />
    </div>
  );
}
