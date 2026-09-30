"""Separate changing data from the branch watched by Streamlit deployments."""
import os
import re
from pathlib import Path

import requests

DATA_BRANCH = 'runtime-data'
_READY_REPOS = set()


def ensure_data_branch(repo, headers):
    if repo in _READY_REPOS:
        return True
    api = f'https://api.github.com/repos/{repo}/git'
    target = f'{api}/ref/heads/{DATA_BRANCH}'
    response = requests.get(target, headers=headers, timeout=15)
    if response.status_code == 200:
        _READY_REPOS.add(repo)
        return True
    if response.status_code != 404:
        raise RuntimeError(f'데이터 브랜치 조회 HTTP {response.status_code}')
    source = requests.get(f'{api}/ref/heads/main', headers=headers, timeout=15)
    if source.status_code != 200:
        raise RuntimeError(f'main 기준 버전 조회 HTTP {source.status_code}')
    sha = (source.json().get('object') or {}).get('sha', '')
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise RuntimeError('main 기준 버전 해시 확인 실패')
    response = requests.post(f'{api}/refs', headers=headers,
        json={'ref': f'refs/heads/{DATA_BRANCH}', 'sha': sha}, timeout=20)
    if response.status_code == 422:
        # Another publisher may have created the same branch. Never reset it.
        response = requests.get(target, headers=headers, timeout=15)
    if response.status_code not in (200, 201):
        raise RuntimeError(f'데이터 브랜치 준비 HTTP {response.status_code}')
    _READY_REPOS.add(repo)
    print(f'✅ 데이터 게시 분리: {DATA_BRANCH} · 웹 코드는 main 유지', flush=True)
    return True


def read_published_json(repo, filename, headers=None):
    for branch in (DATA_BRANCH, 'main'):
        try:
            response = requests.get(
                f'https://raw.githubusercontent.com/{repo}/{branch}/{filename}',
                headers=headers, timeout=5)
            if response.status_code == 200:
                payload = response.json()
                if isinstance(payload, (dict, list)):
                    return payload
        except (requests.RequestException, ValueError):
            pass
    return {}


if __name__ == '__main__':
    from dotenv import load_dotenv
    # Deployer keeps this script in a staging directory; credentials belong
    # to the existing service directory, not to the source release bundle.
    load_dotenv(Path.cwd()/'.env')
    token = os.getenv('GITHUB_TOKEN')
    if not token:
        raise SystemExit('기존 서버 GITHUB_TOKEN을 찾지 못해 적용을 중단했습니다.')
    try:
        ensure_data_branch('chleowhd77-ops/-', {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github+json',
        })
    except Exception as error:
        raise SystemExit(f'데이터 게시 분리 준비 실패: {type(error).__name__}: {error}')
