"""The first three checks and the registry that runs them. RFC-0002, Checks."""

import json
from pathlib import Path

from checks import CHECKS, run_checks
from findings import check_finding_enums
from scan import scan_repo


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _jwt(payload: dict) -> str:
    import base64

    def part(value: dict) -> str:
        raw = json.dumps(value).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(payload)}.c2lnbmF0dXJl"


def _run(root: Path):
    return run_checks(root, scan_repo(root))


def _status(assessments, check):
    return next(a for a in assessments if a.check == check).status


def test_every_check_emits_exactly_one_assessment(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "fastapi\n")
    findings, assessments = _run(tmp_path)
    assert sorted(a.check for a in assessments) == sorted(check.id for check in CHECKS)
    assert check_finding_enums(findings, assessments) == []


def test_committed_credential_becomes_a_critical_finding_linked_to_its_gap(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "fastapi\n")
    _write(tmp_path, "backend/.env", "DB_PASSWORD=hunter2-not-real\n")
    findings, assessments = _run(tmp_path)
    found = [f for f in findings if f.check == "credential.committed"]
    assert len(found) == 1
    assert found[0].severity == "critical"
    assert found[0].related_gaps == ["credential.backend.env.DB_PASSWORD"]
    assert found[0].evidence == ["backend/.env:1"]
    assert "hunter2" not in json.dumps([f.__dict__ for f in findings])
    assert _status(assessments, "credential.committed") == "found"


def test_no_credential_is_clean_not_silent(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "fastapi\n")
    _, assessments = _run(tmp_path)
    assert _status(assessments, "credential.committed") == "clean"


def test_secret_named_build_arg_is_a_high_finding(tmp_path):
    _write(tmp_path, "package.json", '{"dependencies": {"vite": "5"}}')
    _write(tmp_path, "src/ai.ts", "const k = import.meta.env.VITE_OPENAI_API_KEY;\n")
    findings, assessments = _run(tmp_path)
    found = [f for f in findings if f.check == "frontend.secret_in_bundle"]
    assert [f.severity for f in found] == ["high"]
    assert found[0].evidence == ["src/ai.ts:1"]
    assert _status(assessments, "frontend.secret_in_bundle") == "found"


def test_public_by_design_keys_are_not_findings(tmp_path):
    _write(tmp_path, "package.json", '{"dependencies": {"vite": "5"}}')
    _write(tmp_path, "src/f.ts", "\n".join([
        "import.meta.env.VITE_FIREBASE_API_KEY",
        "import.meta.env.VITE_GOOGLE_MAPS_API_KEY",
        "import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY",
        "import.meta.env.VITE_SUPABASE_ANON_KEY",
    ]) + "\n")
    findings, assessments = _run(tmp_path)
    assert [f for f in findings if f.check == "frontend.secret_in_bundle"] == []
    assert _status(assessments, "frontend.secret_in_bundle") == "clean"


def test_a_service_role_jwt_in_frontend_source_is_critical_and_never_echoed(tmp_path):
    token = _jwt({"role": "service_role", "iss": "supabase"})
    _write(tmp_path, "package.json", '{"dependencies": {"vite": "5"}}')
    _write(tmp_path, "src/client.ts", f'const KEY = "{token}";\n')
    findings, _ = _run(tmp_path)
    found = [f for f in findings if f.check == "frontend.secret_in_bundle"]
    assert [f.severity for f in found] == ["critical"]
    assert found[0].evidence == ["src/client.ts:1"]
    assert token not in json.dumps([f.__dict__ for f in findings])


def test_an_anon_jwt_in_frontend_source_is_not_a_finding(tmp_path):
    token = _jwt({"role": "anon", "iss": "supabase"})
    _write(tmp_path, "package.json", '{"dependencies": {"vite": "5"}}')
    _write(tmp_path, "src/client.ts", f'const KEY = "{token}";\n')
    findings, _ = _run(tmp_path)
    assert [f for f in findings if f.check == "frontend.secret_in_bundle"] == []


def test_secret_key_prefix_in_frontend_source_is_critical(tmp_path):
    _write(tmp_path, "package.json", '{"dependencies": {"vite": "5"}}')
    _write(tmp_path, "src/pay.ts", 'const s = "sk_live_' + "0" * 24 + '";\n')
    findings, _ = _run(tmp_path)
    assert [f.severity for f in findings if f.check == "frontend.secret_in_bundle"] == [
        "critical"]


def test_bundle_check_is_not_applicable_without_a_web_service(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "fastapi\n")
    _, assessments = _run(tmp_path)
    assert _status(assessments, "frontend.secret_in_bundle") == "not_applicable"


def test_backend_without_health_route_is_a_low_finding(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "fastapi\n")
    _write(tmp_path, "backend/server.py",
           "from fastapi import FastAPI\napp = FastAPI()\n"
           "@app.get('/items')\ndef items():\n    return []\n")
    findings, assessments = _run(tmp_path)
    assert [(f.id, f.severity) for f in findings if f.check == "service.no_health_endpoint"] == [
        ("service.no_health_endpoint.backend", "low")]
    assert _status(assessments, "service.no_health_endpoint") == "found"


def test_undecidable_routes_are_could_not_assess_not_a_finding():
    fixture = Path("fixtures/emergent-settings-prefix")
    findings, assessments = run_checks(fixture, scan_repo(fixture))
    assert [f for f in findings if f.check == "service.no_health_endpoint"] == []
    assert _status(assessments, "service.no_health_endpoint") == "could_not_assess"


def test_health_check_is_not_applicable_to_a_static_frontend(tmp_path):
    _write(tmp_path, "package.json", '{"dependencies": {"vite": "5"}}')
    _, assessments = _run(tmp_path)
    assert _status(assessments, "service.no_health_endpoint") == "not_applicable"


def test_findings_are_sorted_by_severity_then_id(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "fastapi\n")
    _write(tmp_path, "backend/server.py", "x = 1\n")
    _write(tmp_path, "backend/.env", "API_TOKEN=abc-not-real\nZ_SECRET=def-not-real\n")
    findings, _ = _run(tmp_path)
    keys = [(("critical", "high", "medium", "low").index(f.severity), f.id) for f in findings]
    assert keys == sorted(keys)
