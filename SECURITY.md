# Security policy

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report privately through GitHub's
[private vulnerability reporting](https://github.com/devopsgenie-ai/offramp/security/advisories/new).
That opens a draft advisory visible only to you and the maintainers.

Please include:

- what an attacker can do, and what they need in order to do it
- the smallest reproduction you can manage — ideally a fixture repository
- the commit or version you tested against

You will get an acknowledgement within **3 working days** and an assessment within
**10 working days**. If a fix is warranted we will agree a disclosure date with you and
credit you in the advisory unless you ask us not to.

## Supported versions

`offramp` is pre-1.0 and has no tagged releases. Only the `main` branch is supported.
Once releases exist this table will name the supported minor versions.

| Version | Supported |
|---|---|
| `main` | yes |
| anything else | no |

## What counts as a vulnerability here

`offramp` reads repositories that contain real credentials and writes deployment
configuration. Its threat model follows from that, and from the constraints in
[AGENTS.md](AGENTS.md).

Treat the following as security issues, not ordinary bugs:

- **Secret leakage.** Any path where a credential *value* read from a source repository
  reaches generated output, a fixture, a test, a log line, or an error message. The tool
  is required to emit a reference instead — see AGENTS.md, "Never emit secrets".
- **Escaping the declarative constraint.** Any path where the tool mutates real
  infrastructure, calls a cloud provider to create or change a resource, or requires
  cloud credentials to run. `offramp` authors configuration; it never applies it.
- **Code execution from an untrusted source repository.** The tool is designed to be run
  against a repository you do not own. Detector or renderer input that leads to command
  execution, template injection, or a path traversal outside the output tree is a
  vulnerability.
- **Generated configuration that is insecure by default** in a way a reviewer would not
  reasonably catch — for example emitting a publicly readable bucket, an over-broad IAM
  policy, or a container running as root without recording it as a gap.

## What does not count

- Missing hardening in a *fixture* app. Fixtures are deliberately imperfect inputs.
- A gap the tool correctly reported. Recording "I could not determine this" is the
  designed behaviour, not a failure.
- Vulnerabilities in a source repository that `offramp` merely read and reported on.
- Findings from an automated scanner with no demonstrated impact. Show us the path.

## Scope

This policy covers the contents of this repository. It does not cover the DevOps Genie
hosted platform or any deployment produced by running the tool.
