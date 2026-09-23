#!/usr/bin/env python3
"""Integrate the api/ blueprints into app.py.

  python3 tools/integrate_blueprints.py --dry-run          # show what would change
  python3 tools/integrate_blueprints.py --apply            # do it (writes app.py, api/*.py)

1. For every api/<module>.py: collect its `@bp.route(path, methods=[...])` rules.
2. In app.py, find `@app.route(path, methods=[...])`-decorated functions whose (path, methods)
   is covered by a blueprint rule and delete them (decorators + body).
3. Flip `URL_PREFIX = "/_new"` -> `URL_PREFIX = None` in each module.
4. Replace the body of `updateClintInRedis` with a no-op (the Redis client cache has no readers left).
5. Report app.py functions that are no longer referenced anywhere (candidates for manual removal).
"""
import argparse, ast, os, re, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CAWP-API")
APP = os.path.join(ROOT, "app.py")
API_DIR = os.path.join(ROOT, "api")


def route_rules(tree, decorator_obj):
    """-> list of (func_node, path, frozenset(methods))"""
    out = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                    and isinstance(d.func.value, ast.Name) and d.func.value.id == decorator_obj
                    and d.func.attr == "route"):
                continue
            if not d.args or not isinstance(d.args[0], ast.Constant):
                continue
            path = d.args[0].value
            methods = frozenset({"GET"})
            for kw in d.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods = frozenset(e.value.upper() for e in kw.value.elts if isinstance(e, ast.Constant))
            out.append((node, path.rstrip("/") or "/", methods, path))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--modules", default="clients_reports,reports,dashboard,imports")
    a = ap.parse_args()

    bp_rules = {}  # (path, methods) -> module
    for m in a.modules.split(","):
        f = os.path.join(API_DIR, m + ".py")
        if not os.path.exists(f):
            print(f"-- {m}: missing, skipped"); continue
        tree = ast.parse(open(f).read())
        rules = route_rules(tree, "bp")
        for _, p, meths, _ in rules:
            bp_rules[(p, meths)] = m
        print(f"-- {m}: {len(rules)} rules")

    src = open(APP).read()
    tree = ast.parse(src)
    lines = src.split("\n")
    app_rules = route_rules(tree, "app")

    to_delete = []  # (start_line, end_line, name, path, methods)
    covered_by_path = {}
    for node, p, meths, raw in app_rules:
        key = (p, meths)
        if key in bp_rules:
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])
            to_delete.append((start, node.end_lineno, node.name, raw, sorted(meths), bp_rules[key]))
        else:
            # partial coverage: same path, methods differ -> report only
            for (bp_p, bp_m), m in bp_rules.items():
                if bp_p == p and bp_m != meths:
                    covered_by_path.setdefault(p, []).append((node.name, sorted(meths), sorted(bp_m), m))

    print(f"\n== {len(to_delete)} legacy route functions superseded ==")
    for s, e, name, raw, meths, m in sorted(to_delete):
        print(f"  L{s:>5}-{e:<5} {name:<40} {','.join(meths):<10} {raw}  <- api/{m}")
    if covered_by_path:
        print("\n== same path, different methods (NOT deleted, check manually) ==")
        for p, items in covered_by_path.items():
            for it in items: print("  ", p, it)
    bp_only = [(p, m) for (p, meths), m in bp_rules.items() if not any(pp == p and mm == meths for _, pp, mm, _ in app_rules)]
    if bp_only:
        print("\n== blueprint rules with NO legacy counterpart (new endpoints) ==")
        for p, m in sorted(bp_only): print("  ", p, "<-", m)

    if not a.apply:
        print("\n(dry run — nothing written)"); return

    # delete from the bottom up so line numbers stay valid
    for s, e, name, raw, meths, m in sorted(to_delete, reverse=True):
        # also swallow blank lines / comment lines directly above the decorator block
        while s > 1 and lines[s - 2].strip().startswith("#"):
            s -= 1
        del lines[s - 1:e]
        lines.insert(s - 1, f"# [integrated] {name} {','.join(meths)} {raw} -> api/{m}")
    src = "\n".join(lines)

    # no-op updateClintInRedis
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "updateClintInRedis":
            L = src.split("\n")
            body_start = node.body[0].lineno
            indent = " " * 4
            new_body = [indent + '"""The per-client Redis cache (clint:<id>:*) has no readers since the list/export',
                        indent + 'routes moved to SQL (api/clients_reports.py); kept as a no-op for the remaining callers."""',
                        indent + "return None"]
            L[body_start - 1:node.end_lineno] = new_body
            src = "\n".join(L)
            break

    open(APP, "w").write(src)
    for m in a.modules.split(","):
        f = os.path.join(API_DIR, m + ".py")
        if os.path.exists(f):
            t = open(f).read()
            t2 = re.sub(r'^URL_PREFIX\s*=\s*"/_new"\s*$', "URL_PREFIX = None", t, flags=re.M)
            if t2 != t:
                open(f, "w").write(t2); print(f"-- {m}: URL_PREFIX -> None")

    # unreferenced top-level functions in app.py (candidates)
    tree = ast.parse(src)
    names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)
             and not any(isinstance(d, ast.Call) and getattr(getattr(d.func, "value", None), "id", "") in ("app", "socketIO", "socketio", "jwt")
                         or isinstance(d, ast.Attribute) for d in n.decorator_list)]
    all_src = src
    for m in os.listdir(API_DIR):
        if m.endswith(".py"): all_src += open(os.path.join(API_DIR, m)).read()
    sv = os.path.join(ROOT, "services")
    for m in os.listdir(sv):
        if m.endswith(".py"): all_src += open(os.path.join(sv, m)).read()
    unref = [n for n in names if len(re.findall(r"\b%s\b" % re.escape(n), all_src)) <= 1]
    print(f"\n== {len(unref)} app.py functions now unreferenced (review before deleting) ==")
    print("  " + ", ".join(unref))
    print(f"\napp.py now {len(src.splitlines())} lines; routes: {src.count('@app.route(')}")


if __name__ == "__main__":
    main()
