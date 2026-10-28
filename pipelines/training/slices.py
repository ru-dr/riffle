"""
slices.py — label balance per slice of the mined dataset, to spot groups the model would learn badly.

    python slices.py dataset_merged.parquet [--out slices.csv] [--min-rows 200] [--min-observed 50] [--strict]

For each slice column it reports, per value: rows, share of all rows, merged rate, how many rows
have an observed label_bad, label_bad rate over those rows only, and label coverage. A slice is
flagged `small` (fewer rows than --min-rows) or `thin_labels` (fewer observed label_bad rows than
--min-observed). Rows with label_bad_observed null or false never count as "not bad": the bad rate is
null when nothing is observed. Exit code is 0 unless --strict is given and a slice is flagged.

The slice columns are data (DEFAULT_SLICES), not repository rules. A column the table lacks is skipped
and listed. `size_quartile` is derived from LA + LD.
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import frames  # noqa: E402

DEFAULT_SLICES = ("author_association", "author_is_bot", "ai_agent_authored", "domain", "repo", "size_quartile")
COLUMNS = ("slice", "value", "rows", "share", "merged_rate", "bad_observed", "bad_rate", "coverage", "flags")


def _true(s: pd.Series) -> pd.Series:
    """True only where the value is exactly True; null and False are both not observed."""
    return s.eq(True).fillna(False)


def with_size_quartile(df: pd.DataFrame) -> pd.DataFrame:
    """Add size_quartile (Q1 smallest .. Q4 largest) from LA + LD; null when either is missing or all sizes tie."""
    if not {"LA", "LD"} <= set(df.columns):
        return df
    size = pd.to_numeric(df["LA"], errors="coerce") + pd.to_numeric(df["LD"], errors="coerce")
    try:
        q = pd.qcut(size, 4, labels=["Q1", "Q2", "Q3", "Q4"], duplicates="drop")
    except ValueError:
        return df
    return df.assign(size_quartile=q.astype("object"))


def slice_table(df: pd.DataFrame, slices=DEFAULT_SLICES, min_rows: int = 200, min_observed: int = 50):
    """(table, skipped): one row per slice value, and the slice names the data had no column for."""
    df = with_size_quartile(df)
    total = len(df)
    observed = _true(df["label_bad_observed"]) if "label_bad_observed" in df else pd.Series(False, index=df.index)
    bad = _true(df["label_bad"]) if "label_bad" in df else pd.Series(False, index=df.index)
    merged = df["label_merged"].map(lambda v: None if pd.isna(v) else bool(v)) if "label_merged" in df else None
    out, skipped = [], []
    for name in slices:
        if name not in df.columns or total == 0:
            skipped.append(name)
            continue
        key = df[name].astype("object").where(df[name].notna(), "(null)").astype(str)
        for value, idx in key.groupby(key).groups.items():
            n, obs = len(idx), int(observed[idx].sum())
            m = merged[idx].dropna().astype(float) if merged is not None else pd.Series(dtype=float)
            flags = [f for f, hit in (("small", n < min_rows), ("thin_labels", obs < min_observed)) if hit]
            out.append({"slice": name, "value": value, "rows": n, "share": n / total,
                        "merged_rate": float(m.mean()) if len(m) else None, "bad_observed": obs,
                        "bad_rate": float(bad[idx][observed[idx]].mean()) if obs else None,
                        "coverage": obs / n, "flags": ",".join(flags)})
    table = pd.DataFrame(out, columns=COLUMNS)
    return table.sort_values(["slice", "rows"], ascending=[True, False], ignore_index=True), skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dataset", help="the dataset table (.parquet or .jsonl)")
    ap.add_argument("--out", help="write the table here (.csv)")
    ap.add_argument("--slices", nargs="+", default=list(DEFAULT_SLICES))
    ap.add_argument("--min-rows", type=int, default=200)
    ap.add_argument("--min-observed", type=int, default=50)
    ap.add_argument("--strict", action="store_true", help="exit 1 when any slice is flagged")
    a = ap.parse_args(argv)
    try:
        df = frames._read(a.dataset)
    except (OSError, ValueError) as e:
        print(f"slices: cannot read {a.dataset}: {e}", file=sys.stderr)
        return 2
    table, skipped = slice_table(df, a.slices, a.min_rows, a.min_observed)
    if a.out:
        table.to_csv(a.out, index=False)
    with pd.option_context("display.width", 200, "display.max_rows", 500, "display.float_format", "{:.3f}".format):
        print(table.to_string(index=False))
    if skipped:
        print(f"skipped (no column): {', '.join(skipped)}", file=sys.stderr)
    flagged = table[table["flags"] != ""]
    print(f"{len(flagged)} of {len(table)} slices flagged", file=sys.stderr)
    return 1 if a.strict and len(flagged) else 0


if __name__ == "__main__":
    sys.exit(main())
