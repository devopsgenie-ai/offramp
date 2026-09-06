# Contributing

Thanks for considering it. This project is early — the architecture is still in review,
which makes it a good moment to disagree with it cheaply.

## Read these first

- [AGENTS.md](AGENTS.md) — the operating contract. Applies to humans and coding agents
  alike, and it is short.
- [rfcs/README.md](rfcs/README.md) — how design decisions get made.

## The short version

Design happens before implementation, in public, in an RFC. Nothing lands in `src/`,
`schemas/` or `templates/` without an accepted RFC authorising it, and CI enforces that.

This is deliberately stricter than most projects. The reason is that the tool generates
configuration people will run against their own infrastructure, and once it is generating
output, changing what it emits breaks users who already ran it. It is much cheaper to
argue about the shape of the output before it exists.

## The most useful thing you can do right now

Comment on [RFC-0001](rfcs/0001-architecture.md). Specifically:

- Is the AppSpec missing a field your application would need?
- Is there a source platform whose shape breaks the model?
- The three open questions at the end are genuinely open.

## Making a change

1. Open an issue or a draft RFC first for anything substantial. For a typo, just send the
   PR.
2. Fork, branch, and make the change.
3. Run `make check`.
4. Open a pull request. The template asks which RFC authorises it.

## Fixtures and the recall corpus

These are two different things and the rules differ.

**Fixtures are checked in.** They must be **synthetic, or code you have the right to
redistribute under MIT** — in practice, an app you generated yourself on the platform's free
tier and then scrubbed. Do not contribute a fixture derived from a private or customer
repository, and never include a real credential, hostname or account identifier — not even an
expired one. Note that these platforms frequently commit live `.env` files, so assume your own
export contains one until you have checked.

**The recall corpus is referenced, never copied.** Detector hit rates are measured against
public repositories that stay where they are. Contribute a query or an identifier, never the
code — most of these repositories carry no licence, and an unlicensed repository is not
redistributable regardless of how public it is.

## Reporting something sensitive

If you find a security issue, or a case where the tool copies a secret into its output,
do not open a public issue. Email security@devopsgenie.ai.

## Conduct

Be straightforward and assume good faith. Argue about the work, not the person. Maintainers
will remove comments and contributors that make this an unpleasant place to be.
