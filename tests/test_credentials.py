from pathlib import Path

from detect.credentials import detect_committed_credentials

FIXTURE = Path("fixtures/emergent-fastapi-mongo")


def test_reports_both_committed_credentials_in_the_backend_env():
    gaps, _ = detect_committed_credentials(FIXTURE)
    assert sorted(gap.id for gap in gaps) == [
        "credential.backend.env.JWT_SECRET",
        "credential.backend.env.MONGO_URL",
    ]


def test_every_credential_gap_is_blocking_and_an_action():
    gaps, _ = detect_committed_credentials(FIXTURE)
    for gap in gaps:
        assert gap.severity == "blocking"
        assert gap.kind == "action"
        assert gap.pointers == []


def test_the_gap_says_rotate_before_cutover_and_names_key_and_file():
    gaps, _ = detect_committed_credentials(FIXTURE)
    gap = next(g for g in gaps if g.id.endswith("MONGO_URL"))
    assert "rotate" in gap.question.lower()
    assert "before cutover" in gap.question.lower()
    assert "MONGO_URL" in gap.question
    assert "backend/.env" in gap.question


def test_the_credential_value_appears_nowhere_in_the_gap():
    """AGENTS.md §4. The one assertion that must never be relaxed."""
    gaps, evidence = detect_committed_credentials(FIXTURE)
    secrets = ("not-a-real-password", "not-a-real-signing-key")
    for gap in gaps:
        blob = f"{gap.id}{gap.question}{gap.proposed}{gap.evidence}"
        for secret in secrets:
            assert secret not in blob
    for item in evidence:
        for secret in secrets:
            assert secret not in item.path


def test_a_dependency_example_env_is_never_reported():
    """frontend/node_modules/leftpad/.env holds API_KEY=... and is not the user's."""
    gaps, _ = detect_committed_credentials(FIXTURE)
    assert not [g for g in gaps if "leftpad" in g.id or "leftpad" in str(g.evidence)]


def test_a_harmless_key_is_not_a_credential():
    gaps, _ = detect_committed_credentials(FIXTURE)
    assert not [g for g in gaps if g.id.endswith("DB_NAME")]


def test_evidence_cites_the_exact_line():
    _, evidence = detect_committed_credentials(FIXTURE)
    mongo = next(item for item in evidence if item.path == "backend/.env")
    assert mongo.line >= 1


def test_a_credential_url_in_source_is_reported(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "db.py").write_text(
        "URL = 'postgres://user:hunter2@db.invalid:5432/app'\n"
    )
    gaps, _ = detect_committed_credentials(tmp_path)
    gap = next(g for g in gaps if g.id == "credential.app.db.py.literal")
    assert "app/db.py" in gap.question
    assert "hunter2" not in gap.question


def test_gaps_are_sorted_deterministically():
    first, _ = detect_committed_credentials(FIXTURE)
    second, _ = detect_committed_credentials(FIXTURE)
    assert [g.id for g in first] == [g.id for g in second]
    assert [g.id for g in first] == sorted(g.id for g in first)
