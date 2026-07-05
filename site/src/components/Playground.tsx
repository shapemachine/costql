import { autocompletion } from '@codemirror/autocomplete';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap, lineNumbers } from '@codemirror/view';
import { graphql, updateSchema } from 'cm6-graphql';
import { PricingPack, type QuoteResult } from 'costql';
import { buildClientSchema, type GraphQLSchema } from 'graphql';
import React, { useEffect, useRef, useState } from 'react';
import { CostPanel } from './CostPanel.js';
import { SchemaTree } from './SchemaTree.js';

interface PackInfo {
  id: string;
  label: string;
  file: string;
  tierNote: string;
  examples: Array<{ label: string; query: string }>;
}

const PACKS: PackInfo[] = [
  {
    id: 'tmdb',
    label: 'TMDB (movies)',
    file: '/packs/tmdb_t3.json',
    tierNote: 'T3 · work_ms — instrumented demo: observed sharing, one paid field',
    examples: [
      { label: 'simple lookup', query: '{ movie(id:"27205"){ title genres{ name } } }' },
      { label: 'cast fanout (declared size)', query: '{ movie(id:"27205"){ cast(limit:8){ person{ name } } } }' },
      { label: 'filmography', query: '{ person(id:"6193"){ name filmography{ movie{ title } } } }' },
      { label: 'paid external field', query: '{ movie(id:"27205"){ title aiSummary } }' },
      { label: 'cyclic — watch the flag', query: '{ movie(id:"27205"){ recommendations{ recommendations{ title } } } }' },
    ],
  },
  {
    id: 'rickmorty',
    label: 'Rick & Morty (public API)',
    file: '/packs/rickmorty_t1.json',
    tierNote: 'T1 · wall_time_ms — a black-box public API costQL does not own',
    examples: [
      { label: 'character', query: '{ character(id:"1"){ name status species gender } }' },
      { label: 'with episodes', query: '{ character(id:"1"){ name episode{ name air_date } } }' },
      { label: 'episode cast', query: '{ episode(id:"40"){ name characters{ name } } }' },
      { label: 'cyclic — watch the flag', query: '{ character(id:"1"){ episode{ characters{ name } } } }' },
    ],
  },
  {
    id: 'northwind',
    label: 'Northwind (SQL, heavy sharing)',
    file: '/packs/northwind_t3.json',
    tierNote: 'T3 · work_ms — batch-heavy database: rising loader curves',
    examples: [
      { label: 'product + category', query: '{ product(id:"1"){ name unitPrice category{ name } } }' },
      { label: 'order details', query: '{ order(id:"15000"){ orderDate details(first:15){ quantity product{ name } } } }' },
      { label: 'hub query — sharing folds', query: '{ orders(first:20){ details(first:15){ product{ category{ name } } } } }' },
      { label: 'wider page — price scales', query: '{ orders(first:40){ details(first:15){ product{ name } } } }' },
    ],
  },
];

export default function Playground() {
  const [packId, setPackId] = useState('tmdb');
  const info = PACKS.find((p) => p.id === packId)!;
  const [schema, setSchema] = useState<GraphQLSchema | null>(null);
  const [quote, setQuote] = useState<QuoteResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const editorHost = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const packRef = useRef<PricingPack | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout>>();
  const packCache = useRef(new Map<string, { pack: PricingPack; schema: GraphQLSchema }>());

  const requote = (q: string) => {
    clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      const p = packRef.current;
      if (!p) return;
      try {
        const trimmed = q.trim();
        if (!trimmed) { setQuote(null); setError(null); return; }
        setQuote(p.quote(trimmed));
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }, 250);
  };

  // create the editor once
  useEffect(() => {
    if (!editorHost.current || viewRef.current) return;
    const view = new EditorView({
      parent: editorHost.current,
      state: EditorState.create({
        doc: '',
        extensions: [
          lineNumbers(),
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          autocompletion(),
          graphql(),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) requote(u.state.doc.toString());
          }),
          EditorView.theme({ '&': { fontSize: '14px', height: '100%' } }),
        ],
      }),
    });
    viewRef.current = view;
    return () => { view.destroy(); viewRef.current = null; };
  }, []);

  // load pack + schema on switch
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let entry = packCache.current.get(info.file);
      if (!entry) {
        const data = await (await fetch(info.file)).json();
        const p = PricingPack.fromObject(data);
        const s = buildClientSchema((data.introspection as any).data);
        entry = { pack: p, schema: s };
        packCache.current.set(info.file, entry);
      }
      if (cancelled) return;
      packRef.current = entry.pack;
      setSchema(entry.schema);
      const view = viewRef.current;
      if (view) {
        updateSchema(view, entry.schema);
        const q = info.examples[0].query;
        view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: q } });
      }
    })().catch((e) => setError(String(e)));
    return () => { cancelled = true; };
  }, [packId]);

  const setQuery = (q: string) => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: q } });
    view.focus();
  };

  const insertAtCursor = (snippet: string, _composite: boolean) => {
    const view = viewRef.current;
    if (!view) return;
    const empty = !view.state.doc.toString().trim();
    const from = empty ? 0 : view.state.selection.main.head;
    const to = empty ? view.state.doc.length : from;
    const insert = empty ? `{ ${snippet} }` : ` ${snippet}`;
    view.dispatch({ changes: { from, to, insert } });
    // place the cursor inside `{  }` when the snippet opened a selection set
    const inner = insert.indexOf('{  }', empty ? 1 : 0);
    const anchor = inner >= 0 ? from + inner + 2 : from + insert.length;
    view.dispatch({ selection: { anchor } });
    view.focus();
  };

  return (
    <div className="cq-playground">
      <div className="cq-toolbar">
        <div className="cq-pack-switch" role="tablist">
          {PACKS.map((p) => (
            <button
              key={p.id}
              role="tab"
              aria-selected={p.id === packId}
              className={p.id === packId ? 'cq-tab cq-tab-active' : 'cq-tab'}
              onClick={() => setPackId(p.id)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="cq-tier-note">{info.tierNote}</div>
        <div className="cq-examples">
          {info.examples.map((ex) => (
            <button key={ex.label} className="cq-example" onClick={() => setQuery(ex.query)}>
              {ex.label}
            </button>
          ))}
        </div>
      </div>
      <div className="cq-columns">
        <div className="cq-left">
          {schema && <SchemaTree schema={schema} onInsert={insertAtCursor} />}
          <div className="cq-editor" ref={editorHost} />
        </div>
        <div className="cq-right">
          <CostPanel quote={quote} error={error} />
          <div className="cq-foot">
            Priced entirely in your browser by the <code>costql</code> npm package against a
            static pack — no server involved. Identical to the Python engine by{' '}
            <a href="/docs/js/">conformance test</a>.
          </div>
        </div>
      </div>
    </div>
  );
}
