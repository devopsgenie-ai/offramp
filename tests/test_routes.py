from pathlib import Path

from detect.routes import detect_routes, join_path

FIXTURE = Path("fixtures/emergent-fastapi-mongo")
SETTINGS = Path("fixtures/emergent-settings-prefix")


def test_join_path_normalises():
    assert join_path("/api", "", "/health") == "/api/health"
    assert join_path("/api", "/items", "") == "/api/items"
    assert join_path("", "", "") == "/"
    assert join_path("/api/", "/items/") == "/api/items"


def test_composes_the_router_prefix_with_the_decorator_literal():
    """The case the Problem section is about. /api/health, never /health."""
    routes, gaps, _ = detect_routes(FIXTURE, FIXTURE / "backend", "backend")
    assert sorted(route.path for route in routes) == ["/api/health", "/api/items"]
    assert not [gap for gap in gaps if "prefix" in gap.id]


def test_does_not_report_the_bare_decorator_literal():
    routes, _, _ = detect_routes(FIXTURE, FIXTURE / "backend", "backend")
    assert "/health" not in [route.path for route in routes]


def test_routes_name_their_service_and_leave_host_and_port_open():
    routes, _, _ = detect_routes(FIXTURE, FIXTURE / "backend", "backend")
    for route in routes:
        assert route.service == "backend"
        assert route.host is None
        assert route.port is None
        assert route.tls is False


def test_cites_both_the_mount_and_the_decorator():
    _, _, evidence = detect_routes(FIXTURE, FIXTURE / "backend", "backend")
    paths = {item.path for item in evidence}
    assert "backend/server.py" in paths
    assert "backend/routes/health.py" in paths


def test_a_non_literal_prefix_refuses_to_compose():
    routes, gaps, _ = detect_routes(SETTINGS, SETTINGS / "backend", "backend")
    assert routes == []
    gap = next(g for g in gaps if g.id == "service.backend.routes.prefix")
    assert gap.severity == "blocking"
    assert gap.proposed is None
    assert "settings.api_prefix" in gap.question
    assert "backend/server.py" in gap.evidence[0]


def test_the_undecidable_case_never_reports_a_path():
    routes, _, _ = detect_routes(SETTINGS, SETTINGS / "backend", "backend")
    assert [route.path for route in routes] == []


def test_routes_are_sorted_deterministically():
    first, _, _ = detect_routes(FIXTURE, FIXTURE / "backend", "backend")
    second, _, _ = detect_routes(FIXTURE, FIXTURE / "backend", "backend")
    assert [r.path for r in first] == [r.path for r in second]
    assert [r.path for r in first] == sorted(r.path for r in first)


def test_a_router_included_directly_on_the_app_needs_no_intermediate(tmp_path):
    service = tmp_path / "backend"
    (service / "routes").mkdir(parents=True)
    (service / "requirements.txt").write_text("fastapi==0.110.0\n")
    (service / "routes" / "ping.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/ping')\n"
        "@router.get('/now')\n"
        "def now():\n    return 1\n"
    )
    (service / "server.py").write_text(
        "from fastapi import FastAPI\n"
        "from routes.ping import router as ping_router\n"
        "app = FastAPI()\n"
        "app.include_router(ping_router)\n"
    )
    routes, gaps, _ = detect_routes(tmp_path, service, "backend")
    assert [r.path for r in routes] == ["/ping/now"]


def test_an_include_prefix_and_a_router_prefix_both_apply(tmp_path):
    service = tmp_path / "backend"
    (service / "routes").mkdir(parents=True)
    (service / "requirements.txt").write_text("fastapi==0.110.0\n")
    (service / "routes" / "v1.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/items')\n"
        "@router.get('/{item_id}')\n"
        "def get(item_id: str):\n    return item_id\n"
    )
    (service / "server.py").write_text(
        "from fastapi import FastAPI\n"
        "from routes.v1 import router as v1\n"
        "app = FastAPI()\n"
        "app.include_router(v1, prefix='/api/v1')\n"
    )
    routes, _, _ = detect_routes(tmp_path, service, "backend")
    assert [r.path for r in routes] == ["/api/v1/items/{item_id}"]


def test_a_router_that_is_never_mounted_is_not_a_route(tmp_path):
    """An orphan router serves no traffic. Reporting it would generate a dead Ingress."""
    service = tmp_path / "backend"
    (service / "routes").mkdir(parents=True)
    (service / "requirements.txt").write_text("fastapi==0.110.0\n")
    (service / "routes" / "dead.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/nowhere')\n"
        "def nowhere():\n    return 1\n"
    )
    (service / "server.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
    )
    routes, _, _ = detect_routes(tmp_path, service, "backend")
    assert routes == []
