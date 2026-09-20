from pathlib import Path

from detect.python_service import (
    PROPOSED_PYTHON, detect_python_service, find_python_services,
)

FIXTURE = Path("fixtures/emergent-fastapi-mongo")


def test_finds_the_backend_directory():
    assert [p.name for p in find_python_services(FIXTURE)] == ["backend"]


def test_service_name_comes_from_the_build_context_directory():
    service, _, _ = detect_python_service(FIXTURE, FIXTURE / "backend")
    assert service.name == "backend"
    assert service.build.context == "backend"


def test_role_is_api_for_a_fastapi_service():
    service, _, _ = detect_python_service(FIXTURE, FIXTURE / "backend")
    assert service.role == "api"


def test_install_command_uses_the_requirements_file():
    service, _, _ = detect_python_service(FIXTURE, FIXTURE / "backend")
    assert service.build.install_cmd == "pip install -r requirements.txt"


def test_start_command_names_the_entrypoint_module_and_the_detected_port():
    service, _, _ = detect_python_service(FIXTURE, FIXTURE / "backend")
    assert service.build.start_cmd == "uvicorn server:app --host 0.0.0.0 --port 8001"


def test_the_listen_port_is_read_from_the_uvicorn_call():
    service, gaps, _ = detect_python_service(FIXTURE, FIXTURE / "backend")
    assert service.ports == [8001]
    assert not [g for g in gaps if g.id == "service.backend.ports"]


def test_an_exposed_port_in_a_dockerfile_is_read(tmp_path):
    service_dir = tmp_path / "backend"
    service_dir.mkdir()
    (service_dir / "requirements.txt").write_text("fastapi==0.110.0\n")
    (service_dir / "server.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (service_dir / "Dockerfile").write_text("FROM python:3.11-slim\nEXPOSE 9000\n")
    service, gaps, _ = detect_python_service(tmp_path, service_dir)
    assert service.ports == [9000]


def test_no_dockerfile_in_the_backend_so_the_field_is_none():
    service, _, _ = detect_python_service(FIXTURE, FIXTURE / "backend")
    assert service.build.dockerfile is None


def test_missing_runtime_version_is_a_gap_not_a_default():
    service, gaps, _ = detect_python_service(FIXTURE, FIXTURE / "backend")
    assert service.runtime.version is None
    gap = next(g for g in gaps if g.id == "service.backend.runtime.version")
    assert gap.proposed == PROPOSED_PYTHON
    assert gap.severity == "important"
    assert gap.confidence == "medium"


def test_declared_runtime_version_is_read_and_raises_no_gap(tmp_path):
    service_dir = tmp_path / "backend"
    service_dir.mkdir()
    (service_dir / "requirements.txt").write_text("fastapi==0.110.0\n")
    (service_dir / "server.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (service_dir / ".python-version").write_text("3.12.1\n")
    service, gaps, evidence = detect_python_service(tmp_path, service_dir)
    assert service.runtime.version == "3.12.1"
    assert not [g for g in gaps if g.id.endswith("runtime.version")]
    assert "backend/.python-version" in [item.path for item in evidence]


def test_no_port_anywhere_is_a_gap_and_start_cmd_omits_the_flag(tmp_path):
    """A value that is not in the repository is not the detector's to invent."""
    service_dir = tmp_path / "backend"
    service_dir.mkdir()
    (service_dir / "requirements.txt").write_text("fastapi==0.110.0\n")
    (service_dir / "server.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    service, gaps, _ = detect_python_service(tmp_path, service_dir)
    assert service.ports == []
    assert service.build.start_cmd == "uvicorn server:app --host 0.0.0.0"
    assert any(g.id == "service.backend.ports" for g in gaps)


def test_an_existing_dockerfile_is_recorded_for_reuse(tmp_path):
    service_dir = tmp_path / "backend"
    service_dir.mkdir()
    (service_dir / "requirements.txt").write_text("fastapi==0.110.0\n")
    (service_dir / "server.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (service_dir / "Dockerfile").write_text("FROM python:3.11-slim\n")
    service, _, _ = detect_python_service(tmp_path, service_dir)
    assert service.build.dockerfile == "backend/Dockerfile"


def test_unpinned_dependency_raises_an_action_gap(tmp_path):
    service_dir = tmp_path / "backend"
    service_dir.mkdir()
    (service_dir / "requirements.txt").write_text("fastapi>=0.110.0\nmotor\n")
    (service_dir / "server.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    _, gaps, _ = detect_python_service(tmp_path, service_dir)
    gap = next(g for g in gaps if g.id == "service.backend.dependencies.pinned")
    assert gap.kind == "action"
    assert "fastapi" in gap.question and "motor" in gap.question


def test_fully_pinned_dependencies_raise_no_pinning_gap():
    _, gaps, _ = detect_python_service(FIXTURE, FIXTURE / "backend")
    assert not [g for g in gaps if g.id.endswith("dependencies.pinned")]
