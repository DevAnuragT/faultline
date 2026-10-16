"""Static call-graph extraction for Python packages (no code is imported or executed).

Every edge carries how much we trust it, and that trust level becomes the relationship type in
FalkorDB so Cypher can ask for "exact-only" paths or "any plausible" paths:

  CALLS        the callee was resolved through imports / class hierarchy / local type inference
  MAYBE_CALLS  attribute call on an unresolved receiver; linked to a few same-named methods
  REFERENCES   function used as a value (registries, callbacks), not called here
"""
from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

CALLS, MAYBE_CALLS, REFERENCES = "CALLS", "MAYBE_CALLS", "REFERENCES"
_STRENGTH = {CALLS: 3, MAYBE_CALLS: 2, REFERENCES: 1}
HTTP_VERBS = {"get", "post", "put", "delete", "patch"}
MAX_GUESS = 6
# Receivers named like builtin/container methods would otherwise link to everything.
GUESS_STOPLIST = {
    "append", "extend", "get", "items", "keys", "values", "update", "add", "pop", "read", "write",
    "close", "copy", "split", "join", "strip", "lstrip", "rstrip", "encode", "decode", "format",
    "startswith", "endswith", "lower", "upper", "replace", "sort", "setdefault", "clear", "remove",
    "insert", "index", "count", "find", "flush", "seek", "tell", "readline", "readlines", "match",
    "search", "group", "groups", "sub", "isdigit", "title", "discard", "union", "difference",
    "wait", "set", "acquire", "release", "put", "next", "run", "start", "stop", "open",
}


@dataclass
class Func:
    qual: str
    module: str
    cls: str | None
    name: str
    file: str
    line: int
    end_line: int
    node: ast.AST = field(repr=False, default=None)
    parent: str | None = None
    nested: dict[str, str] = field(default_factory=dict)
    is_module_body: bool = False


@dataclass
class Cls:
    qual: str
    module: str
    name: str
    node: ast.ClassDef = field(repr=False, default=None)
    bases: list[str] = field(default_factory=list)
    methods: dict[str, str] = field(default_factory=dict)
    attr_types: dict[str, str] = field(default_factory=dict)


@dataclass
class Mod:
    name: str
    file: str
    package: str
    tree: ast.Module = field(repr=False, default=None)
    imports: dict[str, tuple] = field(default_factory=dict)
    stars: list[str] = field(default_factory=list)
    funcs: dict[str, str] = field(default_factory=dict)
    classes: dict[str, str] = field(default_factory=dict)
    var_exprs: dict[str, tuple] = field(default_factory=dict)  # module-level `X = expr` / `X: T = expr`
    dist: str = ""
    lines: list[str] = field(default_factory=list, repr=False)


@dataclass
class Endpoint:
    route: str
    method: str
    func: str
    public: bool = True


@dataclass
class Ctx:
    mname: str
    cls: str | None
    func: Func
    env: dict[str, tuple] = field(default_factory=dict)


class CallGraph:
    def __init__(self) -> None:
        self.mods: dict[str, Mod] = {}
        self.funcs: dict[str, Func] = {}
        self.classes: dict[str, Cls] = {}
        self.methods_by_name: dict[str, list[str]] = defaultdict(list)
        self.edges: dict[tuple[str, str], str] = {}
        self.endpoints: list[Endpoint] = []
        self.registered: dict[str, set[str]] = defaultdict(set)  # dist -> functions registered at import time
        self.dispatch_sites: list[str] = []
        self._locals: dict[str, set[str]] = {}
        self._resolving: set[tuple[str, str]] = set()
        self._visible: dict[str, set[str]] = {}

    # ------------------------------------------------------------------ indexing
    def add_root(self, root: Path, dist: str) -> None:
        base = root.parent
        for f in sorted(root.rglob("*.py")):
            rel = f.relative_to(base)
            if any(p in {"tests", "test", "testing"} for p in rel.parts[:-1]):
                continue
            parts = list(rel.with_suffix("").parts)
            is_init = parts[-1] == "__init__"
            if is_init:
                parts = parts[:-1]
            name = ".".join(parts)
            text = f.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue
            pkg = name if is_init else ".".join(parts[:-1])
            mod = Mod(name=name, file=str(rel), package=pkg, tree=tree, dist=dist, lines=text.splitlines())
            self.mods[name] = mod
            self._collect_imports(mod)
            self._collect_defs(tree.body, mod, prefix=name, cls=None, parent=None)
            body = Func(f"{name}.<module>", name, None, "<module>", mod.file, 1,
                        getattr(tree, "end_lineno", 1) or 1, tree, None, {}, True)
            self.funcs[body.qual] = body

    def _collect_imports(self, mod: Mod) -> None:
        for node in ast.walk(mod.tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        mod.imports.setdefault(a.asname, ("mod", a.name))
                    else:
                        top = a.name.split(".")[0]
                        mod.imports.setdefault(top, ("mod", top))
            elif isinstance(node, ast.ImportFrom):
                base = self._abs_module(mod, node.level, node.module)
                if base is None:
                    continue
                for a in node.names:
                    if a.name == "*":
                        mod.stars.append(base)
                    else:
                        mod.imports.setdefault(a.asname or a.name, ("sym", base, a.name))

    @staticmethod
    def _abs_module(mod: Mod, level: int, module: str | None) -> str | None:
        if level == 0:
            return module
        parts = mod.package.split(".") if mod.package else []
        if level - 1 > len(parts):
            return None
        parts = parts[: len(parts) - (level - 1)]
        if module:
            parts += module.split(".")
        return ".".join(parts)

    def _collect_defs(self, body, mod: Mod, prefix: str, cls: Cls | None, parent: Func | None) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}.{stmt.name}"
                fn = Func(qual, mod.name, cls.qual if cls else (parent.cls if parent else None),
                          stmt.name, mod.file, stmt.lineno, stmt.end_lineno or stmt.lineno, stmt,
                          parent.qual if parent else None)
                self.funcs[qual] = fn
                if cls is not None and parent is None:
                    cls.methods[stmt.name] = qual
                    self.methods_by_name[stmt.name].append(qual)
                elif parent is not None:
                    parent.nested[stmt.name] = qual
                else:
                    mod.funcs[stmt.name] = qual
                self._collect_defs(stmt.body, mod, qual, None, fn)
            elif isinstance(stmt, ast.ClassDef):
                qual = f"{prefix}.{stmt.name}"
                c = Cls(qual, mod.name, stmt.name, stmt)
                self.classes[qual] = c
                if parent is None and cls is None:
                    mod.classes[stmt.name] = qual
                self._collect_defs(stmt.body, mod, qual, c, None)
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign)) and cls is None and parent is None:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    mod.var_exprs.setdefault(stmt.targets[0].id, (stmt.value, None))
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    mod.var_exprs.setdefault(stmt.target.id, (stmt.value, stmt.annotation))
            elif isinstance(stmt, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for attr in ("body", "orelse", "finalbody"):
                    self._collect_defs(getattr(stmt, attr, []), mod, prefix, cls, parent)
                for h in getattr(stmt, "handlers", []):
                    self._collect_defs(h.body, mod, prefix, cls, parent)

    # ------------------------------------------------------------------ resolution
    def _resolve_module_var(self, m: Mod, name: str, depth: int):
        """Type a module-level binding: an annotated global (`request: Request = LocalProxy(...)`) is an instance of the
        annotated class; `decode = _jwt.decode` aliases the resolved function; `obj = Cls()` is an instance."""
        key = (m.name, name)
        if key in self._resolving or depth > 6:
            return None
        self._resolving.add(key)
        try:
            value, ann = m.var_exprs[name]
            ctx = Ctx(m.name, None, self.funcs[f"{m.name}.<module>"])
            if ann is not None:
                t = self._annotation(ann, ctx)
                if t:
                    return t
            if value is None:
                return None
            r = self.resolve_expr(value, ctx)
            return r if r and r[0] in ("fn", "inst", "cls", "mod") else None
        finally:
            self._resolving.discard(key)

    def resolve_name_in_module(self, mname: str, name: str, depth: int = 0):
        m = self.mods.get(mname)
        if not m or depth > 8:
            return None
        if name in m.funcs:
            return ("fn", m.funcs[name])
        if name in m.classes:
            return ("cls", m.classes[name])
        if name in m.imports:
            imp = m.imports[name]
            if imp[0] == "mod":
                return ("mod", imp[1]) if imp[1] in self.mods else None
            _, base, sym = imp
            if f"{base}.{sym}" in self.mods:
                return ("mod", f"{base}.{sym}")
            return self.resolve_name_in_module(base, sym, depth + 1)
        if name in m.var_exprs:
            r = self._resolve_module_var(m, name, depth)
            if r:
                return r
        for star in m.stars:
            r = self.resolve_name_in_module(star, name, depth + 1)
            if r:
                return r
        if f"{mname}.{name}" in self.mods:
            return ("mod", f"{mname}.{name}")
        return None

    def class_bases(self, cq: str) -> list[str]:
        c = self.classes[cq]
        if not c.bases and c.node is not None and c.node.bases:
            for b in c.node.bases:
                t = self.resolve_expr(b, Ctx(c.module, None, self.funcs[f"{c.module}.<module>"]))
                if t and t[0] == "cls":
                    c.bases.append(t[1])
        return c.bases

    def find_method(self, cq: str, name: str, skip_self: bool = False, _seen=None) -> str | None:
        _seen = _seen or set()
        if cq in _seen or cq not in self.classes:
            return None
        _seen.add(cq)
        c = self.classes[cq]
        if not skip_self and name in c.methods:
            return c.methods[name]
        for b in self.class_bases(cq):
            r = self.find_method(b, name, False, _seen)
            if r:
                return r
        return None

    def find_attr_type(self, cq: str, attr: str, _seen=None) -> str | None:
        _seen = _seen or set()
        if cq in _seen or cq not in self.classes:
            return None
        _seen.add(cq)
        c = self.classes[cq]
        if attr in c.attr_types:
            return c.attr_types[attr]
        for b in self.class_bases(cq):
            r = self.find_attr_type(b, attr, _seen)
            if r:
                return r
        return None

    def _annotation(self, ann, ctx: Ctx):
        if ann is None:
            return None
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                ann = ast.parse(ann.value, mode="eval").body
            except SyntaxError:
                return None
        t = self.resolve_expr(ann, ctx)
        return ("inst", t[1]) if t and t[0] == "cls" else None

    # ------------------------------------------------------------------ scope helpers
    def _local_names(self, fn: Func) -> set[str]:
        """Names bound inside fn (params, assignments, loop/with/except targets) and its enclosing functions.
        A call through such a name must never fall back to a same-named module-level function."""
        if fn.qual in self._locals:
            return self._locals[fn.qual]
        names: set[str] = set(self._local_names(self.funcs[fn.parent])) if fn.parent in self.funcs else set()
        node = fn.node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else []):
                names.add(arg.arg)
            for n in self._own_nodes(fn):
                if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor, ast.With,
                                  ast.AsyncWith, ast.ExceptHandler, ast.NamedExpr)):
                    targets = []
                    if isinstance(n, ast.Assign):
                        targets = n.targets
                    elif isinstance(n, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
                        targets = [n.target]
                    elif isinstance(n, (ast.For, ast.AsyncFor)):
                        targets = [n.target]
                    elif isinstance(n, (ast.With, ast.AsyncWith)):
                        targets = [i.optional_vars for i in n.items if i.optional_vars is not None]
                    elif isinstance(n, ast.ExceptHandler) and n.name:
                        names.add(n.name)
                    for t in targets:
                        for sub in ast.walk(t):
                            if isinstance(sub, ast.Name):
                                names.add(sub.id)
        self._locals[fn.qual] = names
        return names

    def _dist_of_module(self, name: str) -> str | None:
        parts = name.split(".")
        while parts:
            m = self.mods.get(".".join(parts))
            if m:
                return m.dist
            parts.pop()
        return None

    def visible_dists(self, mname: str) -> set[str]:
        """Distributions a module can legitimately call into: its own plus everything it imports."""
        if mname in self._visible:
            return self._visible[mname]
        m = self.mods[mname]
        out = {m.dist}
        for imp in m.imports.values():
            target = imp[1] if imp[0] == "mod" else f"{imp[1]}.{imp[2]}"
            d = self._dist_of_module(target)
            if d:
                out.add(d)
        for star in m.stars:
            d = self._dist_of_module(star)
            if d:
                out.add(d)
        self._visible[mname] = out
        return out

    @staticmethod
    def _root_name(e) -> str | None:
        while True:
            if isinstance(e, ast.Attribute):
                e = e.value
            elif isinstance(e, ast.Call):
                e = e.func
            elif isinstance(e, ast.Subscript):
                e = e.value
            else:
                break
        return e.id if isinstance(e, ast.Name) else None

    def resolve_expr(self, e, ctx: Ctx):
        if isinstance(e, ast.Name):
            if e.id in ctx.env:
                return ctx.env[e.id]
            fn: Func | None = ctx.func
            while fn is not None:
                if e.id in fn.nested:
                    return ("fn", fn.nested[e.id])
                fn = self.funcs.get(fn.parent) if fn.parent else None
            if not ctx.func.is_module_body and e.id in self._local_names(ctx.func):
                return None  # bound locally to something we can't type: never fall back to a module-level name
            return self.resolve_name_in_module(ctx.mname, e.id)
        if isinstance(e, ast.Attribute):
            return self._attr_of(self.resolve_expr(e.value, ctx), e.attr)
        if isinstance(e, ast.Call):
            if isinstance(e.func, ast.Name) and e.func.id == "super" and ctx.cls:
                return ("super", ctx.cls)
            t = self.resolve_expr(e.func, ctx)
            if t and t[0] == "cls":
                return ("inst", t[1])
            if t and t[0] == "fn":
                ret = getattr(self.funcs[t[1]].node, "returns", None)
                if ret is not None:
                    return self._annotation(ret, Ctx(self.funcs[t[1]].module, self.funcs[t[1]].cls, self.funcs[t[1]]))
            return None
        return None

    def _attr_of(self, base, attr: str):
        if not base:
            return None
        kind = base[0]
        if kind == "mod":
            return self.resolve_name_in_module(base[1], attr)
        if kind in ("cls", "inst"):
            m = self.find_method(base[1], attr)
            if m:
                return ("fn", m)
            t = self.find_attr_type(base[1], attr)
            if t and kind == "inst":
                return ("inst", t)
            nested = f"{base[1]}.{attr}"
            return ("cls", nested) if nested in self.classes else None
        if kind == "super":
            m = self.find_method(base[1], attr, skip_self=True)
            return ("fn", m) if m else None
        return None

    # ------------------------------------------------------------------ analysis
    def analyze(self) -> None:
        for record in (False, True):
            for fn in list(self.funcs.values()):
                self._analyze_func(fn, record)
        self._dispatch_pass()
        self._find_endpoints()

    def _dispatch_pass(self) -> None:
        """Runtime registry dispatch (`handler = self.registry[key]; handler(...)`) can't be resolved
        statically; link each dispatch site to functions registered at import time in the same dist."""
        for site in self.dispatch_sites:
            dist = self.mods[self.funcs[site].module].dist
            for target in self.registered.get(dist, ()):
                if target != site:
                    self._edge(site, target, MAYBE_CALLS)

    def _own_nodes(self, fn: Func):
        """Yield AST nodes belonging to fn, not descending into nested defs (they have their own Func)."""
        roots = fn.node.body if isinstance(fn.node, ast.Module) else fn.node.body
        stack = list(roots)
        while stack:
            n = stack.pop()
            yield n
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.extend(n.decorator_list)
                stack.extend(n.args.defaults)
                continue
            stack.extend(ast.iter_child_nodes(n))

    def _analyze_func(self, fn: Func, record: bool) -> None:
        ctx = Ctx(fn.module, fn.cls, fn)
        node = fn.node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            is_static = any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in node.decorator_list)
            for i, a in enumerate(args):
                if i == 0 and fn.cls and not is_static and fn.parent is None:
                    ctx.env[a.arg] = ("inst", fn.cls)
                else:
                    t = self._annotation(a.annotation, ctx)
                    if t:
                        ctx.env[a.arg] = t
        nodes = list(self._own_nodes(fn))
        nodes.sort(key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))
        for n in nodes:
            if isinstance(n, ast.Assign):
                val = self.resolve_expr(n.value, ctx)
                if val and val[0] == "inst":
                    for t in n.targets:
                        self._bind(t, val, ctx, fn)
            elif isinstance(n, ast.AnnAssign):
                val = self._annotation(n.annotation, ctx) or (self.resolve_expr(n.value, ctx) if n.value else None)
                if val and val[0] == "inst":
                    self._bind(n.target, val, ctx, fn)
            elif isinstance(n, (ast.With, ast.AsyncWith)):
                for item in n.items:
                    val = self.resolve_expr(item.context_expr, ctx)
                    if val and val[0] == "inst" and item.optional_vars is not None:
                        self._bind(item.optional_vars, val, ctx, fn)
        if not record:
            return
        call_funcs = {id(n.func) for n in nodes if isinstance(n, ast.Call)}
        subscript_vars = {t.id for n in nodes if isinstance(n, ast.Assign) and isinstance(n.value, ast.Subscript)
                          for t in n.targets if isinstance(t, ast.Name)}
        dist = self.mods[fn.module].dist
        for n in nodes:
            if isinstance(n, ast.Call):
                self._handle_call(n, ctx, fn)
                if fn.is_module_body:
                    for a in n.args:
                        t = self.resolve_expr(a, ctx) if isinstance(a, (ast.Name, ast.Attribute)) else None
                        if t and t[0] == "fn":
                            self.registered[dist].add(t[1])
                elif isinstance(n.func, ast.Name) and n.func.id in subscript_vars \
                        and self.resolve_expr(n.func, ctx) is None and fn.qual not in self.dispatch_sites:
                    self.dispatch_sites.append(fn.qual)
            elif isinstance(n, (ast.Name, ast.Attribute)) and isinstance(getattr(n, "ctx", None), ast.Load) \
                    and id(n) not in call_funcs:
                t = self.resolve_expr(n, ctx)
                if t and t[0] == "fn" and t[1] != fn.qual:
                    self._edge(fn.qual, t[1], REFERENCES)

    def _bind(self, target, val, ctx: Ctx, fn: Func) -> None:
        if isinstance(target, ast.Name):
            ctx.env[target.id] = val
        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                and target.value.id in ctx.env and ctx.env[target.value.id][0] == "inst":
            owner = ctx.env[target.value.id][1]
            if owner in self.classes:
                self.classes[owner].attr_types[target.attr] = val[1]

    def _handle_call(self, c: ast.Call, ctx: Ctx, fn: Func) -> None:
        target = self.resolve_expr(c.func, ctx)
        if target:
            if target[0] == "fn":
                self._edge(fn.qual, target[1], CALLS)
            elif target[0] == "cls":
                init = self.find_method(target[1], "__init__")
                if init:
                    self._edge(fn.qual, init, CALLS)
            elif target[0] == "inst":
                call = self.find_method(target[1], "__call__")
                if call:
                    self._edge(fn.qual, call, CALLS)
            return
        if isinstance(c.func, ast.Attribute):
            if self.resolve_expr(c.func.value, ctx) is not None:
                return
            root = self._root_name(c.func.value)
            if root and root in self.mods[ctx.mname].imports and root not in self._local_names(fn):
                return  # receiver is an import of an unanalyzed package (e.g. flask.request): not a guess target
            name = c.func.attr
            visible = self.visible_dists(ctx.mname)
            cands = [q for q in self.methods_by_name.get(name, [])
                     if self.mods[self.funcs[q].module].dist in visible]
            if name in GUESS_STOPLIST or name.startswith("__") or not (1 <= len(cands) <= MAX_GUESS):
                return
            for q in cands:
                self._edge(fn.qual, q, MAYBE_CALLS)

    def _edge(self, src: str, dst: str, kind: str) -> None:
        key = (src, dst)
        if key not in self.edges or _STRENGTH[kind] > _STRENGTH[self.edges[key]]:
            self.edges[key] = kind

    # ------------------------------------------------------------------ framework entrypoints
    def _find_endpoints(self) -> None:
        self.endpoints = []
        for fn in self.funcs.values():
            if not isinstance(fn.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in fn.node.decorator_list:
                if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)):
                    continue
                attr = d.func.attr
                if attr != "route" and attr not in HTTP_VERBS:
                    continue
                if not (d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str)):
                    continue
                methods = [attr.upper()] if attr in HTTP_VERBS else ["GET"]
                for kw in d.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = [e.value.upper() for e in kw.value.elts if isinstance(e, ast.Constant)]
                for m in methods:
                    self.endpoints.append(Endpoint(d.args[0].value, m, fn.qual))

    def func_source(self, qual: str) -> str:
        fn = self.funcs[qual]
        return "\n".join(self.mods[fn.module].lines[fn.line - 1: fn.end_line])

    # ------------------------------------------------------------------ patch mapping helper
    def functions_at(self, patch_path: str, lines: list[int]) -> list[str]:
        """Innermost real function (not module body) enclosing each line of a file."""
        out: list[str] = []
        cands = [f for f in self.funcs.values()
                 if not f.is_module_body and patch_path.endswith(f.file)]
        for ln in lines:
            best = None
            for f in cands:
                if f.line <= ln <= f.end_line and (best is None or (f.end_line - f.line) < (best.end_line - best.line)):
                    best = f
            if best and best.qual not in out:
                out.append(best.qual)
        return out


def build(roots: list[tuple[Path, str]]) -> CallGraph:
    cg = CallGraph()
    for root, dist in roots:
        cg.add_root(root, dist)
    cg.analyze()
    return cg
