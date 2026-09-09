# Governance

This document describes how decisions are made in Jina Connect and how contributors can grow into maintainers.

## Project Stewardship

Jina Connect is maintained by **Jina Code Systems LLP**. The project uses a **Benevolent Dictator For Life (BDFL)** model — the maintainer team at Jina Code Systems has final say on what gets merged, what gets prioritised, and where the project goes.

This is the same model used by Linux (Linus Torvalds), Python (formerly Guido), and Grafana (Grafana Labs). It works well for company-backed open-source projects because it keeps decision-making clear and fast.

## How Decisions Are Made

| Decision Type | Who Decides | Process |
|---|---|---|
| Bug fix PRs | Any maintainer | Standard code review |
| New features / enhancements | Maintainer team | PR review + discuss if non-trivial |
| New Django apps or adapters | BDFL / maintainer lead | Proposal in Discussions first |
| Architecture changes | BDFL / maintainer lead | RFC in Discussions, community input welcome |
| Breaking changes | BDFL / maintainer lead | Discussed openly, announced in advance |
| Releases | Maintainer team | Semantic versioning, changelog maintained |
| License changes | BDFL | Would require public notice and rationale |

## Contributor Path

1. **Contributor** — Anyone who opens issues, submits PRs, or participates in discussions. No special access.
2. **Trusted Contributor** — Consistent, high-quality contributions over time. May be given triage access (labelling issues, reviewing PRs).
3. **Maintainer** — Invited by existing maintainers. Has merge access and participates in roadmap discussions.

There is no formal application process. If your contributions are consistently solid, we'll reach out.

## Commercial Relationship

Jina Code Systems operates a hosted version of this software and licenses white-labelled deployments to partners. This repository is the backend platform — the API, channel adapters, flows, and MCP server — and the hosted product builds on it.

Some things are not in this repository and are not planned to be: the web dashboard, partner provisioning and white-label tooling, and partner billing. That is how the company funds the work on the open core.

Features developed commercially that make sense in the core get contributed back under AGPL-3.0. Recent examples are the branding settings and the availability reporting. Where a feature exists only to serve a commercial arrangement, it stays out.

The company's commercial interests do not override community contributions — if you submit a good PR, it gets merged regardless of whether the feature competes with a paid offering.

## Fork Rights

This project is licensed under **AGPL-3.0**. You have the right to fork, modify, and redistribute under the same license. If you disagree with the project's direction, forking is always an option — that's the point of open source.

## Code of Conduct

All participants are expected to behave professionally. We don't have a separate Code of Conduct document yet — the short version: be respectful, assume good intent, keep discussions constructive. We'll formalise this if the community grows to need it.

## Questions About Governance

Open a [discussion](../../discussions). We're transparent about how and why decisions are made.
