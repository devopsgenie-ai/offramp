import json
from pathlib import Path

from detect.node_service import PROPOSED_NODE, detect_node_service, find_node_services

FIXTURE = Path("fixtures/emergent-fastapi-mongo")


def write_node(tmp_path, package, extra=None):
    service = tmp_path / "frontend"
    service.mkdir(exist_ok=True)
    (service / "package.json").write_text(json.dumps(package))
    for name, content in (extra or {}).items():
        (service / name).write_text(content)
    return service


def test_finds_the_frontend_directory():
    assert [p.name for p in find_node_services(FIXTURE)] == ["frontend"]


def test_node_modules_is_never_itself_a_service(tmp_path):
    """frontend/node_modules/leftpad/package.json must not become a service."""
    found = find_node_services(FIXTURE)
    assert all("node_modules" not in p.parts for p in found)


def test_role_is_web_and_name_comes_from_the_directory():
    service, _, _ = detect_node_service(FIXTURE, FIXTURE / "frontend")
    assert service.name == "frontend"
    assert service.role == "web"


def test_react_scripts_build_output_is_the_build_directory():
    service, _, _ = detect_node_service(FIXTURE, FIXTURE / "frontend")
    assert service.build.build_cmd == "npm run build"
    assert service.build.output_dir == "build"


def test_vite_build_output_is_the_dist_directory(tmp_path):
    service_dir = write_node(tmp_path, {
        "name": "f", "devDependencies": {"vite": "^5.0.0"},
        "scripts": {"build": "vite build"},
    })
    service, _, _ = detect_node_service(tmp_path, service_dir)
    assert service.build.output_dir == "dist"


def test_ports_and_probes_are_empty_for_a_static_build():
    service, _, _ = detect_node_service(FIXTURE, FIXTURE / "frontend")
    assert service.ports == []
    assert service.probes == []


def test_the_existing_dockerfile_is_reused_rather_than_replaced():
    """v1 reuses an existing source Dockerfile rather than writing a second one."""
    service, gaps, _ = detect_node_service(FIXTURE, FIXTURE / "frontend")
    assert service.build.dockerfile == "frontend/Dockerfile"
    assert not [g for g in gaps if g.id.endswith("build.dockerfile")]


def test_a_missing_lockfile_is_an_action_gap_and_install_is_not_npm_ci():
    service, gaps, _ = detect_node_service(FIXTURE, FIXTURE / "frontend")
    assert service.build.install_cmd == "npm install"
    gap = next(g for g in gaps if g.id == "service.frontend.dependencies.lockfile")
    assert gap.kind == "action"


def test_a_lockfile_switches_install_to_npm_ci(tmp_path):
    service_dir = write_node(
        tmp_path,
        {"name": "f", "scripts": {"build": "react-scripts build"},
         "dependencies": {"react-scripts": "5.0.1"}},
        extra={"package-lock.json": '{"lockfileVersion":3}'},
    )
    service, gaps, _ = detect_node_service(tmp_path, service_dir)
    assert service.build.install_cmd == "npm ci"
    assert not [g for g in gaps if g.id.endswith("dependencies.lockfile")]


def test_missing_node_version_is_a_gap_not_a_default():
    service, gaps, _ = detect_node_service(FIXTURE, FIXTURE / "frontend")
    assert service.runtime.version is None
    gap = next(g for g in gaps if g.id == "service.frontend.runtime.version")
    assert gap.proposed == PROPOSED_NODE


def test_an_engines_field_is_read(tmp_path):
    service_dir = write_node(tmp_path, {
        "name": "f", "engines": {"node": ">=22"},
        "scripts": {"build": "vite build"}, "devDependencies": {"vite": "^5.0.0"},
    })
    service, gaps, _ = detect_node_service(tmp_path, service_dir)
    assert service.runtime.version == "22"
    assert not [g for g in gaps if g.id.endswith("runtime.version")]


def test_an_unknown_bundler_leaves_output_dir_open_as_a_gap(tmp_path):
    service_dir = write_node(tmp_path, {"name": "f", "scripts": {"build": "bespoke-build"}})
    service, gaps, _ = detect_node_service(tmp_path, service_dir)
    assert service.build.output_dir is None
    assert any(g.id == "service.frontend.build.output_dir" for g in gaps)
