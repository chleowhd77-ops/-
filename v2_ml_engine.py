import pickle
import pandas as pd
import warnings
warnings.filterwarnings('ignore') # 보기 싫은 자잘한 파이썬 경고문구 숨기기

print("=============================================")
print(" 🏥 [V2 인공지능 뇌 신경망 연결 및 테스트] 🏥")
print("=============================================")

# 1. 뇌 파일(pkl) 불러오기
try:
    with open('v2_ai_brain.pkl', 'rb') as f:
        ml_brain = pickle.load(f)
    print("✅ 성공: 'v2_ai_brain.pkl' 뇌 신경망 이식 완료!")
except FileNotFoundError:
    print("❌ 에러: 뇌 파일을 찾을 수 없습니다. (train_v2_brain.py를 먼저 실행하세요)")
    exit()

# 2. 로봇에게 테스트용 배당표 입력 (정배, 치열한 접전, 역배 가능성)
print("\n🔍 [실시간 배당 기반 AI 예측 테스트 가동]")
test_matches = [
    {"name": "맨체스터 시티 vs 풀럼", "H": 1.15, "D": 8.00, "A": 15.00}, # 뻔한 똥배당
    {"name": "토트넘 vs 아스널", "H": 2.90, "D": 3.60, "A": 2.30},      # 치열한 라이벌전
    {"name": "울버햄튼 vs 셰필드", "H": 2.10, "D": 3.20, "A": 3.80}      # 팽팽한 역배 타겟
]

for match in test_matches:
    # AI 뇌에 배당 데이터 주입 (인간의 감정 배제, 오직 숫자만!)
    input_data = pd.DataFrame([{'B365H': match['H'], 'B365D': match['D'], 'B365A': match['A']}])
    
    # AI의 예측 (H: 홈승, D: 무승부, A: 원정승)
    ai_pick = ml_brain.predict(input_data)[0]
    
    # 로봇의 언어를 인간의 언어로 번역
    if ai_pick == 'H': result = "🔥 홈팀 승리"
    elif ai_pick == 'D': result = "⚖️ 무승부 (무잡이 꿀배당 패턴!)"
    else: result = "❄️ 원정팀 승리 (역배당/이변 패턴!)"
        
    print(f"⚽ {match['name']} (배당: 승 {match['H']} / 무 {match['D']} / 패 {match['A']})")
    print(f"   👉 V2 알파고 픽: {result}\n")

print("=============================================")
print("🎉 이식 수술 1단계 완벽 성공! 엔진이 살아 숨 쉽니다!")
