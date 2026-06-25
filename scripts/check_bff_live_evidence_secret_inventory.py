#!/usr/bin/env python3
"""Check GitHub secret metadata readiness for strict BFF live evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from write_bff_live_evidence_preflight import REQUIRED_SECRET_ENV_VARS


DEFAULT_REPOSITORY = "ajoe734/pantheon"
DEFAULT_ENVIRONMENTS = ("dev", "staging-live")
DEFAULT_BFF_BASE_URLS = {
    "dev": "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io",
    "staging-live": "https://pantheon-lupin-staging-bff.104.155.223.192.sslip.io",
}


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"metadata JSON not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"metadata JSON is invalid: {path}: {exc.msg}") from exc


def secret_names_from_metadata(payload: Any) -> list[str]:
    if isinstance(payload, list):
        source = payload
    elif isinstance(payload, dict):
        source = payload.get("secrets") or payload.get("names") or []
    else:
        source = []

    names: list[str] = []
    for item in source:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = ""
        if name:
            names.append(name)
    return sorted(set(names))


def gh_api_json(path: str) -> Any:
    result = subprocess.run(["gh", "api", path], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"gh api failed for {path}: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"gh api returned invalid JSON for {path}: {exc.msg}") from exc


def parse_env_file_specs(specs: list[str]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for spec in specs:
        if "=" not in spec:
            raise SystemExit("--environment-secrets-json must use ENV=PATH")
        env, raw_path = spec.split("=", 1)
        env = env.strip()
        if not env:
            raise SystemExit("--environment-secrets-json ENV cannot be blank")
        files[env] = Path(raw_path)
    return files


def setup_commands(repo: str, environment: str) -> list[str]:
    return [
        f"gh secret set {name} --repo {repo} --env {environment} < /secure/path/{name}.txt"
        for name in REQUIRED_SECRET_ENV_VARS
    ]


def dispatch_template(repo: str, environment: str) -> str:
    base_url = DEFAULT_BFF_BASE_URLS.get(environment, "<bff-base-url>")
    return (
        f"gh workflow run \"Pantheon Stage 0 CI\" --repo {repo} --ref dev "
        f"-f mode=live-evidence -f environment={environment} "
        f"-f bff_base_url={base_url} "
        "-f approval_race_id=<expendable-approval-id> "
        "-f two_man_race_id=<expendable-intervention-id> "
        "-f soak_seconds=75"
    )


def load_environment_secret_names(repo: str, environment: str, fixture_files: dict[str, Path]) -> list[str]:
    if environment in fixture_files:
        return secret_names_from_metadata(read_json(fixture_files[environment]))
    payload = gh_api_json(f"repos/{repo}/environments/{environment}/secrets")
    return secret_names_from_metadata(payload)


def load_repo_secret_names(repo: str, fixture_file: Path | None, skip: bool) -> list[str]:
    if skip:
        return []
    if fixture_file:
        return secret_names_from_metadata(read_json(fixture_file))
    payload = gh_api_json(f"repos/{repo}/actions/secrets")
    return secret_names_from_metadata(payload)


def evaluate(
    repo: str,
    environments: list[str],
    env_fixture_files: dict[str, Path],
    repo_fixture_file: Path | None,
    skip_repo_scope: bool,
) -> dict[str, Any]:
    required = list(REQUIRED_SECRET_ENV_VARS)
    repo_secret_names = load_repo_secret_names(repo, repo_fixture_file, skip_repo_scope)
    repo_required = [name for name in required if name in repo_secret_names]
    env_results: dict[str, Any] = {}

    for environment in environments:
        names = load_environment_secret_names(repo, environment, env_fixture_files)
        present = [name for name in required if name in names]
        missing = [name for name in required if name not in names]
        repo_scope_only = [name for name in repo_required if name in missing]
        env_results[environment] = {
            "ready": not missing,
            "present_secret_names": present,
            "missing_secret_names": missing,
            "repo_scope_only_secret_names": repo_scope_only,
            "secret_set_commands": setup_commands(repo, environment),
            "workflow_dispatch_template": dispatch_template(repo, environment),
        }

    overall = "pass" if all(item["ready"] for item in env_results.values()) else "fail"
    return {
        "task_id": "BFF-LIVE-EVIDENCE-SECRET-INVENTORY",
        "repository": repo,
        "required_secret_names": required,
        "overall": overall,
        "environments": env_results,
        "repo_scope": {
            "checked": not skip_repo_scope,
            "present_required_secret_names": repo_required,
        },
        "notes": [
            "Set these secrets on the selected GitHub environment before strict live evidence dispatch.",
            "This checker reads secret metadata only; it never reads or prints secret values.",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=DEFAULT_REPOSITORY)
    parser.add_argument("--environment", action="append", default=[])
    parser.add_argument("--environment-secrets-json", action="append", default=[], help="Fixture JSON as ENV=PATH")
    parser.add_argument("--repo-secrets-json", type=Path)
    parser.add_argument("--skip-repo-scope", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    environments = args.environment or list(DEFAULT_ENVIRONMENTS)
    env_fixture_files = parse_env_file_specs(args.environment_secrets_json)
    result = evaluate(args.repo, environments, env_fixture_files, args.repo_secrets_json, args.skip_repo_scope)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    if args.json or not args.output:
        print(text)
    return 0 if result["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
