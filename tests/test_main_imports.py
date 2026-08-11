"""Every name main.py imports from a module must actually exist there.

This is the check that was missing when `get_tasks_by_date_range` was dropped
from modules/tasks.py while main.py still imported it: the unit tests all
passed, because they import individual handlers rather than main, and the
mismatch only surfaced as an ImportError when the container started — after a
successful build, as a failed health check.

The check parses the source instead of importing it, so it needs no Firebase
credentials (main.py calls initialize_app at module level).
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _module_path(module: str) -> pathlib.Path:
    return ROOT.joinpath(*module.split(".")).with_suffix(".py")


def _toplevel_names(path: pathlib.Path) -> set:
    """Functions, classes and assignments defined at the top level of a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _local_imports(entry: pathlib.Path):
    """(module, name) pairs imported from the repo's own `modules` package."""
    tree = ast.parse(entry.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] != "modules":
                continue
            for alias in node.names:
                yield node.module, alias.name


ENTRY_POINTS = ["main.py", "app.py"]


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_entry_point_imports_resolve(entry):
    path = ROOT / entry
    missing = []
    for module, name in _local_imports(path):
        target = _module_path(module)
        assert target.exists(), f"{entry} imports from missing module {module}"
        if name not in _toplevel_names(target):
            missing.append(f"{module}.{name}")

    assert not missing, (
        f"{entry} imports names that do not exist: {', '.join(sorted(missing))}. "
        "The container would fail to start with an ImportError."
    )


def test_every_routed_handler_is_imported():
    """Handlers named in the router must be in scope, or the call NameErrors."""
    main_path = ROOT / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    in_scope = _toplevel_names(main_path)

    route = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "route_request"
    )
    called = {
        node.func.id
        for node in ast.walk(route)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    # Locals and builtins used inside the router are not handlers.
    called -= set(route.args.args and [a.arg for a in route.args.args]) | {
        "jsonify",
        "isinstance",
        "len",
        "str",
    }

    assert called <= in_scope, (
        "route_request calls names that are not imported: "
        f"{', '.join(sorted(called - in_scope))}"
    )
