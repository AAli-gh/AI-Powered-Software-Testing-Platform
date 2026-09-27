"""Static endpoint discovery: every supported construct, and what is reported instead of
guessed. Golden tests on the example web applications are in test_examples.py."""

import ast
from textwrap import dedent

from testmind.api_engine.discovery import discover
from testmind.api_engine.models import ApiSurface, Endpoint


def surface(files: dict[str, str]) -> ApiSurface:
    return discover(
        (path, ast.parse(dedent(source).lstrip("\n"))) for path, source in files.items()
    )


def keys(api: ApiSurface) -> list[str]:
    return [e.key for e in api.endpoints]


def by_key(api: ApiSurface) -> dict[str, Endpoint]:
    return {e.key: e for e in api.endpoints}


# ----------------------------------------------------------------------------------- FastAPI
FASTAPI_APP = {
    "src/shop/__init__.py": "",
    "src/shop/main.py": """
        from fastapi import FastAPI
        from .routers import items
        from shop.routers.admin import router as admin_router

        app = FastAPI(title="shop")
        app.include_router(items.router, prefix="/v1")
        app.include_router(admin_router)

        @app.get("/health")
        def health():
            return {}
    """,
    "src/shop/routers/__init__.py": "",
    "src/shop/routers/items.py": """
        from typing import Annotated
        from uuid import UUID
        from fastapi import APIRouter, Body
        from shop.models import Item

        router = APIRouter(prefix="/items")
        nested = APIRouter(prefix="/{item_id}/tags")
        router.include_router(nested)

        @router.get("/{item_id}")
        def get_item(item_id: int): ...

        @router.api_route("/", methods=["post", "put"])
        async def save(item: Item): ...

        @router.patch("/{key}")
        def patch(key: UUID, payload: Annotated[Item, Body()]): ...

        @router.delete("/{rest:path}")
        def delete(rest: str, note: str = Body(None)): ...

        @nested.get("/")
        def tags(item_id: int): ...

        def raw(): ...
        router.add_api_route("/raw", raw, methods=["GET"])
    """,
    "src/shop/routers/admin.py": """
        import fastapi
        router = fastapi.APIRouter(prefix="/admin")

        @router.api_route("/stats")
        def stats(): ...
    """,
    "src/shop/models.py": """
        from pydantic import BaseModel

        class Base(BaseModel):
            id: int

        class Item(Base):
            title: str
    """,
}


def test_fastapi_routes_prefixes_and_includes() -> None:
    api = surface(FASTAPI_APP)
    assert keys(api) == [
        "GET /admin/stats",
        "GET /health",
        "POST /v1/items/",
        "PUT /v1/items/",
        "GET /v1/items/raw",
        "GET /v1/items/{item_id}",
        "GET /v1/items/{item_id}/tags/",
        "PATCH /v1/items/{key}",
        "DELETE /v1/items/{rest}",
    ]
    [app] = api.apps
    assert (app.framework, app.target, app.path, app.line) == (
        "fastapi", "shop.main:app", "src/shop/main.py", 5,
    )  # fmt: skip
    assert all(e.app == "shop.main:app" for e in api.endpoints)
    assert api.notes == []


def test_fastapi_parameters_bodies_and_handlers() -> None:
    endpoints = by_key(surface(FASTAPI_APP))
    get_item = endpoints["GET /v1/items/{item_id}"]
    assert [p.model_dump() for p in get_item.params] == [{"name": "item_id", "type": "int"}]
    assert get_item.handler is not None
    assert (get_item.handler.module, get_item.handler.qualname, get_item.handler.line) == (
        "shop.routers.items", "get_item", 11,
    )  # fmt: skip
    assert endpoints["PATCH /v1/items/{key}"].params[0].type == "uuid"
    assert endpoints["DELETE /v1/items/{rest}"].params[0].type == "path"  # {rest:path}
    # Bodies: a repository pydantic model (also through a base class), Annotated[Model, …],
    # or a Body() default. A plain path parameter is not a body.
    assert endpoints["POST /v1/items/"].accepts_body
    assert endpoints["PATCH /v1/items/{key}"].accepts_body
    assert endpoints["DELETE /v1/items/{rest}"].accepts_body
    assert not get_item.accepts_body
    raw = endpoints["GET /v1/items/raw"]
    assert raw.handler is not None
    assert raw.handler.qualname == "raw"


def test_fastapi_unresolved_constructs_are_notes() -> None:
    api = surface(
        {
            "app.py": """
                from fastapi import FastAPI, APIRouter
                import settings

                app = FastAPI()
                loose = APIRouter()
                dynamic = APIRouter(prefix=settings.PREFIX)
                app.include_router(dynamic)
                app.include_router(settings.router)
                app.include_router(APIRouter(), prefix=settings.PREFIX)

                @loose.get("/orphan")
                def orphan(): ...

                @dynamic.get("/x")
                def x(): ...

                @app.get(settings.PATH)
                def computed(): ...

                @app.get("/ok")
                def ok(): ...

                @app.middleware("http")
                async def middleware(request, call_next): ...
            """,
        }
    )
    assert keys(api) == ["GET /ok"]
    assert api.notes == [
        "route at app.py:17 has a computed path",
        "include_router at app.py:8: the included object is not resolved",
        "include_router at app.py:9: the included object is not resolved",
        "app:dynamic has a computed prefix; its routes are not listed",
        "router app:loose (1 route(s)) is not included in any application found statically",
    ]


def test_an_app_created_by_a_factory_with_arguments_has_no_target() -> None:
    api = surface(
        {
            "factory.py": """
                from fastapi import FastAPI

                def build(settings, *, debug=False):
                    app = FastAPI()

                    @app.get("/")
                    def root(): ...

                    return app
            """,
        }
    )
    [app] = api.apps
    assert (app.factory, app.factory_needs_arguments, app.target) == ("build", True, None)
    assert keys(api) == ["GET /"]
    assert api.endpoints[0].handler is not None
    assert api.endpoints[0].handler.qualname == "build.<locals>.root"


def test_mount_prefix_that_is_computed_is_reported() -> None:
    api = surface(
        {
            "app.py": """
                from fastapi import FastAPI, APIRouter
                import settings

                app = FastAPI()
                router = APIRouter()
                app.include_router(router, prefix=settings.PREFIX)

                @router.get("/x")
                def x(): ...
            """,
        }
    )
    assert api.endpoints == []
    assert api.notes == ["prefix at app.py:6 is computed; the included routes are not listed"]


# ------------------------------------------------------------------------------------- Flask
FLASK_APP = {
    "notes/__init__.py": """
        from flask import Flask, request
        from flask.views import MethodView
        from notes.api import bp

        def create_app(config=None):
            app = Flask(__name__)
            app.register_blueprint(bp, url_prefix="/api")

            @app.route("/")
            def index():
                return "hi"

            @app.route("/notes/<int:note_id>", methods=["GET", "HEAD", "DELETE"])
            def note(note_id):
                return ""

            @app.post("/notes")
            def add():
                return request.get_json()

            app.add_url_rule("/about", view_func=about)
            app.add_url_rule("/items", view_func=ItemView.as_view("items"))
            return app

        def about():
            return ""

        class ItemView(MethodView):
            def get(self): ...
            def post(self):
                return request.form
    """,
    "notes/api.py": """
        from flask import Blueprint
        bp = Blueprint("api", __name__, url_prefix="/ignored")
        other = Blueprint("other", __name__, url_prefix="/other")
        bp.register_blueprint(other)

        @bp.get("/files/<path:name>")
        def files(name): ...

        @bp.route("/users/<username>")
        def user(username): ...

        @other.put("/<uuid:key>")
        def put(key): ...
    """,
}


def test_flask_routes_blueprints_and_factories() -> None:
    api = surface(FLASK_APP)
    assert keys(api) == [
        "GET /",
        "GET /about",
        "GET /api/files/{name}",
        "PUT /api/other/{key}",
        "GET /api/users/{username}",
        "GET /items",
        "POST /items",
        "POST /notes",
        "DELETE /notes/{note_id}",
        "GET /notes/{note_id}",
    ]
    [app] = api.apps
    assert (app.target, app.factory, app.variable) == ("notes:create_app()", "create_app", None)
    endpoints = by_key(api)
    assert [p.model_dump() for p in endpoints["GET /notes/{note_id}"].params] == [
        {"name": "note_id", "type": "int"}
    ]
    assert endpoints["GET /api/files/{name}"].params[0].type == "path"
    assert endpoints["GET /api/users/{username}"].params[0].type == "str"
    assert endpoints["PUT /api/other/{key}"].params[0].type == "uuid"
    assert endpoints["POST /notes"].accepts_body
    assert endpoints["POST /items"].accepts_body  # the class view's post() reads request.form
    assert not endpoints["GET /"].accepts_body
    items = endpoints["GET /items"].handler
    assert items is not None
    assert items.qualname == "ItemView"
    index = endpoints["GET /"].handler
    assert index is not None
    assert index.qualname == "create_app.<locals>.index"


def test_flask_module_level_app_and_blueprint_own_prefix() -> None:
    api = surface(
        {
            "app.py": """
                import flask
                from views import bp

                app = flask.Flask(__name__)
                app.register_blueprint(bp)

                @app.get("/")
                def root(): ...
            """,
            "views.py": """
                from flask import Blueprint
                bp = Blueprint("v", __name__, url_prefix="/v")

                @bp.delete("/<name>")
                def remove(name): ...
            """,
        }
    )
    assert keys(api) == ["GET /", "DELETE /v/{name}"]
    assert api.apps[0].target == "app:app"


def test_flask_computed_blueprint_prefix_is_reported() -> None:
    api = surface(
        {
            "app.py": """
                from flask import Flask, Blueprint
                import config

                app = Flask(__name__)
                bp = Blueprint("b", __name__, url_prefix=config.PREFIX)
                app.register_blueprint(bp)

                @bp.get("/x")
                def x(): ...
            """,
        }
    )
    assert api.endpoints == []
    assert api.notes == ["blueprint prefix at app.py:6 is computed"]


# ------------------------------------------------------------------------------------ Django
DJANGO_SITE = {
    "site_conf/settings.py": 'ROOT_URLCONF = "site_conf.urls"\nDEBUG = True\n',
    "site_conf/urls.py": """
        from django.contrib import admin
        from django.urls import include, path, re_path
        from blog import urls as blog_urls
        import settings_helper

        urlpatterns = [
            path("admin/", admin.site.urls),
            path("blog/", include(blog_urls)),
            path("api/", include("api.urls")),
            path(settings_helper.ROUTE, include("api.urls")),
            path("x/", include(settings_helper.MODULE)),
        ]
    """,
    "blog/__init__.py": "",
    "blog/urls.py": """
        from django.urls import path, re_path
        from blog import views
        from blog.views import PostView

        urlpatterns = [
            path("", views.index),
            path("<slug:slug>/", views.detail),
            re_path(r"^archive/(?P<year>[0-9]{4})/$", views.archive),
            re_path(r"^by-id/(?P<pk>\\d+)/$", views.by_id),
        ]
        urlpatterns += [path("posts/", PostView.as_view())]
    """,
    "blog/views.py": """
        from django.views import View
        from django.views.decorators.http import require_GET, require_http_methods

        @require_GET
        def index(request): ...

        @require_http_methods(["GET", "POST", "HEAD"])
        def detail(request, slug):
            return request.POST

        def archive(request, year): ...
        def by_id(request, pk): ...

        class PostView(View):
            def get(self, request): ...
            def delete(self, request): ...
    """,
    "api/__init__.py": "",
    "api/urls.py": """
        from django.urls import path
        from rest_framework.decorators import api_view

        @api_view(["PUT"])
        def update(request, pk):
            return request.data

        urlpatterns = [path("items/<int:pk>", update)]
    """,
}


def test_django_urlconf_includes_views_and_methods() -> None:
    api = surface(DJANGO_SITE)
    assert keys(api) == [
        "PUT /api/items/{pk}",
        "GET /blog/",
        "ANY /blog/archive/{year}/",
        "ANY /blog/by-id/{pk}/",
        "DELETE /blog/posts/",
        "GET /blog/posts/",
        "GET /blog/{slug}/",
        "POST /blog/{slug}/",
    ]
    endpoints = by_key(api)
    assert endpoints["ANY /blog/archive/{year}/"].params[0].type == "regex"
    assert endpoints["ANY /blog/by-id/{pk}/"].params[0].type == "int"
    assert endpoints["GET /blog/{slug}/"].params[0].type == "slug"
    assert endpoints["POST /blog/{slug}/"].accepts_body
    assert endpoints["PUT /api/items/{pk}"].accepts_body
    handler = endpoints["GET /blog/posts/"].handler
    assert handler is not None
    assert (handler.module, handler.qualname) == ("blog.views", "PostView")
    [app] = api.apps
    assert (app.framework, app.module, app.target) == ("django", "site_conf.urls", None)
    assert api.notes == [
        "view at site_conf/urls.py:7 is not resolved to a function or class",
        "URL pattern at site_conf/urls.py:10 has a computed route",
        "include() at site_conf/urls.py:11 is not resolved",
    ]


def test_django_root_urlconf_outside_the_repository() -> None:
    api = surface({"settings.py": 'ROOT_URLCONF = "elsewhere.urls"\n'})
    assert api.endpoints == []
    assert api.notes == ["ROOT_URLCONF 'elsewhere.urls' (settings.py:1) is not in the repository"]


def test_nothing_to_discover() -> None:
    assert surface({"lib.py": "def f():\n    return 1\n"}) == ApiSurface()


def test_less_common_forms_and_dead_ends() -> None:
    api = surface(
        {
            "pkg/__init__.py": "",
            "pkg/routes.py": """
                from fastapi import APIRouter
                router = APIRouter()

                @router.get(path="/kw")
                def by_keyword(): ...
            """,
            "main.py": """
                import functools
                import pkg
                import pkg.routes as routes_alias
                from fastapi import FastAPI, APIRouter
                from helpers import *

                app = FastAPI()
                a = APIRouter(prefix="/a")
                b = APIRouter(prefix="/b")
                a.include_router(b)
                b.include_router(a)  # a cycle: expanded once
                app.include_router(a)
                app.include_router(pkg.routes.router)
                something.include_router(a)

                @a.get("/x", methods=METHODS)
                def x(): ...

                @functools.lru_cache()
                def cached(): ...

                @app.on_event("startup")
                def start(): ...
            """,
            "web.py": """
                from flask import Flask
                import views

                app = Flask(__name__)
                app.add_url_rule("/positional", "name", views.page)
                app.add_url_rule("/external", view_func=views.missing)
                app.add_url_rule("/lib", view_func=flask_views.Thing.as_view("t"))
                app.add_url_rule("/nothing")

                @app.route("/m", methods=METHODS)
                def m(): ...

                @app.route(rule="/r")
                def r(): ...

                @app.get()
                def no_path(): ...

                @app.errorhandler(404)
                def not_found(error): ...
            """,
            "views.py": "def page(): ...\n",
        }
    )
    assert keys(api) == [
        "GET /a/x",
        "GET /external",
        "GET /kw",
        "GET /lib",
        "GET /m",
        "GET /nothing",
        "GET /positional",
        "GET /r",
    ]
    endpoints = by_key(api)
    positional = endpoints["GET /positional"].handler
    assert positional is not None
    assert (positional.module, positional.qualname) == ("views", "page")
    assert endpoints["GET /external"].handler is None  # the view is not in the repository
    assert endpoints["GET /lib"].handler is None
    assert api.notes == ["route at web.py:16 has a computed path"]


def test_django_dead_ends() -> None:
    api = surface(
        {
            "settings.py": "ROOT_URLCONF = compute()\nROOT_URLCONF = 'urls'\n",
            "urls.py": """
                from django.urls import include, path
                from django.conf.urls.static import static
                import views
                import urls as myself

                urlpatterns = [
                    path("self/", include(myself)),
                    path("empty/", include()),
                    path("missing/", views.missing),
                    path("lib/", Other.as_view()),
                    path("any/", views.anything),
                    path("kw/", views.keyword),
                    path("route"),
                ]
                urlpatterns += static("/media/")
                urlpatterns += [views.not_a_path]
            """,
            "views.py": """
                from django.views.decorators.http import require_http_methods

                @require_http_methods(METHODS)
                def anything(request): ...

                @require_http_methods
                def keyword(request): ...
            """,
        }
    )
    # The urlconf including itself is expanded once.
    assert keys(api) == ["ANY /any/", "ANY /kw/"]
    assert api.notes == [
        "include() at urls.py:8 is not resolved",
        "view at urls.py:9 is not resolved to a function or class",
        "view at urls.py:10 is not resolved to a function or class",
        "URL pattern at urls.py:13 has a computed route",
    ]


def test_mounted_applications_are_part_of_the_parent() -> None:
    api = surface(
        {
            "main.py": """
                from fastapi import FastAPI
                from fastapi.staticfiles import StaticFiles
                from admin import admin_app

                app = FastAPI()
                inner = FastAPI()
                app.mount("/admin", admin_app)
                app.mount(path="/inner", app=inner)
                app.mount("/static", StaticFiles(directory="static"))
                app.mount("/nothing")

                @inner.get("/x")
                def x(): ...
            """,
            "admin.py": """
                from fastapi import FastAPI
                admin_app = FastAPI()

                @admin_app.post("/users")
                def create(): ...
            """,
        }
    )
    assert [a.target for a in api.apps] == ["main:app"]  # the mounted apps are not roots
    assert keys(api) == ["POST /admin/users", "GET /inner/x"]
    assert {e.app for e in api.endpoints} == {"main:app"}
