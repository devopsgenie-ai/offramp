from pathlib import Path

from detect.env import detect_env, is_sensitive, parse_env_file

FIXTURE = Path("fixtures/emergent-fastapi-mongo")


def names(env):
    return sorted(var.name for var in env)


def one(env, name):
    return next(var for var in env if var.name == name)


def test_finds_env_reads_in_every_module_not_just_the_entrypoint():
    """server.py contains no os.environ at all. All four keys live deeper."""
    env, _, _ = detect_env(FIXTURE, FIXTURE / "backend", "backend")
    assert names(env) == ["DB_NAME", "ITEMS_PAGE_SIZE", "JWT_SECRET", "MONGO_URL"]


def test_a_credential_url_is_sensitive_and_its_value_is_never_carried():
    env, _, _ = detect_env(FIXTURE, FIXTURE / "backend", "backend")
    mongo = one(env, "MONGO_URL")
    assert mongo.sensitive is True
    assert mongo.value is None


def test_a_secret_by_name_is_sensitive_and_its_value_is_never_carried():
    env, _, _ = detect_env(FIXTURE, FIXTURE / "backend", "backend")
    assert one(env, "JWT_SECRET").sensitive is True
    assert one(env, "JWT_SECRET").value is None


def test_a_harmless_value_is_carried():
    env, _, _ = detect_env(FIXTURE, FIXTURE / "backend", "backend")
    assert one(env, "DB_NAME").value == "widgets"
    assert one(env, "DB_NAME").sensitive is False


def test_a_code_default_is_a_literal_value():
    env, _, _ = detect_env(FIXTURE, FIXTURE / "backend", "backend")
    page_size = one(env, "ITEMS_PAGE_SIZE")
    assert page_size.value == "20"
    assert page_size.source == "literal"


def test_a_key_with_no_value_anywhere_is_platform_provenance(tmp_path):
    service = tmp_path / "backend"
    service.mkdir()
    (service / "requirements.txt").write_text("fastapi==0.110.0\n")
    (service / "app.py").write_text("import os\nX = os.environ['INJECTED_BY_PLATFORM']\n")
    env, _, _ = detect_env(tmp_path, service, "backend")
    assert one(env, "INJECTED_BY_PLATFORM").source == "platform"
    assert one(env, "INJECTED_BY_PLATFORM").value is None


def test_backend_env_is_all_runtime_binding():
    env, _, _ = detect_env(FIXTURE, FIXTURE / "backend", "backend")
    assert {var.binding for var in env} == {"runtime"}


def test_a_react_prefixed_key_is_a_build_arg():
    env, _, _ = detect_env(FIXTURE, FIXTURE / "frontend", "frontend")
    var = one(env, "REACT_APP_BACKEND_URL")
    assert var.binding == "build_arg"
    assert var.sensitive is False


def test_dev_only_keys_never_reach_the_appspec():
    env, _, _ = detect_env(FIXTURE, FIXTURE / "frontend", "frontend")
    assert "WDS_SOCKET_PORT" not in names(env)


def test_a_committed_key_no_module_reads_is_a_gap_with_no_pointer():
    _, gaps, _ = detect_env(FIXTURE, FIXTURE / "backend", "backend")
    gap = next(g for g in gaps if g.id == "service.backend.env.UNUSED_LEGACY_FLAG")
    assert gap.pointers == []
    assert gap.severity == "cosmetic"


def test_a_secret_shaped_build_arg_is_reported_as_already_public(tmp_path):
    service = tmp_path / "frontend"
    (service / "src").mkdir(parents=True)
    (service / "package.json").write_text('{"name":"f","scripts":{"build":"vite build"}}')
    (service / "src" / "main.js").write_text("const k = process.env.VITE_API_TOKEN;\n")
    (service / ".env").write_text("VITE_API_TOKEN=not-a-real-token\n")
    env, gaps, _ = detect_env(tmp_path, service, "frontend")
    var = one(env, "VITE_API_TOKEN")
    assert var.binding == "build_arg"
    assert var.sensitive is False
    gap = next(g for g in gaps if g.id == "service.frontend.env.VITE_API_TOKEN.public")
    assert "already public" in gap.question
    assert "rotate" not in gap.question.lower()


def test_env_is_sorted_by_name():
    env, _, _ = detect_env(FIXTURE, FIXTURE / "backend", "backend")
    assert [var.name for var in env] == sorted(var.name for var in env)


def test_parse_env_file_skips_comments_and_blanks(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# a comment\n\nA=1\nexport B=2\nC=\n")
    assert parse_env_file(path) == [("A", "1", 3), ("B", "2", 4), ("C", "", 5)]


def test_is_sensitive_rules():
    assert is_sensitive("JWT_SECRET", "x") is True
    assert is_sensitive("API_KEY", "x") is True
    assert is_sensitive("MONGO_URL", "mongodb://u:p@h.invalid/db") is True
    assert is_sensitive("MONGO_URL", "mongodb://h.invalid/db") is False
    assert is_sensitive("DB_NAME", "widgets") is False
