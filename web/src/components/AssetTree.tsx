import { useState } from "react";

import type { AssetNode } from "../api";
import { href } from "../hooks";
import { HealthBadge } from "./Status";

/** Árbol de activos. Los grupos grandes (p. ej. 40 polines) arrancan plegados. */
export function AssetTree({ nodes, selected }: { nodes: AssetNode[]; selected?: string }) {
  return (
    <ul className="tree" role="tree">
      {nodes.map((n) => (
        <TreeNode key={n.code} node={n} selected={selected} depth={0} />
      ))}
    </ul>
  );
}

function TreeNode({ node, selected, depth }: { node: AssetNode; selected?: string; depth: number }) {
  const [open, setOpen] = useState(node.children.length <= 8 || depth < 1);
  const hasKids = node.children.length > 0;
  return (
    <li role="treeitem" aria-expanded={hasKids ? open : undefined} aria-selected={selected === node.code}>
      <div className="tree-row" style={selected === node.code ? { background: "var(--surface-2)" } : undefined}>
        {hasKids ? (
          <button className="tree-toggle" onClick={() => setOpen(!open)} aria-label={open ? "Plegar" : "Desplegar"}>
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span style={{ width: 18 }} />
        )}
        <a href={href("activo", node.code)} className="name" style={{ textDecoration: "none" }}>
          {node.name} <span className="code">{node.code}</span>
        </a>
        {hasKids && !open && <span className="muted" style={{ fontSize: 12 }}>{node.children.length}</span>}
        <HealthBadge value={node.health_index} compact />
      </div>
      {hasKids && open && (
        <ul className="tree" role="group">
          {node.children.map((c) => (
            <TreeNode key={c.code} node={c} selected={selected} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

/** Aplana el árbol (útil para buscar un activo por código). */
export function flatten(nodes: AssetNode[]): AssetNode[] {
  return nodes.flatMap((n) => [n, ...flatten(n.children)]);
}
