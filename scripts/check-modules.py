#!/usr/bin/env python3
"""Every name a module's functions use must resolve inside that module.

WHY THIS EXISTS. Splitting setup.py moved functions without their dependencies
twice, and both times importing the module proved nothing: a name used only
inside a function body is not looked up until the function runs. So `import
embabel_setup.colour` succeeded while banner_art() raised NameError on
APPLIANCE_DIR — during a real install, at the first line the user sees.

This walks every module's ASTs and resolves each free name against the module's
globals, its imports, builtins, and its own locals. It is the check that would
have caught both.
"""
import ast, builtins, os, sys

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "embabel_setup")
BUILTIN = set(dir(builtins))


def bound_at_module(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.Import):
            names |= {(a.asname or a.name.split(".")[0]) for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {(a.asname or a.name) for a in node.names}
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names |= set(node.names)
    return names


def locals_of(fn):
    """Everything bound anywhere inside this function, INCLUDING inside nested
    functions, lambdas and comprehensions. A nested def's parameters are not
    free variables of the outer one, and treating them as such reported a dozen
    problems that were not."""
    names = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
    if fn.args.vararg: names.add(fn.args.vararg.arg)
    if fn.args.kwarg: names.add(fn.args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = node.args
            names |= {p.arg for p in a.args + a.kwonlyargs + a.posonlyargs}
            if a.vararg: names.add(a.vararg.arg)
            if a.kwarg: names.add(a.kwarg.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name.split(".")[0]) for a in node.names}
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name): names.add(t.id)
    return names


problems = []
for filename in sorted(os.listdir(PKG)):
    if not filename.endswith(".py"):
        continue
    tree = ast.parse(open(os.path.join(PKG, filename)).read(), filename)
    module_names = bound_at_module(tree) | BUILTIN
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        known = module_names | locals_of(node)
        for used in ast.walk(node):
            if isinstance(used, ast.Name) and isinstance(used.ctx, ast.Load) and used.id not in known:
                problems.append(f"{filename}:{used.lineno} {node.name}() uses '{used.id}'")

# A `global` that names something the file does not define at module level.
# Inside one big file that is merely untidy; across modules it is a silent
# no-op — the wizard's `global _ACCOUNT` bound a name in setup.py while the
# reader in seed.py stayed None, and every install reported "no credential".
for filename in ["../setup.py"] + sorted(os.listdir(PKG)):
    path = os.path.join(PKG, filename)
    if not filename.endswith(".py") or not os.path.exists(path):
        continue
    tree = ast.parse(open(path).read(), filename)
    at_module = bound_at_module(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            for name in node.names:
                if name not in at_module:
                    problems.append(
                        f"{filename}:{node.lineno} `global {name}` but {filename} "
                        f"does not define it — this binds a new name here, not there")

for p in sorted(set(problems)):
    print(f"  ✗ {p}")
print(f"  {'✓ every name resolves in its own module' if not problems else f'{len(set(problems))} unresolved name(s)'}")
_unresolved = len(set(problems))


# ── do the imports name things that exist? ──────────────────────────────────
#
# The check above proves every name a module USES resolves to something. It said
# yes while `from .settings import instance_ports` was in the tree — because it
# trusted the import line to be true. A wrong name there is an ImportError at
# first run, which is exactly the class of failure this script exists to catch
# before a user does.
def _check_intra_package_imports() -> int:
    import ast as _ast
    import os as _os

    package = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                            "embabel_setup")
    exported: dict[str, set[str]] = {}
    trees: dict[str, _ast.Module] = {}
    for entry in sorted(_os.listdir(package)):
        if not entry.endswith(".py"):
            continue
        name = entry[:-3]
        tree = _ast.parse(open(_os.path.join(package, entry), encoding="utf-8").read())
        trees[name] = tree
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, _ast.Assign):
                names.update(t.id for t in node.targets if isinstance(t, _ast.Name))
            elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
                names.add(node.target.id)
            elif isinstance(node, (_ast.Import, _ast.ImportFrom)):
                names.update((a.asname or a.name).split(".")[0] for a in node.names)
        exported[name] = names

    bad = 0
    for module, tree in trees.items():
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom) and node.level == 1 and node.module in exported:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    if alias.name not in exported[node.module]:
                        print(f"  ✗ {module}.py imports '{alias.name}' from "
                              f"{node.module}.py, which does not define it")
                        bad += 1
    if bad == 0:
        print("  ✓ every intra-package import names something that exists")
    return bad


_bad_imports = _check_intra_package_imports()
sys.exit(1 if (_unresolved or _bad_imports) else 0)
