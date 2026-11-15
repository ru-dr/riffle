"""build_notebook.py - pipeline_src.py (cells marked "# %%") -> ../../pipeline.ipynb, with the frozen feature lists filled in.

    python build_notebook.py frozen_features.json      # {"miner": [...], "engineered": [...]}, the 161 frozen inputs
"""
import json
import os
import sys

import nbformat

HERE = os.path.dirname(os.path.abspath(__file__))
feats = json.load(open(sys.argv[1]))
src = open(os.path.join(HERE, "pipeline_src.py")).read()


def wrap(names, indent=4, width=118):
    lines, cur = [], "["
    for n in names:
        piece = repr(n) + ", "
        if len(cur) + len(piece) > width:
            lines.append(cur.rstrip())
            cur = " " * indent
        cur += piece
    return "\n".join(lines + [cur.rstrip().rstrip(",") + "]"])


src = src.replace("__MINER__", wrap(feats["miner"])).replace("__ENGINEERED__", wrap(feats["engineered"]))
cells, kind, buf = [], None, []


def flush():
    if kind is None:
        return
    text = "\n".join(buf).strip("\n")
    if kind == "md":
        text = "\n".join(l[2:] if l.startswith("# ") else l[1:] if l.startswith("#") else l for l in text.splitlines())
        cells.append(nbformat.v4.new_markdown_cell(text))
    else:
        cells.append(nbformat.v4.new_code_cell(text))


for line in src.splitlines():
    if line.startswith("# %% [markdown]"):
        flush()
        kind, buf = "md", []
    elif line.startswith("# %%"):
        flush()
        kind, buf = "code", []
    else:
        buf.append(line)
flush()
nb = nbformat.v4.new_notebook(cells=cells)
nb.metadata["kernelspec"] = {"name": "riffle", "display_name": "riffle", "language": "python"}
nbformat.write(nb, os.path.join(HERE, "..", "..", "pipeline.ipynb"))
print(f"pipeline.ipynb: {len(cells)} cells")
