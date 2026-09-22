import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import pickle

print("🧠 [V2 머신러닝 뇌 학습 훈련소 가동]")

# 1. 교과서(데이터) 불러오기
try:
    df = pd.read_csv('master_training_data.csv')
    print(f"📖 데이터 불러오기 성공! 총 {len(df)}경기 바탕으로 딥러닝 시작...")
except FileNotFoundError:
    print("❌ 에러: 교과서(master_training_data.csv)가 없습니다!")
    exit()

# 2. 데이터 가공 (로봇이 공부할 핵심 과목 선택)
# 초기 배당 흐름(B365H, B365D, B365A)을 분석해 결과(FTR)를 예측하도록 지시
features = ['B365H', 'B365D', 'B365A']
df = df.dropna(subset=features + ['FTR'])

X = df[features] # 문제지 (경기 전 배당)
y = df['FTR']    # 정답지 (실제 경기 결과: H=홈승, D=무승부, A=원정승)

# 3. 모의고사 준비 (전체 데이터 중 80%는 공부용, 20%는 시험용으로 분리)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

# 4. 머신러닝(랜덤 포레스트 앙상블) 훈련 시작!
print("⏳ 수만 번의 시뮬레이션 중... 로봇이 인간이 모르는 역배당 패턴을 찾고 있습니다...")
model = RandomForestClassifier(n_estimators=100, random_state=42)
model.fit(X_train, y_train)

# 5. 모의고사 채점
predictions = model.predict(X_test)
accuracy = accuracy_score(y_test, predictions)
print(f"✅ 학습 완료! V2 인공지능의 순수 배당 패턴 예측 적중률: {accuracy * 100:.1f}%")

# 6. 똑똑해진 뇌를 파일로 영구 저장
with open('v2_ai_brain.pkl', 'wb') as f:
    pickle.dump(model, f)
    
print("🎉 [최종 완료] 인공지능 뇌 파일 'v2_ai_brain.pkl'이 생성되었습니다!")
print("👉 이제 이 뇌를 현재 돌아가는 자동화 봇에 연결만 하면 모든 작업이 끝납니다.")
