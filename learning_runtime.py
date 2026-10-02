"""Resumable local training stages; never publish an unfinished model."""
import time
import pickle
import os
from pathlib import Path

from learning_state import atomic_json, digest, read_json

RUNTIME_REVISION = 'R7.13.16'


class LearningPaused(BaseException):
    """Bypass legacy model fallbacks, but always release DB/file locks."""


class StageCache:
    def __init__(self, root, engine, notify=None, budget_seconds=90):
        self.root = Path(root)
        self.engine = engine
        self.notify = notify
        self.budget_seconds = budget_seconds
        self.compute_seconds = 0.0
        self.folder = None

    def note(self, stage):
        if self.notify:
            self.notify(stage)

    def prepare(self, examples):
        # Reuse only identical input data and implementation. New outcomes,
        # corrected features or new software cannot inherit an old exam.
        self.note('학습 입력 서명 계산')
        # Incremental encoding avoids a second giant JSON string in memory.
        import hashlib
        hasher = hashlib.sha256((RUNTIME_REVISION+self.engine).encode())
        for row in examples:
            hasher.update(pickle.dumps(row, protocol=5))
        key = hasher.hexdigest()
        self.folder = self.root / '.learning_checkpoints' / self.engine / key

    def run(self, stage, compute):
        if self.folder is None:
            raise RuntimeError('training checkpoint input not set')
        path = self.folder / (stage + '.pkl')
        saved = {}
        try:
            with path.open('rb') as stream: saved = pickle.load(stream)
        except (OSError, ValueError, EOFError, pickle.UnpicklingError):
            pass
        if saved.get('complete') is True:
            self.note(stage + ' · 저장 결과 재사용')
            return saved['result']
        if self.compute_seconds >= self.budget_seconds:
            self.note(stage + ' · 다음 주기에 이어서 진행')
            raise LearningPaused(stage)
        self.note(stage + ' · 계산 시작')
        started = time.monotonic()
        result = compute()
        self.compute_seconds += time.monotonic() - started
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        with temporary.open('wb') as stream:
            pickle.dump(dict(complete=True,result=result),stream,protocol=5)
        os.replace(temporary,path)
        self.note(f'{stage} · 저장 완료 {time.monotonic()-started:.1f}초')
        if self.compute_seconds >= self.budget_seconds:
            raise LearningPaused(stage)
        return result
