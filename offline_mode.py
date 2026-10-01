"""Explicit stored-data mode; independent of the provider's quota status."""
import os
from pathlib import Path
from contextlib import contextmanager

FLAG = '.dj_offline_learning'

def enabled(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    return os.getenv('DJ_OFFLINE_LEARNING', '').lower() in ('1','true','yes') or (root/FLAG).exists()

@contextmanager
def no_training_network():
    """Training uses files only. Publishing callbacks run outside this scope."""
    import requests
    original = requests.sessions.Session.request
    def blocked(*args, **kwargs):
        raise RuntimeError('학습 중 외부 요청 차단: 저장 자료만 사용해야 합니다')
    requests.sessions.Session.request = blocked
    try:
        yield
    finally:
        requests.sessions.Session.request = original


@contextmanager
def training_root(root):
    """Bind legacy relative DB/cache readers to the requested training copy."""
    import collector
    original_root, original_cwd = collector.APP_DIR, Path.cwd()
    try:
        collector.APP_DIR = Path(root).resolve()
        os.chdir(collector.APP_DIR)
        yield
    finally:
        collector.APP_DIR = original_root
        os.chdir(original_cwd)
