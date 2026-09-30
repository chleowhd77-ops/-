"""Resumable local training stages; never publish an unfinished model."""
import time
from pathlib import Path

from learning_state import atomic_json, digest, read_json

RUNTIME_REVISION = 'R7.13.12'


class LearningPaused(BaseException):
    """Bypass legacy model fallbacks, but always release DB/file locks."""


class StageCache:
    def __init__(self, root, engine, notify=None, budget_seconds=90):
        self.root = Path(root)
        self.engine = engine
        self.notify = notify
        self.deadline = time.monotonic() + budget_seconds
        self.folder = None

    def note(self, stage):
        if self.notify:
            self.notify(stage)

    def prepare(self, examples):
        # Reuse only identical input data and implementation. New outcomes,
        # corrected features or new software cannot inherit an old exam.
        key = digest([RUNTIME_REVISION, self.engine, examples])
        self.folder = self.root / '.learning_checkpoints' / self.engine / key

    def run(self, stage, compute):
        if self.folder is None:
            raise RuntimeError('training checkpoint input not set')
        path = self.folder / (stage + '.json')
        saved = read_json(path)
        if saved.get('complete') is True:
            self.note(stage + ' · 저장 결과 재사용')
            return saved['result']
        if time.monotonic() >= self.deadline:
            self.note(stage + ' · 다음 주기에 이어서 진행')
            raise LearningPaused(stage)
        self.note(stage + ' · 계산 시작')
        started = time.monotonic()
        result = compute()
        atomic_json(path, dict(complete=True, result=result))
        self.note(f'{stage} · 저장 완료 {time.monotonic()-started:.1f}초')
        if time.monotonic() >= self.deadline:
            raise LearningPaused(stage)
        return result
