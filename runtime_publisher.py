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


class PublishedFeedCache:
    """Serve the last successful feed while one background refresh runs.

    No credentials or Streamlit state in worker threads. First-page loads can
    prefetch independent feeds together; subsequent widget clicks never wait
    on an expired remote feed.
    """
    def __init__(self, repo, headers=None, ttl=45):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        self.repo, self.headers, self.ttl = repo, headers, ttl
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='published-feed')
        self.lock = threading.Lock()
        self.entries = {}

    def prefetch(self, filenames):
        import time
        with self.lock:
            for filename in filenames:
                entry = self.entries.setdefault(filename, {'data':None,'checked':0,'success':0,'future':None})
                if entry['future'] is not None or time.monotonic()-entry['checked'] < self.ttl:
                    continue
                entry['checked'] = time.monotonic()
                entry['future'] = self.pool.submit(read_published_json,self.repo,filename,self.headers)

    def get(self, filename):
        import time
        from copy import deepcopy
        from concurrent.futures import TimeoutError
        # Consume a completed refresh before scheduling the next one.
        with self.lock:
            entry = self.entries.setdefault(filename, {'data':None,'checked':0,'success':0,'future':None})
            future = entry['future']
            if future is not None and future.done():
                try: value = future.result()
                except Exception: value = None
                if value:
                    entry['data'],entry['success'] = value,time.time()
                entry['future'] = None
        self.prefetch([filename])
        with self.lock:
            data, future = entry['data'],entry['future']
        if data is None and future is not None:
            try: data = future.result(timeout=6)
            except (TimeoutError, Exception): data = None
            if data:
                with self.lock:
                    entry['data'],entry['success'] = data,time.time()
        return deepcopy(data) if data else {}
