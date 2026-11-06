"""Best-effort AST guard for PM 46's immutable published-model contract.

Tracks annotations, constructor/annotated-call results, store getters, aliases,
container indexing/iteration and list-field aliases. This is a regression guard,
not a whole-program type checker: dynamic reflection and untyped external helper
returns still require review. No producer file is exempted by an allowlist.
"""
import ast
from pathlib import Path

import pytest

from app.models.market import MarketSnapshot
from app.models.opportunity import Opportunity


MODELS = {"MarketSnapshot", "Opportunity"}
MUTATORS = {"append", "extend", "insert", "clear", "pop", "remove", "reverse", "sort",
            "__setitem__", "__delitem__", "__iadd__", "__imul__"}
LIST_FIELDS = {
    cls.__name__: {name for name, field in cls.model_fields.items() if "list[" in str(field.annotation)}
    for cls in (MarketSnapshot, Opportunity)
}


def leaf_name(node):
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""


def annotation(node, aliases):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return annotation(ast.parse(node.value, mode="eval").body, aliases)
    name = aliases.get(leaf_name(node), leaf_name(node))
    if name in MODELS:
        return name
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return annotation(node.left, aliases) or annotation(node.right, aliases)
    if isinstance(node, ast.Subscript):
        parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        kinds = [annotation(part, aliases) for part in parts]
        if name in {"Optional", "Union", "Annotated"}:
            return next((kind for kind in kinds if kind), None)
        element = kinds[-1] if name in {"dict", "Mapping"} else next((kind for kind in kinds if kind), None)
        if element:
            return ("container", element)
    return None


def mutation_errors(sources):
    trees = {name: ast.parse(source, filename=name) for name, source in sources.items()}
    aliases = {node.asname or node.name: node.name for tree in trees.values()
               for node in ast.walk(tree) if isinstance(node, ast.alias)}
    returns = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = annotation(node.returns, aliases)
                if kind:
                    returns[node.name] = kind
    returns.update(get_markets=("container", "MarketSnapshot"),
                   get_all_markets=("container", "MarketSnapshot"),
                   get_opportunities=("container", "Opportunity"))
    errors = set()

    class Guard(ast.NodeVisitor):
        def __init__(self, filename):
            self.filename, self.env = filename, {}

        def kind(self, node):
            if isinstance(node, ast.Name):
                return self.env.get(node.id)
            if isinstance(node, ast.Await):
                return self.kind(node.value)
            if isinstance(node, ast.Attribute):
                base = self.kind(node.value)
                if isinstance(base, str) and base in MODELS:
                    if node.attr in LIST_FIELDS[base] or node.attr == "__dict__":
                        return "field"
                return self.env.get(ast.unparse(node))
            if isinstance(node, ast.Subscript):
                base = self.kind(node.value)
                if isinstance(node.slice, ast.Slice):
                    return base
                return base[1] if isinstance(base, tuple) else None
            if isinstance(node, (ast.List, ast.Tuple)):
                kind = next((self.kind(n) for n in node.elts if self.kind(n)), None)
                return ("container", kind) if kind else None
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                for gen in node.generators:
                    self.bind_iteration(gen.target, gen.iter)
                kind = self.kind(node.elt)
                return ("container", kind) if kind else None
            if isinstance(node, ast.Call):
                name = aliases.get(leaf_name(node.func), leaf_name(node.func))
                if name in MODELS:
                    return name
                if isinstance(node.func, ast.Attribute):
                    base = self.kind(node.func.value)
                    if name == "model_copy":
                        return base
                    if name in {"values", "get"} and isinstance(base, tuple):
                        return base if name == "values" else base[1]
                if name in {"list", "tuple", "sorted", "iter"} and node.args:
                    base = self.kind(node.args[0])
                    return None if base == "field" else base
                if name == "next" and node.args:
                    base = self.kind(node.args[0])
                    return base[1] if isinstance(base, tuple) else None
                return returns.get(name)
            return None

        def bind(self, target, kind):
            if isinstance(target, ast.Name) and kind:
                self.env[target.id] = kind
            elif isinstance(target, ast.Attribute) and kind:
                self.env[ast.unparse(target)] = kind
            elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(kind, tuple):
                for part in target.elts:
                    self.bind(part, kind[1])

        def bind_iteration(self, target, source):
            if isinstance(source, ast.Call) and leaf_name(source.func) == "enumerate" and source.args:
                if isinstance(target, (ast.Tuple, ast.List)):
                    return self.bind_iteration(target.elts[-1], source.args[0])
            kind = self.kind(source)
            if isinstance(kind, tuple):
                self.bind(target, kind[1])

        def report(self, node):
            errors.add(f"{self.filename}:{node.lineno}: {ast.unparse(node)}")

        def visit_ClassDef(self, node):
            outer, self.env = self.env, self.env.copy()
            for child in ast.walk(node):
                if isinstance(child, ast.AnnAssign):
                    kind = annotation(child.annotation, aliases)
                    if kind and isinstance(child.target, ast.Attribute):
                        self.bind(child.target, kind)
            self.generic_visit(node)
            self.env = outer

        def check_target(self, target):
            if isinstance(target, ast.Attribute) and self.kind(target.value) in MODELS:
                self.report(target)
            elif isinstance(target, ast.Subscript) and self.kind(target.value) == "field":
                self.report(target)
            elif isinstance(target, (ast.Tuple, ast.List)):
                for part in target.elts:
                    self.check_target(part)

        def visit_FunctionDef(self, node):
            outer, self.env = self.env, self.env.copy()
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                kind = annotation(arg.annotation, aliases)
                if kind:
                    self.env[arg.arg] = kind
            # A few fixed-point passes cover aliases declared in branches and
            # loops without relying on variable names such as market/op/quote.
            for _ in range(3):
                for statement in node.body:
                    self.visit(statement)
            self.env = outer

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Assign(self, node):
            for target in node.targets:
                self.check_target(target)
                self.bind(target, self.kind(node.value))
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            self.check_target(node.target)
            self.bind(node.target, annotation(node.annotation, aliases) or self.kind(node.value))
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            self.check_target(node.target)
            if self.kind(node.target) == "field":
                self.report(node)
            self.generic_visit(node)

        def visit_Delete(self, node):
            for target in node.targets:
                self.check_target(target)

        def visit_For(self, node):
            self.bind_iteration(node.target, node.iter)
            self.generic_visit(node)

        visit_AsyncFor = visit_For

        def visit_comprehension(self, node):
            self.bind_iteration(node.target, node.iter)
            self.generic_visit(node)

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in MUTATORS and self.kind(node.func.value) == "field":
                    self.report(node)
            if leaf_name(node.func) in {"setattr", "delattr"} and node.args and self.kind(node.args[0]) in MODELS:
                self.report(node)
            self.generic_visit(node)

    for filename, tree in trees.items():
        Guard(filename).visit(tree)
    return sorted(errors)


def test_production_does_not_mutate_snapshot_models():
    app = Path(__file__).resolve().parents[1] / "app"
    sources = {path.relative_to(app).as_posix(): path.read_text(encoding="utf-8-sig")
               for path in app.rglob("*.py")
               if "tests" not in path.parts and not any("shadow" in part for part in path.relative_to(app).parts)}
    assert "services/snapshot_store.py" in sources
    assert mutation_errors(sources) == []


@pytest.mark.parametrize("body", [
    "odd.bid = 1", "odd.ask += 1", "del odd.bid", "odd.estimated_fields.append('bid')",
    "alias = odd\nalias.estimated_fields.extend(['ask'])",
    "items = odd.estimated_fields\nitems.clear()",
    "odd.estimated_fields[0] = 'ask'", "del odd.estimated_fields[:1]",
    "odd.estimated_fields += ['bid']", "setattr(odd, 'bid', 1)",
    "odd.__dict__['bid'] = 1",
])
def test_guard_detects_annotated_model_and_field_alias_mutations(body):
    source = "def f(odd: MarketSnapshot):\n" + "\n".join("    " + line for line in body.splitlines())
    assert mutation_errors({"case.py": source})


@pytest.mark.parametrize("body", [
    "odd = Opportunity()\nodd.fake_reasons.clear()",
    "odd = store.get_opportunities()[0]\nodd.buy_routes.append('bad')",
    "for odd in store.get_markets():\n    odd.bid = 1",
    "for i, odd in enumerate(store.get_opportunities()):\n    odd.sell_routes.pop()",
    "odd = build()\nodd.symbol = 'bad'",
    "batch: list[MarketSnapshot] = []\nodd = batch[0]\nodd.bid = 1",
    "batch = [item for item in store.get_markets()]\nbatch[0].ask = 2",
    "odd: 'Opportunity'\nodd.fake_reasons.sort()",
])
def test_guard_tracks_constructors_getters_annotations_and_call_results(body):
    sources = {"factory.py": "def build() -> Opportunity: ...", "case.py": body}
    assert mutation_errors(sources)


def test_guard_allows_replacement_and_unrelated_mutable_models():
    assert mutation_errors({"case.py": """
def f(odd: MarketSnapshot, other: OrderBookSnapshot):
    new = odd.model_copy(update={'bid': 1})
    fields = list(odd.estimated_fields)
    fields.append('bid')
    batch = [new]
    batch.append(odd)
    store.set_markets(batch)
    other.bid = 2
"""}) == []
