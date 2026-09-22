"""Publish only the independent V3 learning-pick JSON to the existing repo."""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
from typing import Any

import requests


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
    existing = requests.get(url, headers=headers, timeout=20)
    payload: dict[str, Any] = {
        "message": "chore: refresh autonomous V3 learning picks",
        "content": base64.b64encode(source.read_bytes()).decode("ascii"),
        "branch": "main",
    }
    if existing.status_code == 200:
        sha = str((existing.json() or {}).get("sha") or "")
        if sha:
            payload["sha"] = sha
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
