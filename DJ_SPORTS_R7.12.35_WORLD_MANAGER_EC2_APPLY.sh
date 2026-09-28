#!/usr/bin/env bash
# R7.12.35 — WORLD 일정 복구 + 관리자 투자 장부 안전 보정
# 바꾸는 파일: collector.py, manager_investment_autopilot.py
# 보존 대상: ai_predictions.db, api_runtime.db, 기존 고객/로봇/V2/V3/Toto14 픽과 채점 기록
set -Eeuo pipefail

ROOT_DIR='/home/ubuntu'
RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
COLLECTOR_SERVICE='dj-collector.service'
MANAGER_SERVICE='dj-manager-investment.service'
VERSION='R7.12.35-world-schedule-manager-safe'
FROM_COLLECTOR_SHA='b118d1615df7a9048f302fd796813aceb686dd6f3a5766c2c3f106665377acf0'
TARGET_COLLECTOR_SHA='fcb7e46069132dc392cd71c19cb9aacf8ef28d113151288403a79d8260036d94'
FROM_MANAGER_SHA='49ba4351901b0afeb848d316b0f36aab0b19c547c243795fc5f0e68af92fea1e'
TARGET_MANAGER_SHA='91a91957ea58b5bc36f9f1d7901e19a54f3d54bbfc4a82a3bf759c7cbc2d5de5'
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /home/ubuntu/update_r71235.XXXXXX)"
BACKUP="/home/ubuntu/backup_r71235_${STAMP}"
INSTALLED=0

cd "$ROOT_DIR"
exec 9>/home/ubuntu/.dj_deploy.lock
flock -n 9 || { echo '다른 배포가 진행 중입니다. 잠시 뒤 다시 실행하세요.'; exit 1; }

cleanup() { rm -rf -- "$STAGE"; }
rollback() {
  trap - ERR
  set +e
  if [[ "$INSTALLED" -eq 1 ]]; then
    sudo systemctl stop "$COLLECTOR_SERVICE" >/dev/null 2>&1 || true
    [[ -f "$BACKUP/collector.py" ]] && cp -p -- "$BACKUP/collector.py" "$ROOT_DIR/collector.py"
    [[ -f "$BACKUP/manager_investment_autopilot.py" ]] && cp -p -- "$BACKUP/manager_investment_autopilot.py" "$ROOT_DIR/manager_investment_autopilot.py"
  fi
  sudo systemctl restart "$COLLECTOR_SERVICE" >/dev/null 2>&1 || true
  echo "적용 실패: 소스 두 파일만 직전 백업으로 복원했습니다. DB·기존 픽·채점은 변경하지 않았습니다. 백업: $BACKUP" >&2
  exit 1
}
trap cleanup EXIT
trap rollback ERR

echo '[1/5] GitHub 새 소스를 임시 위치에서 검증합니다.'
curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
  "${RAW_BASE}/collector.py?r71235=${STAMP}" -o "$STAGE/collector.py"
curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
  "${RAW_BASE}/manager_investment_autopilot.py?r71235=${STAMP}" -o "$STAGE/manager_investment_autopilot.py"
echo "$TARGET_COLLECTOR_SHA  $STAGE/collector.py" | sha256sum -c -
echo "$TARGET_MANAGER_SHA  $STAGE/manager_investment_autopilot.py" | sha256sum -c -
python3 -m py_compile "$STAGE/collector.py" "$STAGE/manager_investment_autopilot.py"
grep -Fq 'COLLECTOR_PATCH_VERSION = "R7.12.35-world-schedule-freshness"' "$STAGE/collector.py"
grep -Fq 'MANAGER_ENGINE_VERSION = "manager-investment-independent-v2-validated-value"' "$STAGE/manager_investment_autopilot.py"

echo '[2/5] 운영 파일·서비스·DB 보존 조건을 확인합니다.'
[[ -f "$ROOT_DIR/collector.py" && -f "$ROOT_DIR/manager_investment_autopilot.py" ]] || { echo '기존 소스가 없어 중단합니다.'; exit 1; }
[[ -f "$ROOT_DIR/ai_predictions.db" ]] || { echo '운영 예측 DB가 없어 중단합니다.'; exit 1; }
sudo systemctl cat "$COLLECTOR_SERVICE" >/dev/null
sudo systemctl cat "$MANAGER_SERVICE" >/dev/null
CURRENT_COLLECTOR_SHA="$(sha256sum "$ROOT_DIR/collector.py" | awk '{print $1}')"
CURRENT_MANAGER_SHA="$(sha256sum "$ROOT_DIR/manager_investment_autopilot.py" | awk '{print $1}')"
if [[ "$CURRENT_COLLECTOR_SHA" == "$TARGET_COLLECTOR_SHA" && "$CURRENT_MANAGER_SHA" == "$TARGET_MANAGER_SHA" ]]; then
  echo 'R7.12.35가 이미 적용되어 있습니다. 같은 명령을 반복하지 않습니다.'
  exit 0
fi
[[ "$CURRENT_COLLECTOR_SHA" == "$FROM_COLLECTOR_SHA" ]] || { echo "현재 collector.py 지문이 R7.12.34와 달라 중단합니다: $CURRENT_COLLECTOR_SHA" >&2; exit 1; }
[[ "$CURRENT_MANAGER_SHA" == "$FROM_MANAGER_SHA" ]] || { echo "현재 manager 엔진 지문이 R7.12.24와 달라 중단합니다: $CURRENT_MANAGER_SHA" >&2; exit 1; }
DB_INODE_BEFORE="$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")"
RUNTIME_INODE_BEFORE=''
[[ -f "$ROOT_DIR/api_runtime.db" ]] && RUNTIME_INODE_BEFORE="$(stat -c '%d:%i' "$ROOT_DIR/api_runtime.db")"

echo '[3/5] 소스 두 파일만 백업합니다.'
mkdir -p "$BACKUP"
cp -p -- "$ROOT_DIR/collector.py" "$BACKUP/collector.py"
cp -p -- "$ROOT_DIR/manager_investment_autopilot.py" "$BACKUP/manager_investment_autopilot.py"

echo '[4/5] 수집기만 재시작하고 관리자 장부는 한 번 안전하게 갱신합니다.'
sudo systemctl stop "$COLLECTOR_SERVICE"
install -m 0644 "$STAGE/collector.py" "$ROOT_DIR/collector.py"
install -m 0644 "$STAGE/manager_investment_autopilot.py" "$ROOT_DIR/manager_investment_autopilot.py"
INSTALLED=1
python3 -m py_compile "$ROOT_DIR/collector.py" "$ROOT_DIR/manager_investment_autopilot.py"
[[ "$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")" == "$DB_INODE_BEFORE" ]]
sudo systemctl start "$COLLECTOR_SERVICE"
sleep 6
sudo systemctl is-active --quiet "$COLLECTOR_SERVICE"
sudo systemctl start "$MANAGER_SERVICE"
if sudo systemctl is-failed --quiet "$MANAGER_SERVICE"; then
  echo '관리자 장부 갱신 서비스가 실패해 적용을 취소합니다.' >&2
  exit 1
fi

echo '[5/5] 적용값·DB 보존·서비스 상태를 확인합니다.'
[[ "$(sha256sum "$ROOT_DIR/collector.py" | awk '{print $1}')" == "$TARGET_COLLECTOR_SHA" ]]
[[ "$(sha256sum "$ROOT_DIR/manager_investment_autopilot.py" | awk '{print $1}')" == "$TARGET_MANAGER_SHA" ]]
[[ "$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")" == "$DB_INODE_BEFORE" ]]
if [[ -n "$RUNTIME_INODE_BEFORE" && -f "$ROOT_DIR/api_runtime.db" ]]; then
  [[ "$(stat -c '%d:%i' "$ROOT_DIR/api_runtime.db")" == "$RUNTIME_INODE_BEFORE" ]]
fi
sudo systemctl is-active --quiet "$COLLECTOR_SERVICE"
test -s "$ROOT_DIR/manager_investment_picks.json"

echo "R7.12.35 적용 완료 · $COLLECTOR_SERVICE active · 관리자 장부 갱신 완료"
echo "DB 보존 · 소스 백업: $BACKUP"
echo 'WORLD는 일정 목록을 먼저 최신화하며, 배당/분석이 준비되기 전에는 고객 픽을 발행하지 않습니다.'
echo '관리자픽은 지난 미채점 경기를 현재 투자 후보에서 제외하고, 고배당은 검증 표본이 충분할 때만 후보로 허용합니다.'
