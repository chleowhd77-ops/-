#!/usr/bin/env bash
# R7.12.33 — 이미 저장된 PROTO 경기 전 픽의 표시 보존
# 변경 범위: collector.py 한 파일. 기존 픽/확률/배당/채점/DB/4개 분석기 계산식은 변경하지 않습니다.
set -Eeuo pipefail

cd /home/ubuntu
exec 9>/home/ubuntu/.dj_deploy.lock
flock -n 9 || { echo '다른 교체 작업이 실행 중입니다. 잠시 뒤 다시 실행하세요.'; exit 1; }

RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
VERSION='R7.12.33-proto-pick-preservation'
FROM_SYSTEM_VERSION='R7.12.31-50-batch-world-parity-v3-sync'
FROM_COLLECTOR_SHA='26c0ae110f747af2155890e86e97e05b73dcfa06b386d94070fd4d6a16656cf5'
TARGET_COLLECTOR_SHA='936650362810c162f099ef5b8cc9c5395f7be0b3989e057c8fdf794ba3277d1d'
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /home/ubuntu/update_r71233.XXXXXX)"
BACKUP="/home/ubuntu/backup_r71233_${STAMP}"
INSTALLED=0

cleanup() { rm -rf -- "$STAGE"; }
rollback() {
  trap - ERR
  set +e
  if [[ "$INSTALLED" -eq 1 && -f "$BACKUP/collector.py" ]]; then
    sudo systemctl stop "$SERVICE" >/dev/null 2>&1 || true
    cp -p -- "$BACKUP/collector.py" /home/ubuntu/collector.py
  fi
  sudo systemctl restart "$SERVICE" >/dev/null 2>&1 || true
  echo "적용 실패: collector.py만 직전 백업으로 복원했습니다. DB와 기존 픽은 변경하지 않았습니다. 백업: $BACKUP" >&2
  exit 1
}
trap cleanup EXIT
trap rollback ERR

echo '[1/5] 새 collector.py를 임시 위치에서 검증합니다.'
curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
  "${RAW_BASE}/collector.py?r71233=${STAMP}" -o "$STAGE/collector.py"
echo "$TARGET_COLLECTOR_SHA  $STAGE/collector.py" | sha256sum -c -
python3 -m py_compile "$STAGE/collector.py"
grep -Fq 'COLLECTOR_PATCH_VERSION = "R7.12.33-proto-pick-preservation"' "$STAGE/collector.py"
grep -Fq 'def _preserve_visible_proto_pick_for_retry' "$STAGE/collector.py"
grep -Fq 'scheduled_pick_recovery' "$STAGE/collector.py"

[[ -f /home/ubuntu/collector.py ]] || { echo '기존 collector.py가 없어 중단합니다.'; exit 1; }
[[ -f /home/ubuntu/api_engine.py ]] || { echo 'api_engine.py가 없어 중단합니다.'; exit 1; }
[[ -f /home/ubuntu/ai_predictions.db ]] || { echo '운영 DB가 없어 중단합니다.'; exit 1; }
sudo systemctl cat "$SERVICE" >/dev/null
CURRENT_SYSTEM_VERSION="$(grep -oE 'SYSTEM_VERSION = "[^"]+"' /home/ubuntu/api_engine.py | head -1 || true)"
[[ "$CURRENT_SYSTEM_VERSION" == "SYSTEM_VERSION = \"$FROM_SYSTEM_VERSION\"" ]] || {
  echo "현재 SYSTEM_VERSION이 예상과 달라 중단합니다: $CURRENT_SYSTEM_VERSION" >&2; exit 1;
}
CURRENT_COLLECTOR_SHA="$(sha256sum /home/ubuntu/collector.py | awk '{print $1}')"
if [[ "$CURRENT_COLLECTOR_SHA" == "$TARGET_COLLECTOR_SHA" ]]; then
  echo 'R7.12.33 collector.py가 이미 적용돼 있습니다. 같은 명령을 반복하지 않습니다.'
  exit 0
fi
[[ "$CURRENT_COLLECTOR_SHA" == "$FROM_COLLECTOR_SHA" ]] || {
  echo "현재 collector.py 지문이 R7.12.32와 달라 중단합니다: $CURRENT_COLLECTOR_SHA" >&2; exit 1;
}

echo '[2/5] collector.py만 백업합니다. 운영 DB는 복사·교체하지 않습니다.'
mkdir -p "$BACKUP"
cp -p -- /home/ubuntu/collector.py "$BACKUP/collector.py"
DB_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)"
RUNTIME_INODE_BEFORE=''
[[ -f /home/ubuntu/api_runtime.db ]] && RUNTIME_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)"

echo '[3/5] collector.py 한 파일만 교체합니다.'
sudo systemctl stop "$SERVICE"
install -m 0644 "$STAGE/collector.py" /home/ubuntu/collector.py
INSTALLED=1
python3 -m py_compile /home/ubuntu/collector.py
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]

echo '[4/5] 외부 API 호출 없이, 가려진 시작 전 저장픽을 즉시 복구·게시합니다.'
python3 -u /home/ubuntu/collector.py --mode recovery
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]

sudo systemctl start "$SERVICE"
sleep 6
sudo systemctl is-enabled --quiet "$SERVICE"
sudo systemctl is-active --quiet "$SERVICE"
[[ "$(sudo systemctl show -p Restart --value "$SERVICE")" == 'always' ]]
MAINPID="$(sudo systemctl show -p MainPID --value "$SERVICE")"
[[ "${MAINPID:-0}" -gt 0 ]]

echo '[5/5] 적용값·DB 보존·서비스 상태를 확인합니다.'
[[ "$(sha256sum /home/ubuntu/collector.py | awk '{print $1}')" == "$TARGET_COLLECTOR_SHA" ]]
grep -F 'COLLECTOR_PATCH_VERSION = "R7.12.33-proto-pick-preservation"' /home/ubuntu/collector.py
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]
if [[ -n "$RUNTIME_INODE_BEFORE" && -f /home/ubuntu/api_runtime.db ]]; then
  [[ "$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)" == "$RUNTIME_INODE_BEFORE" ]]
fi

echo "R7.12.33 적용 완료 · $SERVICE active · MainPID=$MAINPID"
echo "예측 DB 보존 · collector 백업: $BACKUP"
echo '수정 범위: 이미 저장된 시작 전 픽을 팀 정보·로고·fixture 보강 대기 때문에 PENDING 카드로 덮어쓰지 않음.'
echo '복구 작업은 기존 저장픽을 다시 표시만 하며, 외부 API 호출·새 픽 계산·과거 픽/확률/배당/채점 변경을 하지 않습니다.'
echo 'WORLD/API 한도/V2/V3/로봇 계산식은 이번 적용에서 변경하지 않았습니다.'
echo '같은 적용 명령을 반복하지 말고 이 마지막 출력 화면과 웹 픽 화면을 보내주세요.'
