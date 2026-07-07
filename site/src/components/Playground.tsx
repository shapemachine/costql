import { autocompletion } from '@codemirror/autocomplete';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap } from '@codemirror/view';
import { graphql, updateSchema } from 'cm6-graphql';
import { PricingPack, type QuoteResult } from 'costql';
import {
  buildClientSchema,
  getNamedType,
  isEnumType,
  isInterfaceType,
  isObjectType,
  parse,
  print,
  validate,
  type DocumentNode,
  type GraphQLArgument,
  type GraphQLField,
  type GraphQLInterfaceType,
  type GraphQLObjectType,
  type GraphQLSchema,
} from 'graphql';
import React, { useEffect, useRef, useState } from 'react';
import { ReceiptView, type ReceiptItem, type SharedGroup } from './ReceiptView.js';

/** The playground: an Apollo-sandbox-style composer wired to the REAL costql
 * engine (the npm package) against the committed packs. Three columns: the
 * schema's fields (click + / − to build a query), the operation + variables,
 * and the quote as a receipt. The editor is the source of truth; the tree
 * reads the parsed operation and writes back through graphql-js print(). */

const PACKS = [
  {
    id: 'tmdb',
    file: 'tmdb_t3.json',
    sample:
      'query ($id: ID!) {\n  movie(id: $id) {\n    title\n    cast(limit: 8) {\n      person {\n        name\n      }\n    }\n  }\n}',
    vars: '{ "id": "27205" }',
  },
  {
    id: 'northwind',
    file: 'northwind_t3.json',
    sample:
      '{\n  orders(first: 20) {\n    details(first: 15) {\n      product {\n        category {\n          name\n        }\n      }\n    }\n  }\n}',
    vars: '{ }',
  },
  {
    id: 'rickmorty',
    file: 'rickmorty_t1.json',
    sample:
      'query ($id: ID!) {\n  character(id: $id) {\n    name\n    status\n    species\n    origin {\n      name\n    }\n  }\n}',
    vars: '{ "id": "1" }',
  },
];

/* ---------- AST helpers (plain-object graphql-js nodes) ---------- */

type Path = string[];

function cloneDoc(doc: DocumentNode): any {
  return JSON.parse(JSON.stringify(doc));
}

function emptyDoc(): any {
  return {
    kind: 'Document',
    definitions: [
      {
        kind: 'OperationDefinition',
        operation: 'query',
        variableDefinitions: [],
        directives: [],
        selectionSet: { kind: 'SelectionSet', selections: [] },
      },
    ],
  };
}

function firstOp(doc: any): any | null {
  return doc?.definitions?.find((d: any) => d.kind === 'OperationDefinition') ?? null;
}

function findSel(selSet: any, name: string): any | null {
  return selSet?.selections?.find((s: any) => s.kind === 'Field' && s.name.value === name) ?? null;
}

/** Is the field at `path` selected in the operation? */
function pathPresent(doc: any, path: Path): boolean {
  let selSet = firstOp(doc)?.selectionSet;
  for (const name of path) {
    const node = selSet ? findSel(selSet, name) : null;
    if (!node) return false;
    selSet = node.selectionSet;
  }
  return true;
}

/** Collect the paths of all composite fields in the operation (to auto-expand
 * the tree so it mirrors the sample query). */
function presentCompositePaths(doc: any): string[] {
  const out: string[] = [];
  const walk = (selSet: any, prefix: Path) => {
    for (const sel of selSet?.selections ?? []) {
      if (sel.kind !== 'Field') continue;
      if (sel.selectionSet) {
        const p = [...prefix, sel.name.value];
        out.push(p.join('.'));
        walk(sel.selectionSet, p);
      }
    }
  };
  walk(firstOp(doc)?.selectionSet, []);
  return out;
}

/** A literal ValueNode for a required argument we auto-fill on add. */
function defaultValueNode(arg: GraphQLArgument): any {
  let t: any = arg.type;
  while (t.ofType) t = t.ofType;
  const name = t.name;
  if (name === 'Int') return { kind: 'IntValue', value: '5' };
  if (name === 'Float') return { kind: 'FloatValue', value: '1.5' };
  if (name === 'Boolean') return { kind: 'BooleanValue', value: false };
  if (isEnumType(t)) return { kind: 'EnumValue', value: t.getValues()[0]?.name ?? 'UNKNOWN' };
  return { kind: 'StringValue', value: '1' }; // ID, String, custom scalars
}

function makeFieldNode(field: GraphQLField<unknown, unknown>): any {
  const args = field.args
    .filter((a) => a.type.toString().endsWith('!') && a.defaultValue === undefined)
    .map((a) => ({
      kind: 'Argument',
      name: { kind: 'Name', value: a.name },
      value: defaultValueNode(a),
    }));
  return {
    kind: 'Field',
    name: { kind: 'Name', value: field.name },
    arguments: args,
    directives: [],
  };
}

type Composite = GraphQLObjectType | GraphQLInterfaceType;

function compositeType(field: GraphQLField<unknown, unknown>): Composite | null {
  const named = getNamedType(field.type);
  return isObjectType(named) || isInterfaceType(named) ? named : null;
}

/** A sensible first child when adding a composite field: id/name, else the
 * first scalar leaf (an empty selection set would not survive print/parse). */
function defaultChild(type: Composite): GraphQLField<unknown, unknown> | null {
  const fields = type.getFields();
  for (const pick of ['id', 'name']) if (fields[pick] && !compositeType(fields[pick])) return fields[pick];
  return Object.values(fields).find((f) => !compositeType(f)) ?? null;
}

function addFieldAt(doc: any, path: Path, schema: GraphQLSchema): any {
  const out = doc ? cloneDoc(doc) : emptyDoc();
  const op = firstOp(out) ?? emptyDoc().definitions[0];
  let selSet = op.selectionSet;
  let type: Composite = schema.getQueryType()!;
  for (let i = 0; i < path.length; i++) {
    const field = type.getFields()[path[i]];
    if (!field) return out;
    let node = findSel(selSet, path[i]);
    if (!node) {
      node = makeFieldNode(field);
      selSet.selections.push(node);
    }
    const named = compositeType(field);
    if (named) {
      if (!node.selectionSet) node.selectionSet = { kind: 'SelectionSet', selections: [] };
      if (i === path.length - 1 && node.selectionSet.selections.length === 0) {
        const child = defaultChild(named);
        if (child) node.selectionSet.selections.push(makeFieldNode(child));
      }
      selSet = node.selectionSet;
      type = named;
    }
  }
  return out;
}

/** Remove the field at `path`; parents left with an empty selection set are
 * removed too (an empty block would not survive print/parse). */
function removeFieldAt(doc: any, path: Path): any {
  const out = cloneDoc(doc);
  const op = firstOp(out);
  if (!op) return out;
  const rec = (selSet: any, i: number) => {
    const node = findSel(selSet, path[i]);
    if (!node) return;
    if (i === path.length - 1) {
      selSet.selections = selSet.selections.filter((s: any) => s !== node);
    } else if (node.selectionSet) {
      rec(node.selectionSet, i + 1);
      if (node.selectionSet.selections.length === 0) {
        selSet.selections = selSet.selections.filter((s: any) => s !== node);
      }
    }
  };
  rec(op.selectionSet, 0);
  return out;
}

/** Inline the variables as literals and drop the definitions: the quote
 * engine prices the concrete query (so limits stay real numbers). */
function inlineVariables(doc: any, vars: Record<string, unknown>): any {
  const out = cloneDoc(doc);
  const toValueNode = (v: unknown): any => {
    if (v === null) return { kind: 'NullValue' };
    if (typeof v === 'string') return { kind: 'StringValue', value: v };
    if (typeof v === 'number')
      return Number.isInteger(v)
        ? { kind: 'IntValue', value: String(v) }
        : { kind: 'FloatValue', value: String(v) };
    if (typeof v === 'boolean') return { kind: 'BooleanValue', value: v };
    if (Array.isArray(v)) return { kind: 'ListValue', values: v.map(toValueNode) };
    if (typeof v === 'object')
      return {
        kind: 'ObjectValue',
        fields: Object.entries(v as Record<string, unknown>).map(([k, val]) => ({
          kind: 'ObjectField',
          name: { kind: 'Name', value: k },
          value: toValueNode(val),
        })),
      };
    throw new Error(`unsupported variable value: ${String(v)}`);
  };
  const subValue = (node: any): any => {
    if (node.kind === 'Variable') {
      const name = node.name.value;
      if (!(name in vars)) throw new Error(`variable $${name} needs a value in the variables box`);
      return toValueNode(vars[name]);
    }
    if (node.kind === 'ListValue') return { ...node, values: node.values.map(subValue) };
    if (node.kind === 'ObjectValue')
      return { ...node, fields: node.fields.map((f: any) => ({ ...f, value: subValue(f.value) })) };
    return node;
  };
  const subSel = (selSet: any) => {
    for (const sel of selSet?.selections ?? []) {
      if (sel.arguments) sel.arguments = sel.arguments.map((a: any) => ({ ...a, value: subValue(a.value) }));
      if (sel.selectionSet) subSel(sel.selectionSet);
    }
  };
  for (const def of out.definitions) {
    if (def.kind !== 'OperationDefinition') continue;
    subSel(def.selectionSet);
    def.variableDefinitions = [];
  }
  return out;
}

/* ---------- Receipt shaping ---------- */

function shortResolver(rid: string): string {
  return rid.startsWith('Query.') ? rid.slice(6) : rid;
}

function receiptFromQuote(q: QuoteResult): {
  items: ReceiptItem[];
  shared: SharedGroup[];
  typical?: string;
  typicalLabel: string;
  total: string;
  totalLabel: string;
  totalFlagged: boolean;
  footer: React.ReactNode;
} {
  const items: ReceiptItem[] = [];
  const low = q.confidence === 'low';
  const unit = q.currency === 'work_ms' ? 'work-ms'
    : q.currency === 'wall_time_ms' ? 'wall-ms' : q.currency;

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
    items.push({ label: `${q.tier} pack: whole-query price only`, value: q.currency, muted: true });
  }
  const shared: SharedGroup[] = (q.sharing ?? []).map((s) => ({
    resolvers: ((s as any).folds as string[]).map(shortResolver),
    loader: (s as any).loader as string,
  }));
  for (const e of q.external_calls ?? []) {
    const calls = (e as any).calls ?? 1;
    items.push({
      label: `${shortResolver((e as any).resolver_id)} → ${(e as any).host}`,
      value: `${calls} call${calls === 1 ? '' : 's'} · app prices`,
      muted: true,
    });
  }
  if (low) {
    items.push({ label: 'cycle detected', value: '↺', muted: true });
  }

  return {
    items,
    shared,
    typical: q.typical_price != null ? (q.typical_price as number).toFixed(1) : undefined,
    typicalLabel: `typical (${unit})`,
    total: (q.price as number).toFixed(1),
    totalLabel: low ? `safe max (${unit}) · flagged` : `safe max (${unit})`,
    totalFlagged: low,
    footer: (
      <span>
        confidence: <strong className={low ? 'low' : 'ok'}>{q.confidence}</strong>
        {low ? ': this query can loop, so the price is a cautious ceiling' : ` · priced in ${q.currency}`}
      </span>
    ),
  };
}

/* ---------- Schema tree ---------- */

function TreeLevel({
  type,
  path,
  doc,
  valid,
  expanded,
  onExpand,
  onToggle,
}: {
  type: Composite;
  path: Path;
  doc: any;
  valid: boolean;
  expanded: Set<string>;
  onExpand: (key: string) => void;
  onToggle: (path: Path, present: boolean) => void;
}) {
  const fields = Object.values(type.getFields());
  return (
    <ul className="cql-tree__list">
      {fields.map((f) => {
        const named = compositeType(f);
        const p = [...path, f.name];
        const key = p.join('.');
        const present = valid && pathPresent(doc, p);
        const open = expanded.has(key);
        const argsig = f.args.length
          ? `(${f.args.map((a) => `${a.name}: ${String(a.type)}`).join(', ')})`
          : '';
        return (
          <li key={f.name}>
            <div className={`cql-tree__row${present ? ' cql-tree__row--in' : ''}`}>
              <button
                type="button"
                className={`cql-tree__add${present ? ' cql-tree__add--in' : ''}`}
                disabled={!valid}
                title={
                  !valid
                    ? 'fix the operation first'
                    : present
                      ? 'remove from the operation'
                      : 'add to the operation'
                }
                onClick={() => onToggle(p, present)}
              >
                {present ? '−' : '+'}
              </button>
              <span
                className="cql-tree__name"
                onClick={named ? () => onExpand(key) : undefined}
                title={`${f.name}${argsig}: ${String(f.type)}`}
              >
                {f.name}
                {argsig && <span className="cql-tree__args">{argsig}</span>}
              </span>
              <span
                className={`cql-tree__type${named ? ' cql-tree__type--link' : ''}`}
                onClick={named ? () => onExpand(key) : undefined}
              >
                {String(f.type)}
                {named && <span className="cql-tree__caret">{open ? ' ▾' : ' ▸'}</span>}
              </span>
            </div>
            {named && open && (
              <TreeLevel
                type={named}
                path={p}
                doc={doc}
                valid={valid}
                expanded={expanded}
                onExpand={onExpand}
                onToggle={onToggle}
              />
            )}
          </li>
        );
      })}
    </ul>
  );
}

/* ---------- The playground ---------- */

export default function Playground() {
  const [idx, setIdx] = useState(0);
  const [quote, setQuote] = useState<QuoteResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [running, setRunning] = useState(true);
  const [varsText, setVarsText] = useState(PACKS[0].vars);
  const [ast, setAst] = useState<any>(null); // last successfully parsed doc
  const [astValid, setAstValid] = useState(false);
  const [schemaObj, setSchemaObj] = useState<GraphQLSchema | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [pulse, setPulse] = useState(false);
  const prevTotalRef = useRef<string | null>(null);

  const editorHost = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const packRef = useRef<PricingPack | null>(null);
  const schemaRef = useRef<GraphQLSchema | null>(null);
  const varsRef = useRef(PACKS[0].vars);
  const cache = useRef(new Map<string, { pack: PricingPack; schema: GraphQLSchema }>());
  const debounceRef = useRef<number | undefined>(undefined);
  const suppressRef = useRef(false);

  varsRef.current = varsText;

  /** Parse + validate + inline variables + quote. The single pipeline behind
   * typing, the variables box, the tree, and the button. Quoting only happens
   * on a schema-valid operation with resolvable variables. */
  const analyze = () => {
    const view = viewRef.current;
    const pack = packRef.current;
    const schema = schemaRef.current;
    if (!view || !pack || !schema) return;
    const text = view.state.doc.toString().trim();

    if (!text) {
      setAst(emptyDoc());
      setAstValid(true);
      setProblem('add a field from the schema, or type a query');
      return;
    }

    let doc: DocumentNode;
    try {
      doc = parse(text);
    } catch (e) {
      setAstValid(false);
      setProblem(e instanceof Error ? e.message.replace(/^Syntax Error: /, 'syntax: ') : String(e));
      return;
    }
    setAst(doc);

    const verrs = validate(schema, doc);
    if (verrs.length) {
      setAstValid(true); // parseable: the tree can still edit it
      setProblem(verrs[0].message);
      return;
    }

    let vars: Record<string, unknown>;
    try {
      vars = JSON.parse(varsRef.current || '{}');
    } catch {
      setAstValid(true);
      setProblem('the variables box is not valid JSON');
      return;
    }

    try {
      const engineText = print(inlineVariables(doc, vars));
      setQuote(pack.quote(engineText));
      setAstValid(true);
      setProblem(null);
    } catch (e) {
      setAstValid(true);
      setProblem(e instanceof Error ? e.message : String(e));
    }
  };

  const scheduleAnalyze = () => {
    window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(analyze, 350); // settle after you pause, not per keystroke
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
          graphql(), // completion + schema lint: the red squiggles
          EditorView.updateListener.of((u) => {
            if (!u.docChanged || suppressRef.current) return;
            scheduleAnalyze();
          }),
        ],
      }),
    });
    return () => { viewRef.current?.destroy(); viewRef.current = null; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const info = PACKS[idx];
    setRunning(true); // genuine: the pack file may still be fetching
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
      schemaRef.current = entry.schema;
      setSchemaObj(entry.schema);
      setVarsText(info.vars);
      varsRef.current = info.vars;
      const view = viewRef.current;
      if (view) {
        updateSchema(view, entry.schema);
        suppressRef.current = true;
        view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: info.sample } });
        suppressRef.current = false;
      }
      try {
        setExpanded(new Set(presentCompositePaths(parse(info.sample))));
      } catch {
        setExpanded(new Set());
      }
      analyze();       // quote the sample immediately, no invented delay
      setRunning(false);
    })().catch((e) => { setProblem(String(e)); setRunning(false); });
    return () => { cancelled = true; };
  }, [idx]);

  const onExpand = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const onToggle = (path: Path, present: boolean) => {
    const view = viewRef.current;
    const schema = schemaRef.current;
    if (!view || !schema) return;
    const base = ast && astValid ? ast : emptyDoc();
    const next = present ? removeFieldAt(base, path) : addFieldAt(base, path, schema);
    const text = firstOp(next)?.selectionSet.selections.length ? print(next) : '';
    suppressRef.current = true;
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } });
    suppressRef.current = false;
    if (!present) setExpanded((prev) => new Set(prev).add(path.join('.')));
    analyze();
  };

  const r = quote ? receiptFromQuote(quote) : null;
  const queryType = schemaObj?.getQueryType() ?? null;

  /** When the price lands on a new value, pulse it once so the change registers
   * without the number twitching per keystroke. */
  const totalStr = running ? null : r?.total ?? null;
  useEffect(() => {
    if (totalStr == null) return;
    const prev = prevTotalRef.current;
    prevTotalRef.current = totalStr;
    if (prev == null || prev === totalStr) return; // first landing / unchanged: no pulse
    setPulse(true);
    const t = window.setTimeout(() => setPulse(false), 450);
    return () => window.clearTimeout(t);
  }, [totalStr]);

  return (
    <div className="cql-window">
      <div className="cql-window__bar">
        <div className="cql-window__title cql-window__title--left">playground: costql.quote()</div>
        <div className="cql-window__tools">
          <span
            className="cql-badge cql-badge--accent cql-tip"
            tabIndex={0}
            data-tip="every price here is computed on this page, from the pack file alone: no server, no network requests"
          >
            offline
          </span>
        </div>
      </div>
      <div className="cql-window__body--sunken">
        <div className="cql-play__tabbar">
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
          <div className="cql-play__dataset">
            <select
              className="cql-play__dataset-select"
              aria-label="data set"
              value={idx}
              onChange={(e) => setIdx(Number(e.target.value))}
            >
              {PACKS.map((p, i) => (
                <option key={p.id} value={i}>
                  {p.file}
                </option>
              ))}
            </select>
            <span className="cql-play__dataset-caret" aria-hidden="true">▾</span>
          </div>
        </div>
        <div className="cql-play__grid">
          <div className="cql-play__schema">
            <div className="cql-play__colhead">schema · click + to build</div>
            <div className="cql-tree">
              {queryType && (
                <TreeLevel
                  type={queryType}
                  path={[]}
                  doc={ast}
                  valid={astValid}
                  expanded={expanded}
                  onExpand={onExpand}
                  onToggle={onToggle}
                />
              )}
            </div>
          </div>

          <div className="cql-play__middle">
            <div className="cql-play__colhead">operation · ctrl-space autocompletes</div>
            <div className="cql-play__editor" ref={editorHost} />
            <div className="cql-play__colhead" style={{ marginTop: 10 }}>variables (json)</div>
            <textarea
              className="cql-play__vars"
              rows={2}
              spellCheck={false}
              value={varsText}
              onChange={(e) => {
                setVarsText(e.target.value);
                varsRef.current = e.target.value;
                scheduleAnalyze();
              }}
            />
            <div className="cql-play__run">
              <span className="cql-play__pack">pack: {PACKS[idx].file} · priced live, offline</span>
            </div>
            {problem && !running && <div className="cql-play__error">{problem}</div>}
          </div>

          <div className="cql-play__right">
            <div className="cql-play__colhead">quote</div>
            {running || !r ? (
              <div className="cql-play__waiting">
                measuring work-time<span className="cql-play__caret">▍</span>
              </div>
            ) : (
              <div className={problem ? 'cql-play__stale' : undefined}>
                <ReceiptView
                  title="costql quote"
                  meta={`${PACKS[idx].file} · offline`}
                  items={r.items}
                  shared={r.shared}
                  typical={r.typical}
                  typicalLabel={r.typicalLabel}
                  total={r.total}
                  totalLabel={r.totalLabel}
                  totalFlagged={r.totalFlagged}
                  footer={r.footer}
                  width={300}
                  pulse={pulse}
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
