#!/usr/bin/env bash
# R7.12.38 — 실제 공급사 상태로 전체 내부 API 보호장치를 재확인
# 교체 파일: api_engine.py, collector.py
# 보존 대상: ai_predictions.db, api_runtime.db, 기존 픽·확률·배당·채점 기록
set -Eeuo pipefail

ROOT_DIR='/home/ubuntu'
RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
FROM_API_SHA='c724dc754497fe985d8e630c2d30b0c71fcc98fb0c173d4649f02c7409de1e7a'
TARGET_API_SHA='754e52faefcffe1e48e9af6bc2ed5fd395eefa081a12c013c33d85f610733829'
FROM_COLLECTOR_SHA='d6ee571fb90aa97369e406399f372cf7e7792f091bbf48026e2ea33db2e3bdf5'
TARGET_COLLECTOR_SHA='e6c814a377f39d9d101eba927e3e72bf1d33722e8e922f24d3f54b87bcdd05af'
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /home/ubuntu/update_r71238.XXXXXX)"
BACKUP="/home/ubuntu/backup_r71238_${STAMP}"
INSTALLED=0

cd "$ROOT_DIR"
exec 9>/home/ubuntu/.dj_deploy.lock
flock -n 9 || { echo '다른 배포가 진행 중입니다. 잠시 뒤 다시 실행하세요.'; exit 1; }

cleanup() { rm -rf -- "$STAGE"; }
rollback() {
  trap - ERR
  set +e
  if [[ "$INSTALLED" -eq 1 ]]; then
    sudo systemctl stop "$SERVICE" >/dev/null 2>&1 || true
    [[ -f "$BACKUP/api_engine.py" ]] && cp -p -- "$BACKUP/api_engine.py" "$ROOT_DIR/api_engine.py"
    [[ -f "$BACKUP/collector.py" ]] && cp -p -- "$BACKUP/collector.py" "$ROOT_DIR/collector.py"
  fi
  sudo systemctl restart "$SERVICE" >/dev/null 2>&1 || true
  echo "적용 실패: 소스 두 파일만 직전 백업으로 복원했습니다. DB·기존 픽·채점은 변경하지 않았습니다. 백업: $BACKUP" >&2
  exit 1
}
trap cleanup EXIT
trap rollback ERR

echo '[1/4] GitHub 새 소스를 임시 위치에서 검증합니다.'
curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
  "${RAW_BASE}/api_engine.py?r71238=${STAMP}" -o "$STAGE/api_engine.py"
curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
  "${RAW_BASE}/collector.py?r71238=${STAMP}" -o "$STAGE/collector.py"
echo "$TARGET_API_SHA  $STAGE/api_engine.py" | sha256sum -c -
echo "$TARGET_COLLECTOR_SHA  $STAGE/collector.py" | sha256sum -c -
python3 -m py_compile "$STAGE/api_engine.py" "$STAGE/collector.py"
grep -Fq 'api_provider_status_daily' "$STAGE/api_engine.py"
grep -Fq 'provider_status_direct_verified' "$STAGE/api_engine.py"
grep -Fq 'COLLECTOR_PATCH_VERSION = "R7.12.38-provider-status-direct-reconcile"' "$STAGE/collector.py"

echo '[2/4] 운영 파일·서비스·DB 보존 조건을 확인합니다.'
[[ -f "$ROOT_DIR/api_engine.py" && -f "$ROOT_DIR/collector.py" ]] || { echo '기존 소스가 없어 중단합니다.'; exit 1; }
[[ -f "$ROOT_DIR/ai_predictions.db" && -f "$ROOT_DIR/api_runtime.db" ]] || { echo '운영 DB 또는 API 장부가 없어 중단합니다.'; exit 1; }
sudo systemctl cat "$SERVICE" >/dev/null
CURRENT_API_SHA="$(sha256sum "$ROOT_DIR/api_engine.py" | awk '{print $1}')"
CURRENT_COLLECTOR_SHA="$(sha256sum "$ROOT_DIR/collector.py" | awk '{print $1}')"
if [[ "$CURRENT_API_SHA" == "$TARGET_API_SHA" && "$CURRENT_COLLECTOR_SHA" == "$TARGET_COLLECTOR_SHA" ]]; then
  echo 'R7.12.38이 이미 적용되어 있습니다. 같은 명령을 반복하지 않습니다.'
  exit 0
fi
[[ "$CURRENT_API_SHA" == "$FROM_API_SHA" ]] || { echo "현재 api_engine.py 지문이 R7.12.36과 달라 중단합니다: $CURRENT_API_SHA" >&2; exit 1; }
[[ "$CURRENT_COLLECTOR_SHA" == "$FROM_COLLECTOR_SHA" ]] || { echo "현재 collector.py 지문이 R7.12.37과 달라 중단합니다: $CURRENT_COLLECTOR_SHA" >&2; exit 1; }
PREDICTION_INODE_BEFORE="$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")"
RUNTIME_INODE_BEFORE="$(stat -c '%d:%i' "$ROOT_DIR/api_runtime.db")"

echo '[3/4] 소스 두 파일만 백업하고 수집기를 재시작합니다.'
mkdir -p "$BACKUP"
cp -p -- "$ROOT_DIR/api_engine.py" "$BACKUP/api_engine.py"
cp -p -- "$ROOT_DIR/collector.py" "$BACKUP/collector.py"
sudo systemctl stop "$SERVICE"
install -m 0644 "$STAGE/api_engine.py" "$ROOT_DIR/api_engine.py"
install -m 0644 "$STAGE/collector.py" "$ROOT_DIR/collector.py"
INSTALLED=1
python3 -m py_compile "$ROOT_DIR/api_engine.py" "$ROOT_DIR/collector.py"
[[ "$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")" == "$PREDICTION_INODE_BEFORE" ]]
[[ "$(stat -c '%d:%i' "$ROOT_DIR/api_runtime.db")" == "$RUNTIME_INODE_BEFORE" ]]
sudo systemctl start "$SERVICE"
sleep 8
sudo systemctl is-active --quiet "$SERVICE"

echo '[4/4] 적용값·DB 보존·서비스 상태를 확인합니다.'
[[ "$(sha256sum "$ROOT_DIR/api_engine.py" | awk '{print $1}')" == "$TARGET_API_SHA" ]]
[[ "$(sha256sum "$ROOT_DIR/collector.py" | awk '{print $1}')" == "$TARGET_COLLECTOR_SHA" ]]
[[ "$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")" == "$PREDICTION_INODE_BEFORE" ]]
[[ "$(stat -c '%d:%i' "$ROOT_DIR/api_runtime.db")" == "$RUNTIME_INODE_BEFORE" ]]
sudo systemctl is-active --quiet "$SERVICE"

echo "R7.12.38 적용 완료 · $SERVICE active"
echo "DB 보존 · 소스 백업: $BACKUP"
echo '내부 전체 보호선에 걸리면 공급사 /status를 직접 확인합니다.'
echo '실제 잔여량이 LIVE 보호분보다 충분할 때만 선수집·WORLD 분석을 재개합니다.'
