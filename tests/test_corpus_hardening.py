"""Behaviour fixed after running audit over the recall corpus (RFC-0002, Testing: "the
false-positive rate on a hand-labelled sample"). Each test names the corpus shape that
produced a wrong answer; the corpus itself is referenced, never copied."""

import base64
import json
from pathlib import Path

from checks import run_checks
from detect.migrations import replay
from scan import scan_repo
from walk import read_source


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _jwt(role: str) -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'role': role, 'iss': 'supabase'})}.c2lnbmF0dXJl"


def _findings(root: Path, check: str):
    findings, assessments = run_checks(root, scan_repo(root))
    status = next(a for a in assessments if a.check == check)
    return [f for f in findings if f.check == check], status


def _vite(root: Path) -> None:
    _write(root, "package.json", '{"dependencies": {"vite": "5"}}')
    _write(root, "src/main.ts", "export const x = 1;\n")


# A UTF-16 requirements.txt crashed the whole audit.
def test_utf16_files_are_read_not_fatal(tmp_path):
    path = tmp_path / "backend" / "requirements.txt"
    path.parent.mkdir()
    path.write_bytes("fastapi==0.110\nmotor\n".encode("utf-16"))
    assert read_source(path).splitlines() == ["fastapi==0.110", "motor"]
    scan_repo(tmp_path)  # must not raise


def test_undecodable_bytes_do_not_raise(tmp_path):
    path = tmp_path / "x.py"
    path.write_bytes(b"import os\n\xff\xfe\xfa = 1\n")
    assert "import os" in read_source(path)


# Supabase applies only <timestamp>_name.sql; the corpus had hand-named files whose
# run order is unknowable, and a DISABLE in one of them decided the answer.
def test_untimestamped_migrations_make_the_order_unknown(tmp_path):
    _vite(tmp_path)
    _write(tmp_path, "src/c.ts", "import { createClient } from '@supabase/supabase-js';\n")
    _write(tmp_path, "supabase/migrations/20260101000000_init.sql", "create table t (id int);")
    _write(tmp_path, "supabase/migrations/revert_rls.sql",
           "alter table t disable row level security;")
    found, status = _findings(tmp_path, "supabase.rls.disabled")
    assert found == []
    assert status.status == "could_not_assess"
    assert "revert_rls.sql" in status.reason


def test_timestamped_migrations_are_all_replayed(tmp_path):
    # Lovable's own naming: <version>-<uuid>.sql
    _write(tmp_path, "supabase/migrations/20260101000000-2f1c9a4e-0b7d-4c1a-9e55-6c1d0f3b2a10.sql",
           "create table t (id int);")
    _write(tmp_path, "supabase/migrations/20260102000000_b.sql",
           "alter table t enable row level security;")
    assert replay(tmp_path).tables["public.t"].rls is True


# A service_role key in a Supabase edge function is committed, and critical -- but it is
# server-side code and is not "shipped to every browser".
def test_secret_in_an_edge_function_is_committed_not_in_the_bundle(tmp_path):
    _vite(tmp_path)
    _write(tmp_path, "supabase/functions/admin/index.ts", f'const k = "{_jwt("service_role")}";\n')
    bundle, _ = _findings(tmp_path, "frontend.secret_in_bundle")
    committed, _ = _findings(tmp_path, "credential.committed")
    assert bundle == []
    assert [f.severity for f in committed] == ["critical"]
    assert committed[0].evidence == ["supabase/functions/admin/index.ts:1"]


def test_secret_in_a_node_script_is_committed_not_in_the_bundle(tmp_path):
    _vite(tmp_path)
    _write(tmp_path, "scripts/seed.ts", f'const k = "{_jwt("service_role")}";\n')
    bundle, _ = _findings(tmp_path, "frontend.secret_in_bundle")
    committed, _ = _findings(tmp_path, "credential.committed")
    assert bundle == []
    assert len(committed) == 1


def test_secret_in_src_is_bundle_only_not_reported_twice(tmp_path):
    _vite(tmp_path)
    _write(tmp_path, "src/admin.ts", f'const k = "{_jwt("service_role")}";\n')
    bundle, _ = _findings(tmp_path, "frontend.secret_in_bundle")
    committed, _ = _findings(tmp_path, "credential.committed")
    assert len(bundle) == 1
    assert committed == []


# Committed .env keys whose value is a placeholder, a duration or a public key.
def test_placeholders_and_non_secret_values_are_not_committed_credentials(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "fastapi\n")
    _write(tmp_path, "backend/.env", "\n".join([
        "OPENAI_API_KEY=your_openai_api_key_here",
        "JWT_SECRET=<generate-a-secret>",
        "TOKEN_EXPIRY=24h",
        "SESSION_TOKEN_TTL=3600",
        "VITE_COPILOTKIT_PUBLIC_API_KEY=ck_pub_0123456789abcdef",
        "STRIPE_SECRET_KEY=sk_live_" + "a" * 24,
    ]) + "\n")
    found, _ = _findings(tmp_path, "credential.committed")
    assert [f.id.rsplit(".", 1)[-1] for f in found] == ["STRIPE_SECRET_KEY"]


def test_placeholder_connection_string_is_not_a_credential(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "motor\n")
    _write(tmp_path, "backend/db.py",
           'URL = "mongodb+srv://<username>:<password>@cluster0.example.net/app"\n')
    found, _ = _findings(tmp_path, "credential.committed")
    assert found == []


# Anonymous INSERT with `true` is usually a contact or waitlist form. It is worth asking
# about, not the same risk as anyone being able to change or delete every row.
def test_anon_insert_true_is_medium_but_update_true_is_high(tmp_path):
    _vite(tmp_path)
    _write(tmp_path, "src/c.ts", "import { createClient } from '@supabase/supabase-js';\n")
    _write(tmp_path, "supabase/migrations/20260101000000_init.sql", (
        "create table leads (e text); alter table leads enable row level security;"
        "create policy \"Anyone can submit\" on leads for insert to anon with check (true);"
        "create table posts (t text); alter table posts enable row level security;"
        "create policy \"Anyone can edit\" on posts for update using (true);"
    ))
    found, _ = _findings(tmp_path, "supabase.rls.permissive_write")
    assert sorted((f.id.split(".")[-2], f.severity) for f in found) == [
        ("leads", "medium"), ("posts", "high")]


def test_create_policy_if_not_exists_is_reported_as_invalid_sql(tmp_path):
    _write(tmp_path, "supabase/migrations/20260101000000_p.sql",
           'create table t (id int); create policy if not exists "p" on t using (true);')
    [reason] = replay(tmp_path).unparseable
    assert "not valid PostgreSQL" in reason


# Second corpus pass: root-level node scripts, server-only and declaration files were
# being counted as frontend code.
def test_root_level_scripts_are_not_in_the_bundle(tmp_path):
    _vite(tmp_path)
    _write(tmp_path, "migrate-data.mjs", f'const k = "{_jwt("service_role")}";\n')
    bundle, _ = _findings(tmp_path, "frontend.secret_in_bundle")
    committed, _ = _findings(tmp_path, "credential.committed")
    assert bundle == []
    assert [f.evidence for f in committed] == [["migrate-data.mjs:1"]]


def test_server_only_and_declaration_files_are_not_in_the_bundle(tmp_path):
    _vite(tmp_path)
    _write(tmp_path, "src/integrations/supabase/client.server.ts",
           f'const k = "{_jwt("service_role")}";\n'
           "const url = import.meta.env.VITE_SUPABASE_SERVICE_ROLE_KEY;\n")
    _write(tmp_path, "src/env.d.ts", "interface E { readonly VITE_API_KEY: string }\n")
    bundle, _ = _findings(tmp_path, "frontend.secret_in_bundle")
    assert bundle == []


def test_a_secret_named_variable_read_only_by_a_server_file_is_not_a_bundle_finding(tmp_path):
    _vite(tmp_path)
    _write(tmp_path, "api-server.js", "const k = process.env.VITE_OPENAI_API_KEY;\n")
    bundle, _ = _findings(tmp_path, "frontend.secret_in_bundle")
    assert bundle == []


def test_kakao_map_key_is_public_by_design(tmp_path):
    _vite(tmp_path)
    _write(tmp_path, "src/map.ts", "const k = import.meta.env.VITE_KAKAO_API_KEY;\n")
    bundle, _ = _findings(tmp_path, "frontend.secret_in_bundle")
    assert bundle == []


def test_env_example_files_are_templates_not_credentials(tmp_path):
    _write(tmp_path, "backend/requirements.txt", "fastapi\n")
    _write(tmp_path, "backend/.env.example", "API_TOKEN_SALT=tobemodified\n")
    found, _ = _findings(tmp_path, "credential.committed")
    assert found == []


def test_documentation_example_keys_are_not_secrets(tmp_path):
    _vite(tmp_path)
    _write(tmp_path, "src/docs.ts",
           '// Instead of `const apiKey = "sk_live_abcdef"`, read it from the environment.\n'
           # Built at runtime so the repository never holds a key-shaped literal.
           'const example = "' + "sk_live_" + "x" * 24 + '";\n')
    bundle, _ = _findings(tmp_path, "frontend.secret_in_bundle")
    assert bundle == []
