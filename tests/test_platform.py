"""platform.hardcoded_url: what stops working when the application leaves the platform.
The rules are data, each confirmed against the recall corpus (RFC-0002)."""

from pathlib import Path

from checks import run_checks
from scan import scan_repo


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _found(root: Path):
    findings, assessments = run_checks(root, scan_repo(root))
    status = next(a for a in assessments if a.check == "platform.hardcoded_url")
    return {f.id.split(".")[-1]: f for f in findings if f.check == "platform.hardcoded_url"}, status


def test_emergent_couplings(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "fastapi\n")
    _write(tmp_path, "backend/server.py",
           'SESSION = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"\n'
           'STORE = "https://integrations.emergentagent.com/objstore/api/v1/storage"\n')
    _write(tmp_path, "frontend/package.json", '{"dependencies": {"react-scripts": "5"}}')
    _write(tmp_path, "frontend/.env", "REACT_APP_BACKEND_URL=https://shop-12.preview.emergentagent.com\n")
    _write(tmp_path, "frontend/src/Login.js",
           'window.location.href = "https://auth.emergentagent.com/?redirect=" + r;\n')
    _write(tmp_path, "frontend/public/index.html",
           '<script src="https://assets.emergent.sh/scripts/emergent-main.js"></script>\n')
    found, status = _found(tmp_path)
    assert sorted(found) == ["emergent-editor-scripts", "emergent-integrations-proxy",
                             "emergent-managed-auth", "emergent-preview-url"]
    assert found["emergent-managed-auth"].severity == "medium"
    assert found["emergent-managed-auth"].evidence == [
        "backend/server.py:1", "frontend/src/Login.js:1"]
    assert found["emergent-editor-scripts"].severity == "low"
    assert status.status == "found"


def test_lovable_couplings(tmp_path):
    _write(tmp_path, "package.json", '{"dependencies": {"vite": "5"}}')
    _write(tmp_path, "index.html",
           '<script src="https://cdn.gpteng.co/gptengineer.js" type="module"></script>\n'
           '<meta property="og:image" content="https://lovable.dev/opengraph-image-p98pqg.png" />\n')
    _write(tmp_path, "supabase/functions/chat/index.ts",
           'await fetch("https://ai.gateway.lovable.dev/v1/chat/completions", {});\n'
           'await fetch("https://connector-gateway.lovable.dev/resend/emails", {});\n')
    _write(tmp_path, "src/config.ts", 'export const SITE = "https://my-shop.lovable.app";\n')
    found, _ = _found(tmp_path)
    assert sorted(found) == ["lovable-ai-gateway", "lovable-app-url", "lovable-connector-gateway",
                             "lovable-default-social-image", "lovable-editor-script"]


def test_tests_docs_and_lockfiles_are_not_findings(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "fastapi\n")
    _write(tmp_path, "backend_test.py", 'BASE = "https://x-1.preview.emergentagent.com/api"\n')
    _write(tmp_path, "tests/test_api.py", 'BASE = "https://x-1.preview.emergentagent.com/api"\n')
    _write(tmp_path, "README.md", "Edit at https://lovable.dev/projects/abc\n")
    found, status = _found(tmp_path)
    assert found == {}
    assert status.status == "clean"


def test_a_plain_link_to_the_platform_is_not_coupling(tmp_path):
    _write(tmp_path, "package.json", '{"dependencies": {"vite": "5"}}')
    _write(tmp_path, "src/Footer.tsx", '<a href="https://lovable.dev">Built with Lovable</a>\n')
    found, _ = _found(tmp_path)
    assert found == {}
