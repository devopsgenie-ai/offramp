import subprocess
from pathlib import Path

import pytest
import yaml

FIXTURE = Path("fixtures/emergent-fastapi-mongo")

REQUIRED = [
    "backend/.env",
    "backend/server.py",
    "backend/routes/health.py",
    "backend/routes/items.py",
    "backend/services/db.py",
    "backend/security/auth.py",
    "backend/requirements.txt",
    "frontend/.env",
    "frontend/package.json",
    "frontend/Dockerfile",
    "frontend/src/App.js",
    "frontend/node_modules/leftpad/.env",
    ".emergent/emergent.yml",
    ".gitconfig",
    "truth.yaml",
]


@pytest.mark.parametrize("relative", REQUIRED)
def test_required_fixture_file_exists(relative):
    assert (FIXTURE / relative).is_file()


@pytest.mark.parametrize("relative", REQUIRED)
def test_required_fixture_file_is_tracked_by_git(relative):
    """.gitignore ignores .env, and the fixture's own .gitignore ignores node_modules.
    Both have to be worked around or CI runs without the files that matter."""
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(FIXTURE / relative)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"{relative} is not tracked: {result.stderr}"


def test_routes_and_env_reads_are_split_across_modules():
    """The RFC's structural requirement, asserted rather than assumed."""
    server = (FIXTURE / "backend/server.py").read_text()
    assert "@app.get" not in server and "@api_router.get" not in server
    assert "os.environ" not in server
    assert "@router.get" in (FIXTURE / "backend/routes/health.py").read_text()
    assert "os.environ" in (FIXTURE / "backend/services/db.py").read_text()


def test_truth_yaml_parses_and_names_the_fixture_app():
    truth = yaml.safe_load((FIXTURE / "truth.yaml").read_text())
    assert truth["app"]["name"] == "widgets"
    assert {s["name"] for s in truth["services"]} == {"backend", "frontend"}


def test_no_fixture_file_contains_a_resolvable_hostname():
    """Every invented host is under .invalid. This is the no-real-hostname rule."""
    for path in FIXTURE.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        for marker in ("emergent.sh", "amazonaws.com", "mongodb.net", "supabase.co"):
            assert marker not in text, f"{path} mentions {marker}"
