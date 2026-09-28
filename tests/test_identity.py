from pathlib import Path

import pytest
from detect.identity import detect_identity, detect_platform, name_from_remote

FIXTURE = Path("fixtures/emergent-fastapi-mongo")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/example-org/widgets.git", "widgets"),
        ("https://github.com/example-org/widgets", "widgets"),
        ("git@github.com:example-org/My_Widgets.git", "my-widgets"),
        ("ssh://git@host.invalid/org/sub/widgets.git", "widgets"),
    ],
)
def test_name_from_remote(url, expected):
    assert name_from_remote(url) == expected


def test_detect_identity_reads_the_fixture_gitconfig_fallback():
    name, repo_url, commit, evidence = detect_identity(FIXTURE)
    assert name == "widgets"
    assert repo_url == "https://github.com/example-org/widgets.git"
    assert commit is None
    assert [item.path for item in evidence] == [".gitconfig"]


def test_detect_identity_prefers_a_real_git_config(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/example-org/real.git\n'
    )
    (tmp_path / ".gitconfig").write_text(
        '[remote "origin"]\n\turl = https://github.com/example-org/fallback.git\n'
    )
    name, _, _, evidence = detect_identity(tmp_path)
    assert name == "real"
    assert evidence[0].path == ".git/config"


def test_detect_identity_reads_head_commit_when_present(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/example-org/real.git\n'
    )
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    refs = tmp_path / ".git" / "refs" / "heads"
    refs.mkdir(parents=True)
    (refs / "main").write_text("0123456789abcdef0123456789abcdef01234567\n")
    _, _, commit, _ = detect_identity(tmp_path)
    assert commit == "0123456789abcdef0123456789abcdef01234567"


def test_detect_identity_returns_none_rather_than_the_directory_name(tmp_path):
    """The one rule this function exists to enforce."""
    named = tmp_path / "some-checkout-directory"
    named.mkdir()
    name, repo_url, commit, evidence = detect_identity(named)
    assert name is None
    assert repo_url is None
    assert evidence == []


def test_detect_platform_recognises_emergent():
    platform, evidence = detect_platform(FIXTURE)
    assert platform == "emergent"
    assert evidence[0].path == ".emergent/emergent.yml"


def test_detect_platform_is_unknown_without_a_marker(tmp_path):
    platform, evidence = detect_platform(tmp_path)
    assert platform == "unknown"
    assert evidence == []
