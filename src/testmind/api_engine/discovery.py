"""Static discovery of HTTP endpoints (FastAPI, Flask, Django) from the AST.

Nothing is imported or executed. Discovery follows the constructs the frameworks document:
- FastAPI: `FastAPI()` / `APIRouter(prefix=…)`, the method decorators (`@router.get(…)`,
  `@app.api_route(…, methods=[…])`), `add_api_route`, `include_router(…, prefix=…)` and
  `mount(path, sub_application)`;
- Flask: `Flask()` / `Blueprint(…, url_prefix=…)`, `@app.route(…, methods=[…])`, the method
  decorators, `add_url_rule` and `register_blueprint(…, url_prefix=…)`, also inside an
  application factory;
- Django: `ROOT_URLCONF`, `urlpatterns` with `path` / `re_path` / `url`, `include(…)`, function
  views (with `require_GET`, `require_http_methods`, DRF `api_view`) and `View.as_view()`.

References are resolved across the repository's modules through their imports. Anything that
is not a literal (a computed prefix, a route built at run time) is reported in `notes` and
not guessed. The running application's own route table, recorded in the sandbox, is the
cross-check (docs/api-testing.md).
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from testmind.analyzers.repository.entry_points import module_name
from testmind.api_engine.models import (
    ANY_METHOD,
    ApiApp,
    ApiSurface,
    Endpoint,
    Framework,
    Handler,
    PathParam,
)
from testmind.api_engine.paths import canonical, join

FASTAPI_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head", "trace"})
FLASK_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
HTTP_HANDLER_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
CONTAINERS = {
    "FastAPI": (Framework.FASTAPI, True, "prefix"),
    "APIRouter": (Framework.FASTAPI, False, "prefix"),
    "Flask": (Framework.FLASK, True, None),
    "Blueprint": (Framework.FLASK, False, "url_prefix"),
}
BODY_DEFAULTS = frozenset({"Body", "Form", "File"})
FLASK_BODY = frozenset({"json", "get_json", "form", "data", "files", "values", "get_data"})
DJANGO_BODY = frozenset({"POST", "body", "FILES", "data"})
DJANGO_DECORATORS = {"require_GET": ["GET"], "require_POST": ["POST"], "require_safe": ["GET"]}
ANNOTATION_TYPES = {"int": "int", "float": "float", "str": "str", "UUID": "uuid", "bool": "bool"}
# Positions: add_api_route(path, endpoint), add_url_rule(rule, endpoint, view_func),
# path(route, view), mount(path, app).
ENDPOINT_ARGUMENT, VIEW_FUNC_ARGUMENT, VIEW_ARGUMENT, MOUNTED_ARGUMENT = 1, 2, 1, 1
REGISTRATIONS = frozenset(
    {"include_router", "register_blueprint", "add_api_route", "add_url_rule", "mount"}
)
DYNAMIC = object()  # a prefix or path that is not a string literal


@dataclass(slots=True)
class _Module:
    path: str
    name: str
    tree: ast.Module
    is_package: bool
    imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    functions: dict[str, tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = field(
        default_factory=dict
    )  # name → (qualname, node), module level and inside top-level functions
    classes: dict[str, ast.ClassDef] = field(default_factory=dict)


@dataclass(slots=True)
class _Route:
    methods: list[str]
    path: str
    handler: Handler | None
    params: dict[str, str]  # handler parameter annotations (FastAPI path parameter types)
    accepts_body: bool


@dataclass(slots=True)
class _Container:
    key: tuple[str, str]
    framework: Framework
    is_app: bool
    prefix: str | object
    path: str
    line: int
    factory: str | None
    factory_needs_arguments: bool
    routes: list[_Route] = field(default_factory=list)
    mounts: list[tuple[tuple[str, str], str | object | None, str]] = field(default_factory=list)
    mounted: bool = False


def discover(sources: Iterable[tuple[str, ast.Module]]) -> ApiSurface:
    """Endpoints of the parsed production modules (`(path, tree)` pairs)."""
    modules: dict[str, _Module] = {}
    for path, tree in sources:
        module = _Module(
            path=path,
            name=module_name(path),
            tree=tree,
            is_package=PurePosixPath(path).name == "__init__.py",
        )
        _index(module)
        modules[module.name] = module
    return _Discovery(modules).run()


class _Discovery:
    def __init__(self, modules: dict[str, _Module]) -> None:
        self.modules = modules
        self.containers: dict[tuple[str, str], _Container] = {}
        self.notes: list[str] = []

    def run(self) -> ApiSurface:
        for module in self.modules.values():
            self._find_containers(module)
        for module in self.modules.values():
            self._find_routes(module)
        apps: list[ApiApp] = []
        endpoints: list[Endpoint] = []
        for container in sorted(self.containers.values(), key=lambda c: c.key):
            if container.is_app and not container.mounted:  # a mounted app is part of another
                app = self._app(container)
                apps.append(app)
                endpoints += self._expand(container, "", app.target, set())
        for container in sorted(self.containers.values(), key=lambda c: c.key):
            if not container.is_app and not container.mounted and container.routes:
                kind = "router" if container.framework is Framework.FASTAPI else "blueprint"
                self.notes.append(
                    f"{kind} {container.key[0]}:{container.key[1]} ({len(container.routes)} "
                    "route(s)) is not included in any application found statically"
                )
        django_apps, django_endpoints = self._django()
        apps += django_apps
        endpoints += django_endpoints
        unique = {(e.app, e.method, e.path): e for e in endpoints}
        return ApiSurface(
            apps=apps,
            endpoints=sorted(unique.values(), key=lambda e: (e.path, e.method, e.app or "")),
            notes=self.notes,
        )

    # ------------------------------------------------------------------ FastAPI and Flask
    def _find_containers(self, module: _Module) -> None:
        for node, factory in _walk_with_factory(module.tree):
            if not (
                isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(node.value, ast.Call)
            ):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            kind = _call_name(node.value.func).rsplit(".", 1)[-1]
            if kind not in CONTAINERS or len(targets) != 1 or not isinstance(targets[0], ast.Name):
                continue
            framework, is_app, prefix_keyword = CONTAINERS[kind]
            prefix: str | object = ""
            if prefix_keyword is not None:
                value = _keyword(node.value, prefix_keyword)
                prefix = "" if value is None else _string(value)
            key = (module.name, targets[0].id)
            needs_arguments = factory is not None and _needs_arguments(factory)
            self.containers[key] = _Container(
                key=key,
                framework=framework,
                is_app=is_app,
                prefix=prefix,
                path=module.path,
                line=node.lineno,
                factory=factory.name if factory is not None else None,
                factory_needs_arguments=needs_arguments,
            )

    def _find_routes(self, module: _Module) -> None:
        for node, qualname in _functions(module.tree):
            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                ):
                    continue
                container = self._container(module, decorator.func.value)
                if container is None:
                    continue
                methods = _decorator_methods(container.framework, decorator.func.attr, decorator)
                if methods is None:
                    continue
                path = (
                    _string(decorator.args[0])
                    if decorator.args
                    else _keyword_string(decorator, "path", "rule")
                )
                self._add_route(
                    container, module, methods, path, node=node, qualname=qualname,
                    location=f"{module.path}:{decorator.lineno}",
                )  # fmt: skip
        for call in ast.walk(module.tree):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                self._registration_call(module, call, call.func)

    def _registration_call(self, module: _Module, call: ast.Call, attribute: ast.Attribute) -> None:
        name = attribute.attr
        container = self._container(module, attribute.value) if name in REGISTRATIONS else None
        if container is None:
            return
        location = f"{module.path}:{call.lineno}"
        if name == "mount":
            self._mount(module, container, call, location)
        elif name in {"include_router", "register_blueprint"}:
            self._include(module, container, call, location)
        else:
            self._route_call(module, container, call, location)

    def _mount(self, module: _Module, container: _Container, call: ast.Call, location: str) -> None:
        """FastAPI / Starlette: `app.mount("/sub", sub_application)`."""
        target = (
            call.args[MOUNTED_ARGUMENT]
            if len(call.args) > MOUNTED_ARGUMENT
            else _keyword(call, "app")
        )
        child = self._container(module, target) if target is not None else None
        if child is not None:  # anything else (StaticFiles, …) serves no API endpoints
            child.mounted = True
            path = _string(call.args[0]) if call.args else _keyword_string(call, "path")
            container.mounts.append((child.key, path, location))

    def _include(
        self, module: _Module, container: _Container, call: ast.Call, location: str
    ) -> None:
        """`include_router(router, prefix=…)` / `register_blueprint(bp, url_prefix=…)`."""
        name = (
            "include_router" if container.framework is Framework.FASTAPI else "register_blueprint"
        )
        child = self._container(module, call.args[0]) if call.args else None
        if child is None:
            self.notes.append(f"{name} at {location}: the included object is not resolved")
            return
        value = _keyword(call, "prefix" if name == "include_router" else "url_prefix")
        prefix: str | object | None = None if value is None else _string(value)
        child.mounted = True
        container.mounts.append((child.key, prefix, location))

    def _route_call(
        self, module: _Module, container: _Container, call: ast.Call, location: str
    ) -> None:
        """`add_api_route(path, endpoint)` / `add_url_rule(rule, endpoint, view_func)`."""
        fastapi = container.framework is Framework.FASTAPI
        path = _string(call.args[0]) if call.args else _keyword_string(call, "path", "rule")
        view = _keyword(call, "endpoint" if fastapi else "view_func")
        position = ENDPOINT_ARGUMENT if fastapi else VIEW_FUNC_ARGUMENT
        if view is None and len(call.args) > position:
            view = call.args[position]
        methods = _methods_keyword(call) or ["GET"]
        target = self._view(module, view) if view is not None else None
        if isinstance(target, _ClassView):
            methods = _methods_keyword(call) or target.methods or ["GET"]
            self._add_route(
                container, module, methods, path, location=location, handler=target.handler,
                accepts_body=target.accepts_body,
            )  # fmt: skip
        elif target is None:
            self._add_route(container, module, methods, path, location=location)
        else:
            node, qualname, owner = target
            self._add_route(
                container, owner, methods, path, node=node, qualname=qualname, location=location
            )

    def _add_route(
        self,
        container: _Container,
        module: _Module,
        methods: list[str],
        path: str | object,
        *,
        location: str,
        node: ast.FunctionDef | ast.AsyncFunctionDef | None = None,
        qualname: str | None = None,
        handler: Handler | None = None,
        accepts_body: bool = False,
    ) -> None:
        """`module` is where the handler is defined; `location` is the registration."""
        if not isinstance(path, str):
            self.notes.append(f"route at {location} has a computed path")
            return
        if handler is None and node is not None and qualname is not None:
            handler = Handler(
                module=module.name, qualname=qualname, path=module.path, line=node.lineno
            )
        container.routes.append(
            _Route(
                methods=methods,
                path=path,
                handler=handler,
                params=_annotations(node) if node is not None else {},
                accepts_body=accepts_body
                or (node is not None and self._accepts_body(container.framework, module, node)),
            )
        )

    def _expand(
        self, container: _Container, prefix: str, app: str | None, seen: set[tuple[str, str]]
    ) -> list[Endpoint]:
        if container.key in seen:
            return []
        seen = seen | {container.key}
        own = container.prefix
        if not isinstance(own, str):
            self.notes.append(
                f"{container.key[0]}:{container.key[1]} has a computed prefix; its routes are "
                "not listed"
            )
            return []
        base = (
            join(prefix, own)
            if container.is_app or container.framework is Framework.FASTAPI
            else prefix
        )
        endpoints = [
            self._endpoint(container.framework, method, join(base, route.path), route, app)
            for route in container.routes
            for method in route.methods
        ]
        for child_key, mount_prefix, location in container.mounts:
            child = self.containers[child_key]
            if mount_prefix is DYNAMIC:
                self.notes.append(
                    f"prefix at {location} is computed; the included routes are not listed"
                )
                continue
            if child.framework is Framework.FLASK:
                # Flask: a url_prefix given at registration replaces the blueprint's own.
                child_prefix = mount_prefix if isinstance(mount_prefix, str) else child.prefix
                if not isinstance(child_prefix, str):
                    self.notes.append(f"blueprint prefix at {location} is computed")
                    continue
                endpoints += self._expand(
                    _with_prefix(child, ""), join(base, child_prefix), app, seen
                )
            else:
                extra = mount_prefix if isinstance(mount_prefix, str) else ""
                endpoints += self._expand(child, join(base, extra), app, seen)
        return endpoints

    def _endpoint(
        self, framework: Framework, method: str, template: str, route: _Route, app: str | None
    ) -> Endpoint:
        path, params = canonical(framework, template)
        if framework is Framework.FASTAPI:
            params = [
                PathParam(
                    name=p.name,
                    type=p.type if p.type != "unknown" else route.params.get(p.name, "unknown"),
                )
                for p in params
            ]
        return Endpoint(
            framework=framework,
            method=method,
            path=path,
            params=params,
            handler=route.handler,
            accepts_body=route.accepts_body,
            app=app,
        )

    def _app(self, container: _Container) -> ApiApp:
        module, variable = container.key
        return ApiApp(
            framework=container.framework,
            module=module,
            path=container.path,
            line=container.line,
            variable=variable if container.factory is None else None,
            factory=container.factory,
            factory_needs_arguments=container.factory_needs_arguments,
        )

    # --------------------------------------------------------------------------- Django
    def _django(self) -> tuple[list[ApiApp], list[Endpoint]]:
        roots: list[tuple[str, _Module, int]] = []
        for module in self.modules.values():
            for stmt in module.tree.body:
                if isinstance(stmt, ast.Assign) and _assigns(stmt, "ROOT_URLCONF"):
                    value = _string(stmt.value)
                    if isinstance(value, str):
                        roots.append((value, module, stmt.lineno))
        apps: list[ApiApp] = []
        endpoints: list[Endpoint] = []
        for urlconf, settings, line in sorted(roots, key=lambda r: r[0]):
            if urlconf not in self.modules:
                self.notes.append(
                    f"ROOT_URLCONF {urlconf!r} ({settings.path}:{line}) is not in the repository"
                )
                continue
            root = self.modules[urlconf]
            apps.append(
                ApiApp(
                    framework=Framework.DJANGO,
                    module=urlconf,
                    path=root.path,
                    line=1,
                    variable=None,
                    factory=None,
                )
            )
            endpoints += self._urlconf(root, "", set())
        return apps, endpoints

    def _urlconf(self, module: _Module, prefix: str, seen: set[str]) -> list[Endpoint]:
        if module.name in seen:
            return []
        seen = seen | {module.name}
        endpoints: list[Endpoint] = []
        for element in _urlpatterns(module.tree):
            if not (
                isinstance(element, ast.Call)
                and _call_name(element.func).rsplit(".", 1)[-1] in {"path", "re_path", "url"}
            ):
                continue
            location = f"{module.path}:{element.lineno}"
            route = _string(element.args[0]) if element.args else DYNAMIC
            if not isinstance(route, str) or len(element.args) <= VIEW_ARGUMENT:
                self.notes.append(f"URL pattern at {location} has a computed route")
                continue
            full = prefix + route
            view = element.args[VIEW_ARGUMENT]
            if isinstance(view, ast.Call) and _call_name(view.func).rsplit(".", 1)[-1] == "include":
                included = self._included(module, view)
                if included is None:
                    self.notes.append(f"include() at {location} is not resolved")
                else:
                    endpoints += self._urlconf(included, full, seen)
                continue
            target = self._view(module, view)
            if isinstance(target, _ClassView):
                methods, handler, accepts = (
                    target.methods or [ANY_METHOD],
                    target.handler,
                    target.accepts_body,
                )
            elif target is not None:
                node, qualname, owner = target
                methods = _django_methods(node) or [ANY_METHOD]
                handler = Handler(
                    module=owner.name, qualname=qualname, path=owner.path, line=node.lineno
                )
                accepts = _reads_request(node, DJANGO_BODY)
            else:
                self.notes.append(f"view at {location} is not resolved to a function or class")
                continue
            path, params = canonical(Framework.DJANGO, full)
            endpoints += [
                Endpoint(
                    framework=Framework.DJANGO,
                    method=method,
                    path=path,
                    params=params,
                    handler=handler,
                    accepts_body=accepts,
                    app=None,
                )
                for method in methods
            ]
        return endpoints

    def _included(self, module: _Module, call: ast.Call) -> _Module | None:
        if not call.args:
            return None
        argument = call.args[0]
        name = _string(argument)
        if isinstance(name, str):
            return self.modules.get(name)
        resolved = self._reference(module, argument)
        if resolved is not None and resolved[1] is None:
            return self.modules.get(resolved[0])
        return None

    # --------------------------------------------------------------------------- resolution
    def _reference(self, module: _Module, node: ast.expr) -> tuple[str, str | None] | None:
        """(module, attribute or None for the module itself) that an expression refers to."""
        if isinstance(node, ast.Name):
            if node.id in module.imports:
                target, attribute = module.imports[node.id]
                if attribute is not None and f"{target}.{attribute}" in self.modules:
                    return f"{target}.{attribute}", None
                return target, attribute
            return module.name, node.id
        if isinstance(node, ast.Attribute):
            owner = self._reference(module, node.value)
            if owner is not None and owner[1] is None:
                if f"{owner[0]}.{node.attr}" in self.modules:
                    return f"{owner[0]}.{node.attr}", None
                return owner[0], node.attr
        return None

    def _container(self, module: _Module, node: ast.expr) -> _Container | None:
        resolved = self._reference(module, node)
        if resolved is None or resolved[1] is None:
            return None
        return self.containers.get((resolved[0], resolved[1]))

    def _view(
        self, module: _Module, node: ast.expr
    ) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, _Module] | _ClassView | None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "as_view"
        ):
            found_class = self._class(module, node.func.value)
            if found_class is None:
                return None
            owner, cls = found_class
            defined = {
                n.name for n in cls.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            methods = [m.upper() for m in HTTP_HANDLER_METHODS if m in defined]
            accepts = any(
                _reads_request(n, DJANGO_BODY | FLASK_BODY)
                for n in cls.body
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                and n.name in {"post", "put", "patch"}
            )
            handler = Handler(
                module=owner.name, qualname=cls.name, path=owner.path, line=cls.lineno
            )
            return _ClassView(methods=methods, handler=handler, accepts_body=accepts)
        resolved = self._reference(module, node)
        if resolved is None or resolved[1] is None or resolved[0] not in self.modules:
            return None
        owner = self.modules[resolved[0]]
        found = owner.functions.get(resolved[1])
        if found is None:
            return None
        qualname, function = found
        return function, qualname, owner

    def _accepts_body(
        self, framework: Framework, module: _Module, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> bool:
        if framework is Framework.FLASK:
            return _reads_request(node, FLASK_BODY)
        arguments = node.args.args + node.args.kwonlyargs
        defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(
            node.args.defaults
        )
        defaults += list(node.args.kw_defaults)
        for argument, default in zip(arguments, defaults, strict=True):
            if (
                isinstance(default, ast.Call)
                and _call_name(default.func).rsplit(".", 1)[-1] in BODY_DEFAULTS
            ):
                return True
            if argument.annotation is not None and self._is_model(
                module, argument.annotation, set()
            ):
                return True
        return False

    def _is_model(self, module: _Module, annotation: ast.expr, seen: set[tuple[str, str]]) -> bool:
        """Is the annotation a pydantic model defined in the repository?"""
        if isinstance(annotation, ast.Subscript):  # Annotated[Model, …], list[Model]
            inner = annotation.slice
            first = inner.elts[0] if isinstance(inner, ast.Tuple) and inner.elts else inner
            return self._is_model(module, first, seen)
        found = self._class(module, annotation)
        if found is None or (found[0].name, found[1].name) in seen:
            return False
        owner, cls = found
        seen = seen | {(owner.name, cls.name)}
        return any(
            _call_name(base).rsplit(".", 1)[-1] == "BaseModel" or self._is_model(owner, base, seen)
            for base in cls.bases
        )

    def _class(self, module: _Module, node: ast.expr) -> tuple[_Module, ast.ClassDef] | None:
        resolved = self._reference(module, node)
        if resolved is None or resolved[1] is None or resolved[0] not in self.modules:
            return None
        owner = self.modules[resolved[0]]
        cls = owner.classes.get(resolved[1])
        return (owner, cls) if cls is not None else None


@dataclass(frozen=True, slots=True)
class _ClassView:
    methods: list[str]
    handler: Handler
    accepts_body: bool


# ------------------------------------------------------------------------------ helpers
def _index(module: _Module) -> None:
    package = module.name if module.is_package else module.name.rpartition(".")[0]
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    module.imports[alias.asname] = (alias.name, None)
                else:
                    top = alias.name.split(".")[0]
                    module.imports[top] = (top, None)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join([*parts, *([node.module] if node.module else [])])
            for alias in node.names:
                if alias.name != "*":
                    module.imports[alias.asname or alias.name] = (base, alias.name)
    for stmt in module.tree.body:
        if isinstance(stmt, ast.ClassDef):
            module.classes[stmt.name] = stmt
    for function, qualname in _functions(module.tree):
        module.functions.setdefault(function.name, (qualname, function))


def _functions(
    tree: ast.Module,
) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    """Functions at module level and inside top-level functions (application factories)."""
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            yield stmt, stmt.name
            for inner in ast.walk(stmt):
                if inner is not stmt and isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef):
                    yield inner, f"{stmt.name}.<locals>.{inner.name}"


def _walk_with_factory(
    tree: ast.Module,
) -> Iterator[tuple[ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]]:
    for stmt in tree.body:
        factory = stmt if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) else None
        for node in ast.walk(stmt):
            yield node, factory


def _needs_arguments(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    positional = function.args.posonlyargs + function.args.args
    required = len(positional) - len(function.args.defaults)
    required += sum(1 for d in function.args.kw_defaults if d is None)
    return required > 0


def _decorator_methods(framework: Framework, name: str, decorator: ast.Call) -> list[str] | None:
    if framework is Framework.FASTAPI:
        if name in FASTAPI_METHODS:
            return [name.upper()]
        if name == "api_route":
            return _methods_keyword(decorator) or ["GET"]
        return None
    if name in FLASK_METHODS:
        return [name.upper()]
    if name == "route":
        return _methods_keyword(decorator) or ["GET"]
    return None


def _methods_keyword(call: ast.Call) -> list[str] | None:
    value = _keyword(call, "methods")
    if isinstance(value, ast.List | ast.Tuple | ast.Set):
        methods = [
            e.value.upper()
            for e in value.elts
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        return [m for m in methods if m not in {"HEAD", "OPTIONS"}] or methods or None
    return None


def _django_methods(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str] | None:
    for decorator in node.decorator_list:
        name = _call_name(decorator.func if isinstance(decorator, ast.Call) else decorator)
        short = name.rsplit(".", 1)[-1]
        if short in DJANGO_DECORATORS:
            return DJANGO_DECORATORS[short]
        if (
            short in {"require_http_methods", "api_view"}
            and isinstance(decorator, ast.Call)
            and decorator.args
        ):
            argument = decorator.args[0]
            if isinstance(argument, ast.List | ast.Tuple | ast.Set):
                methods = [
                    e.value.upper() for e in argument.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]  # fmt: skip
                return [m for m in methods if m not in {"HEAD", "OPTIONS"}] or None
    return None


def _reads_request(node: ast.AST, attributes: frozenset[str]) -> bool:
    return any(
        isinstance(n, ast.Attribute) and n.attr in attributes and isinstance(n.value, ast.Name)
        and n.value.id == "request"
        for n in ast.walk(node)
    )  # fmt: skip


def _annotations(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    found = {}
    for argument in node.args.args + node.args.kwonlyargs:
        annotation = argument.annotation
        if isinstance(annotation, ast.Name | ast.Attribute):
            name = _call_name(annotation).rsplit(".", 1)[-1]
            if name in ANNOTATION_TYPES:
                found[argument.arg] = ANNOTATION_TYPES[name]
    return found


def _urlpatterns(tree: ast.Module) -> Iterator[ast.expr]:
    for stmt in tree.body:
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign | ast.AugAssign) and _assigns(stmt, "urlpatterns"):
            value = stmt.value
        if isinstance(value, ast.List | ast.Tuple):
            yield from value.elts


def _assigns(stmt: ast.Assign | ast.AugAssign, name: str) -> bool:
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
    return any(isinstance(t, ast.Name) and t.id == name for t in targets)


def _with_prefix(container: _Container, prefix: str) -> _Container:
    """A copy whose own prefix is replaced (Flask registration prefixes)."""
    return _Container(
        key=container.key, framework=container.framework, is_app=container.is_app, prefix=prefix,
        path=container.path, line=container.line, factory=container.factory,
        factory_needs_arguments=container.factory_needs_arguments, routes=container.routes,
        mounts=container.mounts, mounted=container.mounted,
    )  # fmt: skip


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _keyword_string(call: ast.Call, *names: str) -> str | object:
    for name in names:
        value = _keyword(call, name)
        if value is not None:
            return _string(value)
    return DYNAMIC


def _string(node: ast.expr) -> str | object:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else DYNAMIC


def _call_name(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))
