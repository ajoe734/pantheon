"""BFF-ASYNC-ROUTE-NO-AWAIT-SWEEP-20261103: no route-decorated ``async def`` without an await.

An ``async def`` route that never awaits runs its (blocking) body on the event
loop and stalls every other request, including /livez.  Such handlers must be
plain ``def`` so FastAPI serves them from the threadpool.
"""
from __future__ import annotations

import ast
from pathlib import Path

BFF_ROOT = Path(__file__).resolve().parents[1]
ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route"}

# SSE endpoints return StreamingResponse over async generators and are meant to
# live on the event loop: (path relative to BFF root, function name) -> reason.
ASYNC_NO_AWAIT_ALLOWLIST = {
    ("agora/interaction/router.py", "stream_interaction"): "SSE StreamingResponse",
    ("agora/strategy_workshop/routes/stream.py", "stream_workshop"): "SSE StreamingResponse",
    ("agora/trading_room/routes/decisions.py", "stream_trading_room"): "SSE StreamingResponse",
}


def _is_route(fn: ast.AsyncFunctionDef) -> bool:
    for decorator in fn.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr in ROUTE_METHODS:
            return True
    return False


def _has_await(fn: ast.AsyncFunctionDef) -> bool:
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue  # awaits in nested scopes do not make this handler yield
        stack.extend(ast.iter_child_nodes(node))
    return False


def _offenders() -> list[str]:
    found: list[str] = []
    for path in sorted(BFF_ROOT.rglob("*.py")):
        rel = path.relative_to(BFF_ROOT)
        if "tests" in rel.parts or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef) or not _is_route(node):
                continue
            if _has_await(node) or (rel.as_posix(), node.name) in ASYNC_NO_AWAIT_ALLOWLIST:
                continue
            found.append(f"{rel.as_posix()}:{node.lineno} async def {node.name}")
    return found


def test_route_async_handlers_always_await() -> None:
    offenders = _offenders()
    assert not offenders, (
        "route-decorated async def without await/async for/async with blocks the event loop; "
        "make it a plain def:\n" + "\n".join(offenders)
    )


def test_allowlist_entries_still_exist_and_need_it() -> None:
    stale = []
    for (rel, name), _reason in ASYNC_NO_AWAIT_ALLOWLIST.items():
        tree = ast.parse((BFF_ROOT / rel).read_text(encoding="utf-8"))
        fns = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name]
        if not fns or any(_has_await(fn) for fn in fns):
            stale.append(f"{rel}:{name}")
    assert not stale, f"stale allowlist entries: {stale}"
