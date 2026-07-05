import React from 'react';

/** React twin of Receipt.astro — same classes, same markup. Used by the
 * playground to render live quotes. */

export interface ReceiptItem {
  label: string;
  value: string;
  muted?: boolean;
  indent?: boolean;
}

export function ReceiptView({
  title = 'costql quote',
  meta,
  items,
  total,
  totalLabel = 'price ceiling',
  totalFlagged = false,
  footer,
  width = 300,
}: {
  title?: string;
  meta?: string;
  items: ReceiptItem[];
  total?: string;
  totalLabel?: string;
  totalFlagged?: boolean;
  footer?: React.ReactNode;
  width?: number;
}) {
  return (
    <div className="cql-receipt" style={{ width }}>
      <div className="cql-receipt__zig cql-receipt__zig--top" />
      <div className="cql-receipt__paper">
        <div className="cql-receipt__title">{title}</div>
        {meta && <div className="cql-receipt__meta">{meta}</div>}
        <div className="cql-receipt__rule" />
        {items.map((it, i) => (
          <div
            key={i}
            className={`cql-receipt__row${it.muted ? ' cql-receipt__row--muted' : ''}${it.indent ? ' cql-receipt__row--indent' : ''}`}
          >
            <span className="cql-receipt__label">{it.label}</span>
            <span className="cql-receipt__leader" />
            <span className="cql-receipt__value">{it.value}</span>
          </div>
        ))}
        {total != null && (
          <>
            <div className="cql-receipt__rule" />
            <div className={`cql-receipt__total${totalFlagged ? ' cql-receipt__total--flagged' : ''}`}>
              <span>{totalLabel}</span>
              <span className="cql-receipt__total-value">{total}</span>
            </div>
          </>
        )}
        {footer && <div className="cql-receipt__footer">{footer}</div>}
      </div>
      <div className="cql-receipt__zig cql-receipt__zig--bottom" />
    </div>
  );
}
