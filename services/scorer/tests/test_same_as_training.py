"""The scorer runs the model with the training notebook's own code and library versions.

predict.py holds a copy of the notebook's model-running functions (the notebook source is a template that cannot be
imported). These tests fail if the copy and the notebook drift apart, or if the scorer's model libraries stop
matching the training image's.
"""
import ast
import re

from conftest import ROOT, HERE

NOTEBOOK = ROOT / "pipelines" / "training" / "outcome" / "experiments" / "notebook_build" / "pipeline_src.py"
PREDICT = HERE.parent / "scorer" / "predict.py"
FUNCTIONS = ("_flag", "apply_vector", "apply_blend", "member_scores", "predict_bundle", "prediction_columns",
             "load_bundle")
METHODS = {"Encoder": ("transform",), "Dense": ("_log", "transform", "from_dict")}
CONSTANTS = ("REASONS", "CLOSE_NAME", "BINARY_TASKS", "MEMBERS", "CATEGORICAL")
# The one deliberate difference: the scorer's Encoder takes the feature list from the model's meta.json instead of
# the notebook's FEATURES constant, so a model always reads the columns it was trained on, in its order.
KNOWN = [("Encoder(meta[\"categorical_levels\"])", "Encoder(meta[\"features\"], meta[\"categorical_levels\"])"),
         ("for c in FEATURES:", "for c in self.features:")]


def defs(source):
    tree = ast.parse(source)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for m in node.body:
                if isinstance(m, ast.FunctionDef):
                    out[f"{node.name}.{m.name}"] = m
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
    return out


def dump(node):
    if isinstance(node, ast.FunctionDef):
        body = node.body[1:] if ast.get_docstring(node) else node.body
        node = ast.FunctionDef(name=node.name, args=node.args, body=body, decorator_list=node.decorator_list,
                               returns=None, type_comment=None, type_params=[])
    return ast.dump(node, annotate_fields=False)


def test_the_model_code_is_the_notebooks():
    nb = NOTEBOOK.read_text(encoding="utf-8")
    for old, new in KNOWN:
        assert old in nb, f"the notebook changed near {old!r}: update predict.py and this test"
        nb = nb.replace(old, new)
    want, have = defs(nb), defs(PREDICT.read_text(encoding="utf-8"))
    names = list(FUNCTIONS) + [f"{c}.{m}" for c, ms in METHODS.items() for m in ms] + list(CONSTANTS)
    differ = [n for n in names if n not in have or dump(want[n]) != dump(have[n])]
    assert not differ, f"predict.py differs from the training notebook in: {differ}"


def pins(path):
    return dict(re.findall(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s#]+)", path.read_text(encoding="utf-8"), re.M))


def test_the_model_libraries_are_the_training_versions():
    training = pins(ROOT / "infra" / "docker" / "outcome-training" / "requirements.txt")
    scorer = pins(HERE.parent / "requirements.txt")
    for lib in ("numpy", "pandas", "pyarrow", "scipy", "scikit-learn", "lightgbm", "xgboost"):
        assert scorer.get(lib) == training.get(lib), f"{lib}: scorer {scorer.get(lib)}, training {training.get(lib)}"
