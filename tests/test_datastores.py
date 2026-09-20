from pathlib import Path

from detect.datastores import detect_datastores, tag_datastore_env
from detect.env import detect_env
from detect.python_service import detect_python_service
from spec import Build, EnvVar, Resources, Runtime, Service

FIXTURE = Path("fixtures/emergent-fastapi-mongo")


def backend_service(root=FIXTURE, directory=None):
    directory = directory or (root / "backend")
    service, _, _ = detect_python_service(root, directory)
    service.env, _, _ = detect_env(root, directory, service.name)
    return service


def test_mongodb_is_detected_from_the_client_construction():
    datastores, gaps, evidence = detect_datastores(FIXTURE, [backend_service()])
    assert [d.kind for d in datastores] == ["mongodb"]
    assert datastores[0].name == "mongodb"
    assert "backend/services/db.py" in {item.path for item in evidence}


def test_consumed_by_names_the_service_that_imports_it():
    datastores, _, _ = detect_datastores(FIXTURE, [backend_service()])
    assert datastores[0].consumed_by == ["backend"]


def test_env_keys_are_the_connection_keys_the_service_reads():
    datastores, _, _ = detect_datastores(FIXTURE, [backend_service()])
    assert datastores[0].env_keys == ["MONGO_URL"]


def test_mode_is_never_defaulted_and_is_a_blocking_gap():
    datastores, gaps, _ = detect_datastores(FIXTURE, [backend_service()])
    assert datastores[0].mode is None
    gap = next(g for g in gaps if g.id == "datastore.mongodb.mode")
    assert gap.severity == "blocking"
    assert gap.proposed is None


def test_version_depends_on_mode_so_it_can_be_retired_unasked():
    datastores, gaps, _ = detect_datastores(FIXTURE, [backend_service()])
    assert datastores[0].version is None
    gap = next(g for g in gaps if g.id == "datastore.mongodb.version")
    assert gap.depends_on == ["datastore.mongodb.mode"]


def test_a_dependency_with_no_import_is_a_disagreement_gap_not_a_datastore(tmp_path):
    """motor in requirements.txt with nothing importing it."""
    service_dir = tmp_path / "backend"
    service_dir.mkdir()
    (service_dir / "requirements.txt").write_text("fastapi==0.110.0\nmotor==3.3.2\n")
    (service_dir / "server.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    datastores, gaps, _ = detect_datastores(tmp_path, [backend_service(tmp_path, service_dir)])
    assert datastores == []
    gap = next(g for g in gaps if g.id == "datastore.mongodb.disputed")
    assert "motor" in gap.question
    assert "requirements.txt" in gap.question
    assert gap.severity == "important"


def test_an_import_with_no_dependency_still_counts_as_usage(tmp_path):
    service_dir = tmp_path / "backend"
    service_dir.mkdir()
    (service_dir / "requirements.txt").write_text("fastapi==0.110.0\n")
    (service_dir / "server.py").write_text(
        "from fastapi import FastAPI\n"
        "from redis import Redis\n"
        "app = FastAPI()\n"
        "cache = Redis.from_url('redis://localhost:6379')\n"
    )
    datastores, _, _ = detect_datastores(tmp_path, [backend_service(tmp_path, service_dir)])
    assert [d.kind for d in datastores] == ["redis"]


def test_datastores_are_sorted_by_name():
    datastores, _, _ = detect_datastores(FIXTURE, [backend_service()])
    assert [d.name for d in datastores] == sorted(d.name for d in datastores)


def test_tag_datastore_env_marks_the_connection_key_as_datastore_provenance():
    service = backend_service()
    datastores, _, _ = detect_datastores(FIXTURE, [service])
    tagged = tag_datastore_env([service], datastores)
    mongo = next(v for v in tagged[0].env if v.name == "MONGO_URL")
    assert mongo.source == "datastore"
    assert mongo.sensitive is True


def test_tag_datastore_env_leaves_other_keys_alone():
    service = backend_service()
    datastores, _, _ = detect_datastores(FIXTURE, [service])
    tagged = tag_datastore_env([service], datastores)
    assert next(v for v in tagged[0].env if v.name == "DB_NAME").source == "literal"


def test_tag_datastore_env_does_not_mutate_its_input():
    service = backend_service()
    datastores, _, _ = detect_datastores(FIXTURE, [service])
    tag_datastore_env([service], datastores)
    assert next(v for v in service.env if v.name == "MONGO_URL").source != "datastore"
