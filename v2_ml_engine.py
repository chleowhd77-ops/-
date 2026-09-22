import pickle
import pandas as pd
import warnings
import os
warnings.filterwarnings('ignore')

# 서버가 켜질 때 인공지능 뇌를 미리 메모리에 장착 (속도 0.01초 최적화)
try:
    # 파일 경로를 서버 환경에 맞게 절대경로로 안전하게 탐색
    brain_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'v2_ai_brain.pkl')
    with open(brain_path, 'rb') as f:
        v2_brain = pickle.load(f)
except Exception as e:
    v2_brain = None
    print(f"⚠️ V2 뇌 연결 대기중... (에러: {e})")

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
