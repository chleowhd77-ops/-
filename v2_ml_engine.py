import pickle
import pandas as pd
import warnings
import os
warnings.filterwarnings('ignore')

from pathlib import Path
from learning_state import active_entry


def _load_brain(root):
    entry = active_entry(root, 'v2')
    name = entry.get('artifact')
    paths = []
    if name and Path(name).name == name:
        paths.append((root/'.learning_models'/name,entry.get('active_version','unverified')))
    paths.append((root/'v2_ai_brain.pkl','legacy-v2-fallback'))
    for path,version in paths:
        try:
            with path.open('rb') as stream:
                model = pickle.load(stream)
            if not callable(getattr(model,'predict',None)):
                continue
            return model,version
        except (OSError, ValueError, EOFError, pickle.PickleError, AttributeError, ImportError, TypeError):
            continue
    return None,'unavailable'


v2_brain, v2_model_version = _load_brain(Path(__file__).resolve().parent)

def get_v2_ai_pick(h_odds, d_odds, a_odds):
    """
    메인 로봇(V1)이 배당을 던져주면, V2 머신러닝이 'H(홈)', 'D(무)', 'A(원정)' 픽을 반환합니다.
    """
    if v2_brain is None:
        return "V2_OFF" # 뇌가 없으면 기존 V1 로직만 가동
    
    try:
        # 배당률 숫자 변환 및 검증
        h, d, a = float(h_odds), float(d_odds), float(a_odds)
        if h == 0 or d == 0 or a == 0:
            return "NO_ODDS"
            
        # AI 분석 가동
        input_data = pd.DataFrame([{'B365H': h, 'B365D': d, 'B365A': a}])
        ai_pick = v2_brain.predict(input_data)[0]
        
        return ai_pick 
    except Exception:
        return "ERROR"


def get_v2_prediction(h_odds, d_odds, a_odds):
    """Expose this model's own 1X2 probabilities, never another analyst's."""
    import math
    try:
        odds = [float(x) for x in (h_odds, d_odds, a_odds)]
        if not all(math.isfinite(x) and x > 1 for x in odds):
            return {'status':'unavailable','source_code':'NO_ODDS','reason':'V2용 1X2 배당 미수신'}
    except (TypeError, ValueError):
        return {'status':'unavailable','source_code':'NO_ODDS','reason':'V2용 1X2 배당 미수신'}
    if v2_brain is None:
        return {'status':'unavailable','source_code':'V2_OFF','reason':'V2 모델 파일 연결 대기'}
    try:
        data = pd.DataFrame([dict(zip(('B365H','B365D','B365A'),odds))])
        code = str(v2_brain.predict(data)[0]).upper()
        if code not in ('H','D','A'):
            raise ValueError('unknown V2 class')
        result = {'engine':'v2-ai','code':code,'status':'ready','market_key':'1x2',
                  'selection_side':{'H':'home','D':'draw','A':'away'}[code],
                  'model_version': v2_model_version,
                  'odds':dict(zip(('home','draw','away'),odds))}
        if hasattr(v2_brain,'predict_proba'):
            result['probabilities'] = {{'H':'home','D':'draw','A':'away'}[str(k).upper()]:float(v)
                for k,v in zip(v2_brain.classes_,v2_brain.predict_proba(data)[0]) if str(k).upper() in ('H','D','A')}
            result['probability'] = result['probabilities'].get(result['selection_side'])
        return result
    except Exception as e:
        return {'status':'unavailable','source_code':'ERROR','reason':f'V2 모델 계산 오류: {type(e).__name__}'}
