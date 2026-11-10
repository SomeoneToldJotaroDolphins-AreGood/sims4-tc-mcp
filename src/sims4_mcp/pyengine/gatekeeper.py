"""Python 3.7.0 AST gatekeeper for The Sims 4's embedded CPython.

The game ships CPython **3.7.0** (64-bit).  Anything parsed by a newer
``ast`` module that uses post-3.7 syntax must be rejected or rewritten.
Blocked constructs:

- walrus operator ``:=`` (3.8)
- positional-only parameters ``def f(a, /)`` (3.8)
- ``match``/``case`` soft keywords (3.10)
- ``f"...{x=}"`` self-documenting f-strings (3.8)
- ``dict | dict`` / ``dict |= dict`` merge operator (3.9)
- PEP 585 builtin generic subscripts evaluated at runtime
  (``list[int]``, ``dict[str, int]`` outside ``from __future__ import
  annotations``) (3.9)
- ``X | Y`` type unions in evaluated annotations (3.10)
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class CompatIssue:
    line: int
    col: int
    construct: str
    message: str
    min_python: str


@dataclass
class GateResult:
    compatible: bool
    issues: list[CompatIssue] = field(default_factory=list)
    rewritten_source: str | None = None

    def as_dict(self) -> dict:
        return {
            "compatible": self.compatible,
            "issues": [{"line": i.line, "col": i.col,
                        "construct": i.construct, "message": i.message,
                        "requires_python": i.min_python}
                       for i in self.issues],
        }


class _GateVisitor(ast.NodeVisitor):
    def __init__(self, has_future_annotations: bool):
        self.issues: list[CompatIssue] = []
        self._future_annotations = has_future_annotations

    def _add(self, node, construct, message, min_python="3.8"):
        self.issues.append(CompatIssue(
            line=getattr(node, "lineno", 0),
            col=getattr(node, "col_offset", 0),
            construct=construct, message=message, min_python=min_python))

    # 3.8 walrus
    def visit_NamedExpr(self, node):
        self._add(node, "walrus :=",
                  "Assignment expressions require Python 3.8")

    # 3.8 positional-only params
    def visit_arguments(self, node):
        if getattr(node, "posonlyargs", None):
            pos = node.posonlyargs[0]
            self._add(pos, "positional-only /",
                      "Positional-only parameters require Python 3.8")
        self.generic_visit(node)

    # 3.10 match
    def visit_Match(self, node):
        self._add(node, "match/case",
                  "Structural pattern matching requires Python 3.10",
                  min_python="3.10")

    # 3.9 dict merge
    def visit_BinOp(self, node):
        if isinstance(node.op, ast.BitOr):
            if _is_dict_like(node.left) or _is_dict_like(node.right):
                self._add(node, "dict | dict",
                          "Dict merge operator requires Python 3.9",
                          min_python="3.9")
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        if isinstance(node.op, ast.BitOr) and _is_dict_like(node.target):
            self._add(node, "dict |= ...",
                      "Dict union operator requires Python 3.9",
                      min_python="3.9")
        self.generic_visit(node)

    # annotations: PEP585 subscripts & X | Y unions
    def _check_annotation(self, ann):
        if ann is None:
            return
        for node in ast.walk(ann):
            # visit_Subscript() flags generics unless tagged + future import
            if isinstance(node, ast.Subscript):
                node._annotation_context = True  # type: ignore[attr-defined]
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                if not self._future_annotations and _is_type_union(node):
                    self._add(node, "X | Y annotation",
                              "PEP 604 union annotations require Python "
                              "3.10 (or 'from __future__ import "
                              "annotations')", min_python="3.10")

    def visit_Subscript(self, node):
        # PEP 585 builtin generics evaluated at runtime anywhere
        if (isinstance(node.value, ast.Name)
                and node.value.id in _BUILTIN_GENERICS):
            context = getattr(node, "_annotation_context", False)
            if not (context and self._future_annotations):
                where = " annotation" if context else ""
                self._add(node, f"{node.value.id}[...]{where}".strip(),
                          "Builtin generic subscripts are evaluated at "
                          "runtime and require Python 3.9 (or "
                          "'from __future__ import annotations')",
                          min_python="3.9")
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        for a in node.args.args + node.args.kwonlyargs:
            self._check_annotation(a.annotation)
        self._check_annotation(node.returns)
        self._check_fstring_defaults(node)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_AnnAssign(self, node):
        self._check_annotation(node.annotation)
        self.generic_visit(node)

    def _check_fstring_defaults(self, node):
        for d in node.args.defaults + node.args.kw_defaults:
            if isinstance(d, ast.JoinedStr):
                for part in ast.walk(d):
                    if isinstance(part, ast.FormattedValue) and part.format_spec is None \
                       and getattr(part, "conversion", -1) == -1:
                        # '=' self-documenting specifier lives in the source;
                        # detect via f-string raw text below instead
                        pass

    def visit_JoinedStr(self, node):
        # f"{x=}" self-documenting specifier (3.8): detect by looking for
        # a FormattedValue whose expression source ends with '=' in the
        # original line — approximated by checking the AST dump for the
        # literal marker lxml doesn't keep.  We instead scan raw source in
        # check_source().
        self.generic_visit(node)


_BUILTIN_GENERICS = {
    "list", "dict", "set", "frozenset", "tuple", "type",
    "deque", "defaultdict", "Counter", "OrderedDict",
}


def _is_dict_like(node: ast.expr | ast.stmt) -> bool:
    if isinstance(node, ast.Dict):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in ("dict", "OrderedDict", "defaultdict")
    return False


def _is_type_union(node: ast.BinOp) -> bool:
    def side(n) -> bool:
        return (isinstance(n, ast.Name)
                or (isinstance(n, ast.Constant) and n.value is None)
                or (isinstance(n, ast.Subscript)
                    and isinstance(n.value, ast.Name))
                or (isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr)))
    return side(node.left) and side(node.right)


def _find_fstring_equals(line: str) -> int | None:
    """Return the column of an ``f"{x=}"`` self-documenting field, if any."""
    i = 0
    n = len(line)
    while i < n:
        if line[i] in "fF" and i + 1 < n and line[i + 1] in "\"'":
            quote = line[i + 1]
            triple = line[i + 2:i + 4] == quote * 2
            start = i + 4 if triple else i + 2
            j = start
            depth = 0
            while j < n:
                ch = line[j]
                if depth == 0 and ch == quote:
                    # crude end-of-string (may be escaped; good enough)
                    if not triple or line[j:j + 3] == quote * 3:
                        break
                if ch == "{":
                    if j + 1 < n and line[j + 1] == "{":
                        j += 2
                        continue
                    depth += 1
                elif ch == "}":
                    depth = max(0, depth - 1)
                elif ch == "=" and depth == 1 and j + 1 < n and line[j + 1] in "}>!:":
                    return i
                j += 1
            i = j + 1
        else:
            i += 1
    return None


def check_source(source: str, filename: str = "<string>") -> GateResult:
    """Parse ``source`` and report 3.7.0 incompatibilities."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return GateResult(compatible=False, issues=[CompatIssue(
            line=e.lineno or 0, col=e.offset or 0,
            construct="SyntaxError", message=str(e.msg),
            min_python="unknown")])

    has_future = any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names)
        for n in ast.walk(tree))

    v = _GateVisitor(has_future_annotations=has_future)
    v.visit(tree)

    # raw-source scan for f-string '=' specifier
    for i, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        col = _find_fstring_equals(line)
        if col is not None:
            v.issues.append(CompatIssue(
                line=i, col=col, construct="f'{x=}'",
                message="Self-documenting f-strings require Python 3.8",
                min_python="3.8"))

    return GateResult(compatible=not v.issues, issues=v.issues)


def check_file(path: str) -> GateResult:
    with open(path, "r", encoding="utf-8") as fh:
        return check_source(fh.read(), filename=path)
