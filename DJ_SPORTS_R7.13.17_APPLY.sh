#!/usr/bin/env bash
# Source update and resume normal collection after API recharge. Upload the eight release files to GitHub before running.
set -Eeuo pipefail

APP_ROOT='/home/ubuntu'
RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
FILES=(collector.py api_engine.py scorecard_core.py manager_investment_autopilot.py official_meta_v3_autopilot.py)
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /tmp/dj-r71317.XXXXXX)"
BACKUP="$APP_ROOT/backup_r71317_$STAMP"
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
    if [[ -f "$BACKUP/offline_flag_was_absent" ]]; then
      rm -f -- "$APP_ROOT/.dj_offline_learning"
    else
      touch "$APP_ROOT/.dj_offline_learning"
    fi
    sudo systemctl start "$SERVICE"
    echo "적용 실패: 직전 소스 복원 및 서비스 재시작을 시도했습니다. 백업: $BACKUP" >&2
    sudo systemctl is-active "$SERVICE"
  else
    echo '검증 실패: 운영 소스와 서비스는 변경하지 않았습니다. GitHub에 8개 파일이 함께 올라갔는지 확인하세요.' >&2
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
    "$RAW_BASE/$file?r71317=$STAMP" -o "$STAGE/$file"
done
(
  cd "$STAGE"
  sha256sum -c <<'DJ_SOURCE_HASHES'
6f5a5341222885bf1af4abcf7752c4f78e5c0ff2db84fd3308d7c780c8bcdaf8  collector.py
6c502665cbc0c8cb6b3b20f06a243a83bd6ebb3ffaf638bff48fbc82e9a0711d  api_engine.py
3f90edeabde7b9f1376f2c4e0481ffc0b735da610a188afb856d16b92272cd03  scorecard_core.py
bef948d2a29f9ee8900194da0c6775d9c9c1ece66d2d10a2bfe45dfc6917197a  manager_investment_autopilot.py
d28d9b12d1cbf732fa668a5e86efce949170e73314de8d589cf13670cb6ee066  official_meta_v3_autopilot.py
DJ_SOURCE_HASHES
  python3 -m py_compile "${FILES[@]}"
)

python3 -c 'import sklearn, scipy, pandas, threadpoolctl' || { echo '학습 패키지가 없어 적용을 중단했습니다. 기존 소스는 유지됩니다.' >&2; exit 1; }

# Existing R7.13.16 runtime-data configuration is retained.
test -f "$APP_ROOT/runtime_publisher.py"

echo '[2/3] 기존 소스 백업 · 서버 파일 5개 교체'
mkdir -p "$BACKUP"
for file in "${FILES[@]}"; do
  if [[ -f "$APP_ROOT/$file" ]]; then
    cp -p -- "$APP_ROOT/$file" "$BACKUP/$file"
  else
    touch "$BACKUP/$file.absent"
  fi
done
if [[ ! -f "$APP_ROOT/.dj_offline_learning" ]]; then
  touch "$BACKUP/offline_flag_was_absent"
fi
DB_ID="$(stat -c '%d:%i' "$APP_ROOT/ai_predictions.db")"
CHANGED=1
sudo systemctl stop "$SERVICE"
for file in "${FILES[@]}"; do
  install -m 0644 "$STAGE/$file" "$APP_ROOT/$file"
done
[[ "$(stat -c '%d:%i' "$APP_ROOT/ai_predictions.db")" == "$DB_ID" ]]

echo '[3/3] 기존 운영 모드로 서비스 재시작'
sudo systemctl start "$SERVICE"
sleep 5
sudo systemctl is-active --quiet "$SERVICE"
echo "R7.13.17 소스 적용 완료 · $SERVICE active · 백업: $BACKUP"
echo 'DB·기존 픽·모델 보존. 채점 조회·수집 재시도 수정 적용. 학습 완료는 실시간 로그에서 확인.'
echo 'active는 소스 적용 완료입니다. 학습 완료·웹 복구는 각각 로그와 화면에서 확인하세요.'
echo '실시간 로그: sudo journalctl -u dj-collector.service -f -n 100 --no-pager'
