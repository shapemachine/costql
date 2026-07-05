import type { GraphQLSchema } from 'graphql';
import {
  GraphQLObjectType,
  getNamedType,
  isLeafType,
  isObjectType,
} from 'graphql';
import React, { useState } from 'react';

/** Clickable schema tree: expand types, click a field to write it into the
 * editor at the cursor. Composite fields insert `name { }` with the cursor
 * placed inside; required args get placeholder values. */

function snippetFor(field: any): string {
  const named = getNamedType(field.type);
  const args = field.args
    .filter((a: any) => a.type.toString().endsWith('!') && a.defaultValue === undefined)
    .map((a: any) => {
      const t = getNamedType(a.type).name;
      const v = t === 'Int' || t === 'Float' ? '1' : t === 'Boolean' ? 'true' : '"1"';
      return `${a.name}: ${v}`;
    });
  const argStr = args.length ? `(${args.join(', ')})` : '';
  return isLeafType(named) ? `${field.name}${argStr}` : `${field.name}${argStr} {  }`;
}

function FieldRow({
  field,
  depth,
  onInsert,
}: {
  field: any;
  depth: number;
  onInsert: (snippet: string, composite: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const named = getNamedType(field.type);
  const composite = isObjectType(named);
  const isList = field.type.toString().includes('[');
  return (
    <div className="cq-tree-node" style={{ marginLeft: depth * 14 }}>
      <span
        className={`cq-tree-toggle ${composite ? '' : 'cq-tree-leafmark'}`}
        onClick={() => composite && setOpen(!open)}
      >
        {composite ? (open ? '▾' : '▸') : '·'}
      </span>
      <button
        className="cq-tree-field"
        title={`insert ${field.name} at cursor`}
        onClick={() => onInsert(snippetFor(field), composite)}
      >
        {field.name}
      </button>
      <span className="cq-tree-type">
        {isList ? '[' : ''}
        {named.name}
        {isList ? ']' : ''}
      </span>
      {open && composite && (
        <div>
          {Object.values((named as GraphQLObjectType).getFields()).map((f) => (
            <FieldRow key={f.name} field={f} depth={depth + 1} onInsert={onInsert} />
          ))}
        </div>
      )}
    </div>
  );
}

export function SchemaTree({
  schema,
  onInsert,
}: {
  schema: GraphQLSchema;
  onInsert: (snippet: string, composite: boolean) => void;
}) {
  const query = schema.getQueryType();
  if (!query) return null;
  return (
    <div className="cq-tree">
      <div className="cq-tree-title">schema — click a field to insert it</div>
      {Object.values(query.getFields()).map((f) => (
        <FieldRow key={f.name} field={f} depth={0} onInsert={onInsert} />
      ))}
    </div>
  );
}
