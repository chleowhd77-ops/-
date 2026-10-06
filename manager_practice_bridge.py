"""Live storage adapter around the unchanged, archived paper-investment code.

No study loop is started. Original question(), RememberedAuthor, PracticeAuthor,
predictions() and wrong_packet() are used directly. Existing live receipts and
publication IDs remain compatible; no paid historical answer is retried.
"""
import hashlib
import importlib
from functools import lru_cache
import json
from pathlib import Path
import sys


@lru_cache(maxsize=1)
def originals():
    root = Path(__file__).resolve().parent / 'practice_original'
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for name, meta in manifest.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest() != meta['sha256']:
            raise ValueError('모의투자 원본 변경 감지: '+name)
    sys.path.insert(0, str(root))
    try:
        for name in manifest:
            module = name[:-3]
            loaded = sys.modules.get(module)
            if loaded is not None and Path(loaded.__file__).resolve() != (root/name).resolve():
                raise ValueError('모의투자 원본과 다른 모듈이 로드됨: '+name)
        modules = [importlib.import_module(n) for n in
                   ('analyst_method_loop', 'analyst_ten_match_practice', 'analyst_remembered_exam')]
        # question() imports these helpers when called, after this path is removed.
        importlib.import_module('manager_learning_goal')
        importlib.import_module('scorecard_core')
    finally:
        sys.path.remove(str(root))
    return modules


def option_key(o):
    def line(value):
        return float(value) if value is not None else None
    return (o.get('market_key'), o.get('selection_side'), line(o.get('handicap_base')),
            line(o.get('totals_base')), o.get('raw_pick'))


def case_from_live(q):
    method, _, _ = originals()
    from weather_observation import correct_evidence_weather
    question = method.question(correct_evidence_weather(q['evidence']), q.get('practice_candidates', q['options']),
        q['identity'], 'manager', captured_at=q['source']['evidence_captured_at'])
    by_key = {option_key(o): o['option_id'] for o in q['options']}
    mapping = {o['option_id']: by_key[option_key(o)] for o in question['options']}
    if not mapping:
        raise ValueError('모의투자 기준의 실제 선택지 없음: '+q['case_id'])
    return {'case_id':q['case_id'], 'question':question}, mapping


def exam_packet(packet):
    cases, mappings = [], {}
    for q in packet['questions']:
        case, mapping = case_from_live(q)
        cases.append(case); mappings[case['case_id']] = mapping
    memory = packet.get('memory', {})
    successes = list(memory.get('successful_memory', []))
    errors = list(memory.get('error_memory', []))
    for record in memory.get('live_pick_reviews', []):
        (successes if record.get('grade', {}).get('is_correct') else errors).append(record)
    outgoing = {'mode':'exam', 'analyst':packet['analyst'],
        'exam_context':'실제 예정 경기 첫 분석. 경기 결과와 정답은 아직 없으며 재시험하지 않습니다.',
        'successful_memory':successes, 'error_memory':errors, 'wrong_feedback':[],
        'questions':cases}
    return outgoing, cases, mappings


class PaperConnection:
    def __init__(self, settings, stop_file):
        method, practice, remembered = originals()
        self.method, self.practice = method, practice
        self.exam = remembered.RememberedAuthor(settings, stop_file)
        self.review = practice.PracticeAuthor(settings, stop_file)
        self.exam.instructions = self.exam.instructions.replace(
            'This is one attempt on historical matches, not a prospective performance test.',
            'This is one first prediction on actual upcoming matches; their outcomes are not available.')
        self.review.instructions = self.review.instructions.replace(
            'This is SAME TEN MATCH REHEARSAL, not an unseen evaluation.',
            'This reviews settled actual matches. Original picks stay fixed; do not rerun predictions.')

    def ask(self, folder, name, packet):
        folder = Path(folder)/'paper-original'
        if packet['mode'] == 'candidates':
            outgoing, cases, mappings = exam_packet(packet)
            raw = self.exam.ask(folder, name, outgoing)
            values = self.practice.predictions(raw, cases)
            picks = [{'case_id':cid, 'selected_id':mappings[cid][a['selected_id']],
                      'probability':a['scores'][a['selected_id']], 'reason':a['reason'],
                      'scores':{mappings[cid][k]:v for k,v in a['scores'].items()}}
                     for cid,a in values.items()]
            return {'summary':raw['summary'], 'picks':picks, 'reflections':[]}
        if packet['mode'] != 'review':
            raise ValueError('모의투자 연결 대상 모드 아님')
        wrong, notes = [], []
        for record in packet['records']:
            original, grade = record['original'], record['grade']
            if grade['is_correct']:
                notes.append({'case_id':record['case_id'],
                    'explanation':'적중 기록: 모의투자 방식대로 원래 답안을 보존함. 오답 AI 복기 대상 아님.'})
                continue
            case, mapping = case_from_live(original['question'])
            score = [int(v.strip()) for v in grade['actual_score'].split(':')]
            case.update(score=score, known_at=grade['graded_at'],
                        answers=self.method.correct_options(case['question'], *score))
            selected = next(k for k,v in mapping.items() if v == original['answer']['selected_id'])
            # Older single-pick receipts have no full probability vector. Keep it unknown.
            own = original.get('candidate_analysis') or original['answer']
            answer = {'selected_id':selected,
                'scores':{k:own.get('scores', {})[v] for k,v in mapping.items()
                          if v in own.get('scores', {})}, 'reason':own['reason']}
            wrong.append(self.practice.wrong_packet(case, answer))
        if wrong:
            outgoing = {'mode':'review', 'analyst':packet['analyst'], 'wrong_answers':wrong}
            raw = self.review.ask(folder, name, outgoing)
            if raw.get('predictions'):
                raise ValueError('복기에서 기존 픽 변경 차단')
            notes += raw['reflections']
        return {'summary':'모의투자 원본 복기 경로', 'picks':[], 'reflections':notes}
