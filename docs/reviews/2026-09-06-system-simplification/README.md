# System simplification and OSS audit — 2026-09-06

This bundle records a source-based review of Pantheon and the separate
execute-plans frontend, plus official upstream dependency metadata. It is a
planning/review record, not canonical architecture, deployment acceptance, or
approval to remove functionality.

Start with [the Traditional Chinese report](REPORT.zh-TW.md). It contains the
prioritized simplification candidates, deletion conditions, version highlights,
rollout order, and links to the detailed evidence.

## Evidence

- [Architecture and development tooling](architecture-findings.md)
- [LLM, research, ingestion, search, and memory](llm-research-findings.md)
- [OSS findings and all Python package versions](oss-findings.md)
- [94 frontend direct dependencies](frontend-dependencies.csv)
- [195 backend dependency declarations](oss-requirements-current-dev.csv)
- [44 latest stable Python package versions](oss-pypi-latest-current-dev.csv)
- [Docker image declarations](oss-images-current-dev.csv)
- [Inline installation declarations](oss-inline-installs-current-dev.csv)
- [Audit baseline and methodology](audit-baseline.json)
- [Backend inventory counts](oss-inventory-summary.json)
- [PyPI metadata observations](oss-pypi-latest-current-dev.json)
- [BFF exact AST duplicates](bff-exact-duplicates.json)

Backend source: `471dc5391a0f9cbde54d51730891583043708e42`.
Frontend source: `5d4f385284b44a30e10764426a47fd808a7ae3cb`.
All version observations are dated 2026-09-06. They do not establish installed
or deployed versions, vulnerability status, or behavioral equivalence after
an upgrade. The bundle contains no frontend source tree, runtime task-state
snapshot, credentials, or executed simplification changes.

The read-only audit preceded this documentation publication. Statements about
no repository writes in its evidence describe that audit phase. This bundle's
Git history records its later publication; it does not change the audit bases.
