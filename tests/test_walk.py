import pytest
from pathlib import Path
from walk import EXCLUDED_DIRS, dns_label, rel, walk_files


@pytest.fixture
def tree(tmp_path):
    """A repository with one real file per excluded directory name, at two depths."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "server.py").write_text("x = 1\n")
    (tmp_path / "backend" / ".env").write_text("REAL=yes\n")
    for name in sorted(EXCLUDED_DIRS):
        top = tmp_path / name / "pkg"
        top.mkdir(parents=True)
        (top / ".env").write_text("DECOY=no\n")
        (top / "mod.py").write_text("y = 2\n")
        nested = tmp_path / "frontend" / name / "pkg"
        nested.mkdir(parents=True)
        (nested / ".env").write_text("DECOY=no\n")
    (tmp_path / "frontend" / "package.json").write_text("{}\n")
    return tmp_path


def test_walk_yields_real_files(tree):
    found = {rel(tree, path) for path in walk_files(tree)}
    assert "backend/server.py" in found
    assert "backend/.env" in found
    assert "frontend/package.json" in found


def test_walk_prunes_every_excluded_directory_at_top_level(tree):
    found = {rel(tree, path) for path in walk_files(tree)}
    for name in EXCLUDED_DIRS:
        assert not any(path.startswith(f"{name}/") for path in found), name


def test_walk_prunes_excluded_directories_at_depth(tree):
    """The dependency-example-.env case: node_modules is not always at the root."""
    found = {rel(tree, path) for path in walk_files(tree)}
    for name in EXCLUDED_DIRS:
        assert f"frontend/{name}/pkg/.env" not in found, name


def test_excluded_dirs_covers_the_set_the_rfc_names():
    assert EXCLUDED_DIRS == frozenset(
        {".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist"}
    )


def test_walk_is_deterministic(tree):
    assert [rel(tree, p) for p in walk_files(tree)] == [
        rel(tree, p) for p in walk_files(tree)
    ]


def test_walk_filters_by_suffix(tree):
    found = {rel(tree, path) for path in walk_files(tree, suffixes=(".py",))}
    assert found == {"backend/server.py"}


def test_walk_skips_symlinks_rather_than_following_them(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "a.py").write_text("a = 1\n")
    (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
    found = {rel(tmp_path, path) for path in walk_files(tmp_path)}
    assert found == {"real/a.py"}


def test_rel_is_posix_separated(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.py").write_text("")
    assert rel(tmp_path, tmp_path / "a" / "b.py") == "a/b.py"


@pytest.mark.parametrize(
    "raw,expected",
    [("backend", "backend"), ("My_Service", "my-service"), ("api.v2", "api-v2"),
     ("--weird--", "weird")],
)
def test_dns_label(raw, expected):
    assert dns_label(raw) == expected
