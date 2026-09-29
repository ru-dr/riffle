#!/usr/bin/env python3
"""Generate contracts/REFERENCE.md from the schemas. Run: python contracts/gen_reference.py
CI runs it with --check and fails if REFERENCE.md is out of date."""
import json, sys, pathlib

HERE = pathlib.Path(__file__).parent
OUT = HERE / "REFERENCE.md"

def load(name):
    return json.loads((HERE / name).read_text())

def resolve(schema, node):
    while isinstance(node, dict) and "$ref" in node and len(node) <= 3:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            break
        target = schema
        for part in ref[2:].split("/"):
            target = target[part]
        extra = {k: v for k, v in node.items() if k != "$ref"}
        node = {**target, **extra}
    return node

def cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")

def summarize(schema, node):
    node = resolve(schema, node)
    if "const" in node:
        return f"`{node['const']}`"
    if "enum" in node:
        return ", ".join(f"`{v}`" for v in node["enum"])
    if "oneOf" in node:
        return " or ".join(summarize(schema, o) for o in node["oneOf"])
    t = node.get("type", "")
    if isinstance(t, list):
        t = " or ".join(t)
    if t == "array":
        return f"list of {summarize(schema, node.get('items', {}))}"
    rng = ""
    if "minimum" in node or "maximum" in node:
        rng = f" {node.get('minimum', '')}–{node.get('maximum', '')}"
    fmt = f" ({node['format']})" if "format" in node else ""
    pat = f" matching `{node['pattern']}`" if "pattern" in node and t == "string" else ""
    return f"{t}{rng}{fmt}{pat}".strip() or "any"

def default(node):
    if "default" not in node:
        return ""
    return f"`{json.dumps(node['default'])}`"

def flags(node):
    f = []
    if node.get("x-riffle-merge") == "union": f.append("union")
    if node.get("x-riffle-org-only"): f.append("org only")
    if node.get("x-riffle-phase") == "later": f.append("later")
    if node.get("x-riffle-decision-pending"): f.append("decision pending")
    return ", ".join(f)

def walk(schema, node, path, rows, inherited=""):
    node = resolve(schema, node)
    fl = ", ".join(x for x in [inherited, flags(node)] if x)
    props = node.get("properties")
    if node.get("type") == "object" and props:
        for k, v in props.items():
            walk(schema, v, f"{path}.{k}" if path else k, rows, fl)
    elif node.get("type") == "array" and isinstance(node.get("items"), dict) and resolve(schema, node["items"]).get("properties"):
        rows.append((path, "list of objects", default(node), fl, node.get("description", "")))
        walk(schema, node["items"], path + "[]", rows, fl)
    else:
        rows.append((path, summarize(schema, node), default(node), fl, node.get("description", "")))

def table(rows, head=("Key", "Allowed", "Default", "Flags", "Meaning")):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join([f"`{cell(r[0])}`"] + [cell(x) for x in r[1:]]) + " |")
    return "\n".join(out)

def rules_section(R):
    md = ["## Rules (`org_rules.schema.json`)", "", R["description"], ""]
    md += ["### Fields every rule accepts", ""]
    base = R["$defs"]["base"]
    rows = []
    for k, v in base["properties"].items():
        walk(R, v, k, rows)
    md += [table(rows), ""]
    md += ["### Rule kinds", ""]
    kinds = [o["$ref"].split("/")[-1] for o in R["$defs"]["rule"]["oneOf"]]
    for k in kinds:
        d = R["$defs"][k]
        md += [f"#### `{k}`", ""]
        if d.get("description"): md += [d["description"], ""]
        meta = []
        if d.get("x-riffle-sink"): meta.append(f"**Sink:** {d['x-riffle-sink']}")
        if d.get("x-riffle-compiles-to"): meta.append("**Compiles to:** " + ", ".join(f"`{c}`" for c in d["x-riffle-compiles-to"]))
        if d.get("x-riffle-phase"): meta.append(f"**Phase:** {d['x-riffle-phase']}")
        if meta: md += ["  \n".join(meta), ""]
        req = set(d.get("required", []))
        rows = []
        for p, v in d["properties"].items():
            if p == "kind": continue
            v = resolve(R, v)
            rows.append((p, summarize(R, v), default(v), "required" if p in req else "", v.get("description", "")))
        md += [table(rows, ("Field", "Allowed", "Default", "Required", "Meaning")), ""]
        notes = d.get("x-riffle-phase-notes")
        if notes:
            md += ["Phase notes: " + "; ".join(f"`{a}` {b}" for a, b in notes.items()), ""]
    for block in ["sources", "labels", "mining", "model"]:
        rows = []
        walk(R, R["properties"][block], block, rows)
        md += [f"### `{block}` block", "", table(rows), ""]
    md += ["### Declared model columns", "", "Every rule compiles to these fixed columns. `null` when undeclared.", "",
           table([(k, v) for k, v in R["x-riffle-model-columns"].items()], ("Column", "Meaning")), ""]
    return md

def settings_section(S):
    md = ["## Settings (`org_config.schema.json`)", "", S["description"], ""]
    for k, v in S["properties"].items():
        if k == "overrides":
            md += ["### `overrides`", "", resolve(S, v).get("description", ""), "",
                   "Each entry has `match` (`component` or `paths`) and may set `rank_bands`, `pr_output`, `reviewer_routing`, `review_targets` with the same keys as the top-level blocks.", ""]
            continue
        rows = []
        walk(S, v, k, rows)
        md += [f"### `{k}`", "", table(rows), ""]
    md += ["### Code checks", "", "Cross-field rules the schema cannot express:", ""]
    md += [f"- {c}" for c in S.get("x-riffle-code-checks", [])] + [""]
    return md

def mined_section(M):
    md = ["## Mined features (`mined_features.schema.json`)", "", M["description"], ""]
    for block in ["columns", "evidence"]:
        rows = []
        walk(M, M["properties"][block], block, rows)
        md += [f"### `{block}`", "", table(rows), ""]
    return md

def import_section(I):
    md = ["## Outcome import (`outcome_import.schema.json`)", "", I["description"], ""]
    rows = []
    for k, v in I["properties"].items():
        walk(I, v, k, rows)
    return md + [table(rows), ""]

def main():
    files = {p.name: p for p in HERE.glob("*.schema.json")}
    def pick(*names):
        for n in names:
            if n in files: return load(n)
        sys.exit(f"missing one of {names}")
    R = pick("org_rules.schema.json", "org-rules.schema.json")
    S = pick("org_config.schema.json", "org-config.schema.json")
    M = pick("mined_features.schema.json", "mined-features.schema.json")
    I = pick("outcome_import.schema.json", "outcome-import.schema.json")
    md = ["# Contracts reference", "",
          "Generated from the schemas by `gen_reference.py`. Do not edit by hand.", "",
          "- [Rules](#rules-org_rulesschemajson)", "- [Settings](#settings-org_configschemajson)",
          "- [Mined features](#mined-features-mined_featuresschemajson)",
          "- [Outcome import](#outcome-import-outcome_importschemajson)", ""]
    md += rules_section(R) + settings_section(S) + mined_section(M) + import_section(I)
    text = "\n".join(md).rstrip() + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            sys.exit("REFERENCE.md is out of date. Run python contracts/gen_reference.py")
        print("REFERENCE.md up to date")
        return
    OUT.write_text(text)
    print(f"wrote {OUT} ({len(text.splitlines())} lines)")

if __name__ == "__main__":
    main()
