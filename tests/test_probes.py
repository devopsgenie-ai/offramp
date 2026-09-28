from detect.probes import detect_probes
from spec import Route


def route(path):
    return Route(host=None, path=path, service="backend", port=None, tls=False)


def test_a_health_route_becomes_both_readiness_and_liveness():
    probes, gaps = detect_probes([route("/api/health"), route("/api/items")], "backend", 8000)
    assert {probe.role for probe in probes} == {"readiness", "liveness"}
    assert all(probe.path == "/api/health" for probe in probes)
    assert all(probe.kind == "http" for probe in probes)
    assert all(probe.port == 8000 for probe in probes)
    assert gaps == []


def test_probes_use_the_composed_path_not_the_decorator_literal():
    probes, _ = detect_probes([route("/api/health")], "backend", 8000)
    assert [probe.path for probe in probes] == ["/api/health", "/api/health"]


def test_probes_are_sorted_by_role_for_determinism():
    probes, _ = detect_probes([route("/api/health")], "backend", 8000)
    assert [probe.role for probe in probes] == ["liveness", "readiness"]


def test_no_health_route_is_an_action_gap_rather_than_an_invented_path():
    probes, gaps = detect_probes([route("/api/items")], "backend", 8000)
    assert probes == []
    gap = next(g for g in gaps if g.id == "service.backend.probes")
    assert gap.kind == "action"
    assert gap.severity == "important"
    assert "/api/items" not in gap.question


def test_an_unknown_port_defers_to_the_port_gap():
    probes, gaps = detect_probes([route("/api/health")], "backend", None)
    assert probes == []
    gap = next(g for g in gaps if g.id == "service.backend.probes.port")
    assert gap.depends_on == ["service.backend.ports"]


def test_no_routes_at_all_produces_no_probes_and_no_duplicate_gap():
    probes, gaps = detect_probes([], "frontend", None)
    assert probes == []
    assert [gap.id for gap in gaps] == ["service.frontend.probes"]


def test_health_variants_are_recognised():
    for path in ("/healthz", "/api/livez", "/readyz", "/ping", "/_health"):
        probes, _ = detect_probes([route(path)], "backend", 8000)
        assert len(probes) == 2, path
