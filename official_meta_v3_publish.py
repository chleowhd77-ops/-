"""Publish only the independent V3 learning-pick JSON to the existing repo."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from typing import Any

import requests
from runtime_publisher import DATA_BRANCH, ensure_data_branch


def _settings() -> tuple[str, str]:
    repo = os.getenv("GITHUB_REPO", "").strip()
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if repo and token:
        return repo, token
    try:
        from config import GITHUB_REPO, GITHUB_TOKEN  # type: ignore
    except Exception as error:
        raise RuntimeError("GitHub publish settings are unavailable") from error
    repo = str(repo or GITHUB_REPO or "").strip()
    token = str(token or GITHUB_TOKEN or "").strip()
    if not repo or not token:
        raise RuntimeError("GitHub publish token is unavailable")
    return repo, token


def publish(file_path: str | Path, remote_path: str = "v3_learning_picks.json") -> None:
    source = Path(file_path)
    if not source.is_file():
        raise RuntimeError(f"V3 learning file not found: {source}")
    repo, token = _settings()
    url = f"https://api.github.com/repos/{repo}/contents/{remote_path}"
    headers: dict[str, Any] = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    local_bytes = source.read_bytes()
    ensure_data_branch(repo, headers)
    existing = requests.get(url, headers=headers, params={'ref': DATA_BRANCH}, timeout=20)
    payload: dict[str, Any] = {
        "message": "chore: refresh autonomous V3 learning picks",
        "content": base64.b64encode(local_bytes).decode("ascii"),
        "branch": DATA_BRANCH,
    }
    if existing.status_code == 200:
        remote = existing.json() or {}
        sha = str(remote.get("sha") or "")
        if sha:
            payload["sha"] = sha
        # The V3 timer now checks new PROTO/WORLD cards frequently. Avoid a
        # GitHub commit when the only difference is the volatile generated_at
        # timestamp; new picks, grades or training metrics still publish.
        try:
            remote_bytes = base64.b64decode(str(remote.get("content") or ""))
            local_json = json.loads(local_bytes.decode("utf-8"))
            remote_json = json.loads(remote_bytes.decode("utf-8"))
            if isinstance(local_json, dict) and isinstance(remote_json, dict):
                local_compare = dict(local_json)
                remote_compare = dict(remote_json)
                local_compare.pop("generated_at", None)
                remote_compare.pop("generated_at", None)
                if local_compare == remote_compare:
                    print("V3_WEB_LEARNING_UNCHANGED")
                    return
        except Exception:
            pass
    elif existing.status_code != 404:
        raise RuntimeError(f"GitHub V3 file lookup failed: HTTP {existing.status_code}")
    response = requests.put(url, headers=headers, json=payload, timeout=30)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"GitHub V3 file publish failed: HTTP {response.status_code}")


def _main() -> int:
    parser = argparse.ArgumentParser(description="Publish autonomous V3 learning JSON only")
    parser.add_argument("--file", required=True)
    args = parser.parse_args()
    publish(args.file)
    print("V3_WEB_LEARNING_PUBLISHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
