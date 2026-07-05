import { autocompletion } from '@codemirror/autocomplete';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap } from '@codemirror/view';
import { graphql, updateSchema } from 'cm6-graphql';
import { PricingPack, type QuoteResult } from 'costql';
import { buildClientSchema, type GraphQLSchema } from 'graphql';
import React, { useEffect, useRef, useState } from 'react';
import { ReceiptView, type ReceiptItem } from './ReceiptView.js';

/** The playground: the design system's Window + Receipt, wired to the REAL
 * costql engine (the npm package) against the committed packs. */

const PACKS = [
  {
    id: 'tmdb',
    file: 'tmdb_t3.json',
    sample: '{ movie(id:"27205"){ title cast(limit:8){ person{ name } } } }',
  },
  {
    id: 'northwind',
    file: 'northwind_t3.json',
    sample: '{ orders(first:20){ details(first:15){ product{ category{ name } } } } }',
  },
  {
    id: 'rickmorty',
    file: 'rickmorty_t1.json',
    sample: '{ character(id:"1"){ episode{ characters{ name } } } }',
  },
];

function shortResolver(rid: string): string {
  return rid.startsWith('Query.') ? rid.slice(6) : rid;
}

function receiptFromQuote(q: QuoteResult): {
  items: ReceiptItem[];
  total: string;
  totalLabel: string;
  totalFlagged: boolean;
  footer: React.ReactNode;
} {
  const items: ReceiptItem[] = [];
  const low = q.confidence === 'low';

  const lines = [...(q.breakdown ?? [])]
    .sort((a, b) => (b.cost as number) - (a.cost as number))
    .slice(0, 6);
  for (const b of lines) {
    const rid = b.resolver_id as string;
    const inv = Number(b.invocations);
    items.push({
      label: `${shortResolver(rid)}${inv > 1 ? ` ×${inv}` : ''}`,
      value: (b.cost as number).toFixed(1),
      indent: !rid.startsWith('Query.'),
    });
  }
  if (!lines.length) {
    items.push({ label: `${q.tier} pack; total only`, value: q.currency, muted: true });
  }
  for (const s of (q.sharing ?? []).slice(0, 3)) {
    items.push({
      label: `${(s as any).folds.map(shortResolver).join(', ')} → ${(s as any).loader}`,
      value: 'once',
      muted: true,
    });
  }
  for (const e of q.external_costs ?? []) {
    items.push({
      label: `${shortResolver((e as any).resolver_id)} → ${(e as any).host}`,
      value: String((e as any).authored_fee),
      muted: true,
    });
  }
  if (q.typical_price != null) {
    items.push({ label: 'typical estimate', value: (q.typical_price as number).toFixed(1), muted: true });
  }
  if (low) {
    items.push({ label: 'cycle detected', value: '↺', muted: true });
  }

  return {
    items,
    total: (q.price as number).toFixed(1),
    totalLabel: low ? 'ceiling · flagged' : 'price ceiling',
    totalFlagged: low,
    footer: (
      <span>
        confidence: <strong className={low ? 'low' : 'ok'}>{q.confidence}</strong>
        {low ? ': structural upper bound; run it for the exact cost' : ` · ${q.currency}, never dollars`}
      </span>
    ),
  };
}

export default function Playground() {
  const [idx, setIdx] = useState(0);
  const [quote, setQuote] = useState<QuoteResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(true);
  const editorHost = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const packRef = useRef<PricingPack | null>(null);
  const cache = useRef(new Map<string, { pack: PricingPack; schema: GraphQLSchema }>());

  const run = () => {
    const view = viewRef.current;
    const pack = packRef.current;
    if (!view || !pack) return;
    setRunning(true);
    setTimeout(() => {
      try {
        const q = view.state.doc.toString().trim();
        if (q) {
          setQuote(pack.quote(q));
          setError(null);
        }
      } catch (e) {
        setQuote(null);
        setError(e instanceof Error ? e.message : String(e));
      }
      setRunning(false);
    }, 380); // a beat of "measuring"; the quote itself is instant
  };

  useEffect(() => {
    if (!editorHost.current || viewRef.current) return;
    viewRef.current = new EditorView({
      parent: editorHost.current,
      state: EditorState.create({
        doc: '',
        extensions: [
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          autocompletion(),
          graphql(),
        ],
      }),
    });
    return () => { viewRef.current?.destroy(); viewRef.current = null; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const info = PACKS[idx];
    (async () => {
      let entry = cache.current.get(info.file);
      if (!entry) {
        const data = await (await fetch(`/packs/${info.file}`)).json();
        entry = {
          pack: PricingPack.fromObject(data),
          schema: buildClientSchema((data.introspection as any).data),
        };
        cache.current.set(info.file, entry);
      }
      if (cancelled) return;
      packRef.current = entry.pack;
      const view = viewRef.current;
      if (view) {
        updateSchema(view, entry.schema);
        view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: info.sample } });
      }
      run();
    })().catch((e) => { setError(String(e)); setRunning(false); });
    return () => { cancelled = true; };
  }, [idx]);

  const r = quote ? receiptFromQuote(quote) : null;

  return (
    <div className="cql-window">
      <div className="cql-window__bar">
        <div className="cql-dots">
          <span className="cql-dot cql-dot--filled" />
          <span className="cql-dot" />
          <span className="cql-dot" />
        </div>
        <div className="cql-window__title">playground: costql.quote()</div>
        <div className="cql-window__tools">
          <span className="cql-badge cql-badge--aqua">offline</span>
        </div>
      </div>
      <div className="cql-window__body--sunken">
        <div className="cql-play__grid">
          <div className="cql-play__left">
            <div className="cql-play__tabs" role="tablist">
              {PACKS.map((p, i) => (
                <button
                  key={p.id}
                  role="tab"
                  aria-selected={i === idx}
                  className={`cql-play__tab${i === idx ? ' cql-play__tab--active' : ''}`}
                  onClick={() => setIdx(i)}
                >
                  {p.file}
                </button>
              ))}
            </div>
            <label className="cql-play__label">query</label>
            <div className="cql-play__editor" ref={editorHost} />
            <div className="cql-play__run">
              <button className="cql-btn cql-btn--primary" onClick={run} disabled={running}>
                {running ? 'quoting…' : 'quote →'}
              </button>
              <span className="cql-play__pack">pack: {PACKS[idx].file}</span>
            </div>
            {error && (
              <div className="cql-play__error">
                can't parse this query: {error}. v0.1: no fragments, aliases unresolved.
              </div>
            )}
          </div>
          <div className="cql-play__right">
            {running || !r ? (
              <div className="cql-play__waiting">
                measuring work-ms<span className="cql-play__caret">▍</span>
              </div>
            ) : (
              <ReceiptView
                title="costql quote"
                meta={`${PACKS[idx].file} · offline`}
                items={r.items}
                total={r.total}
                totalLabel={r.totalLabel}
                totalFlagged={r.totalFlagged}
                footer={r.footer}
                width={320}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
