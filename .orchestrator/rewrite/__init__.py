"""Parallel rewrite package for the orchestrator supervisor.

New architecture from docs/02-architecture/SUPERVISOR_REWRITE_PLAN.md is built
here in isolation. Nothing in this package is imported by the live supervisor;
each module is proven behaviour-equivalent to the incumbent via a shadow
validator (read real config/state, compute the new decision, diff against the
old one) BEFORE any cutover, one phase at a time behind a flag.
"""
