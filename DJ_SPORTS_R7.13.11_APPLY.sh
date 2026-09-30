#!/usr/bin/env bash
# Source-only update. Upload the fifteen release files to GitHub before running.
set -Eeuo pipefail

APP_ROOT='/home/ubuntu'
RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
FILES=(collector.py api_engine.py scorecard_core.py manager_investment_autopilot.py official_meta_v3_autopilot.py official_meta_v3.py v2_ml_engine.py analyst_products.py football_model.py learning_state.py learning_worker.py)
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /tmp/dj-r71311.XXXXXX)"
BACKUP="$APP_ROOT/backup_r71311_$STAMP"
CHANGED=0

cleanup() { rm -rf -- "$STAGE"; }
rollback() {
  trap - ERR
  set +e
  if [[ "$CHANGED" == 1 ]]; then
    sudo systemctl stop "$SERVICE"
    for file in "${FILES[@]}"; do
      if [[ -f "$BACKUP/$file" ]]; then
        cp -p -- "$BACKUP/$file" "$APP_ROOT/$file"
      elif [[ -f "$BACKUP/$file.absent" ]]; then
        rm -f -- "$APP_ROOT/$file"
      fi
    done
    sudo systemctl start "$SERVICE"
    echo "적용 실패: 직전 소스 복원 및 서비스 재시작을 시도했습니다. 백업: $BACKUP" >&2
    sudo systemctl is-active "$SERVICE"
  else
    echo '검증 실패: 운영 소스와 서비스는 변경하지 않았습니다. GitHub에 15개 파일이 함께 올라갔는지 확인하세요.' >&2
  fi
  exit 1
}
trap cleanup EXIT
trap rollback ERR

cd "$APP_ROOT"
exec 9>"$APP_ROOT/.dj_deploy.lock"
flock -n 9
test -f "$APP_ROOT/collector.py"
test -f "$APP_ROOT/ai_predictions.db"
sudo systemctl cat "$SERVICE" >/dev/null

echo '[1/3] GitHub 소스 다운로드 · 버전과 문법 확인'
for file in "${FILES[@]}"; do
  curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
    "$RAW_BASE/$file?r71311=$STAMP" -o "$STAGE/$file"
done
(
  cd "$STAGE"
  sha256sum -c <<'DJ_SOURCE_HASHES'
11a9a9810a898af2974da4d68dec8b9e275ec690789b88f48cc6018f56aa2020  collector.py
e3bdfd015d0b48c09f56e74c45ff76d806ebfa6e926ac8c729fdcb95e8b403fa  api_engine.py
acd522ade705b7b5279d7ef0e507046962d5549c61e94d77da1791a115db65f3  scorecard_core.py
4d49d358296e67c7a20f21cb009f664557df2b657cfc936aa2bea4bab6300e8b  manager_investment_autopilot.py
91e42e8e8921307f5f4c9d799afdf18b7fe1b130aa5969160be83b52efc3c096  official_meta_v3_autopilot.py
226abde276e8da82e57ab61f9981e59fb0a0be433da23913c5a735eb7a6addf5  official_meta_v3.py
ac6142ea6b9861af2c61dc92f06ac3a9684aca9e755b276126aeadd7822beb52  v2_ml_engine.py
c9929cc71e9dba18af388aae0c4e7ff669dfca10fecdc5182e13979b302dbe24  analyst_products.py
8fc58715e49972b2a787384d71f5b7a2a31b9fba1269eeb466bf5ddbc621a24f  football_model.py
064db783e02e85d717370cf0cf2cad3ca3a84ff8fe269c170d58d5d89810235e  learning_state.py
8c2b8dc94acff00d925157906f73460e8ac23315b7f62515e953d2600bee635e  learning_worker.py
DJ_SOURCE_HASHES
  python3 -m py_compile "${FILES[@]}"
)

python3 -c 'import sklearn, pandas, threadpoolctl' || { echo '학습 패키지가 없어 적용을 중단했습니다. 기존 소스는 유지됩니다.' >&2; exit 1; }

echo '[2/3] 기존 소스 백업 · 서버 파일 11개 교체'
mkdir -p "$BACKUP"
for file in "${FILES[@]}"; do
  if [[ -f "$APP_ROOT/$file" ]]; then
    cp -p -- "$APP_ROOT/$file" "$BACKUP/$file"
  else
    touch "$BACKUP/$file.absent"
  fi
done
DB_ID="$(stat -c '%d:%i' "$APP_ROOT/ai_predictions.db")"
CHANGED=1
sudo systemctl stop "$SERVICE"
for file in "${FILES[@]}"; do
  install -m 0644 "$STAGE/$file" "$APP_ROOT/$file"
done
[[ "$(stat -c '%d:%i' "$APP_ROOT/ai_predictions.db")" == "$DB_ID" ]]

echo '[3/3] 수집기 재시작'
sudo systemctl start "$SERVICE"
sleep 5
sudo systemctl is-active --quiet "$SERVICE"
echo "R7.13.11 소스 적용 완료 · $SERVICE active · 백업: $BACKUP"
echo 'DB·기존 픽·모델 파일 보존. 독립 학습 점검 후 경기 전 픽을 순차 재분석합니다.'
echo 'active는 소스 적용 완료입니다. 학습 완료 여부는 📚 로그와 관리자 학습 현황에서 확인하세요.'
echo '실시간 로그: sudo journalctl -u dj-collector.service -f -n 100 --no-pager'
