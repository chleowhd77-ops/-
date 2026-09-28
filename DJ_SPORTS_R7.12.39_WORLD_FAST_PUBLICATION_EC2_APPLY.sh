#!/usr/bin/env bash
# R7.12.39 — WORLD 최신 일정과 분석 체크포인트를 즉시 웹에 게시
# 교체 파일: collector.py
# 보존 대상: ai_predictions.db, api_runtime.db, 기존 픽·확률·배당·채점 기록
set -Eeuo pipefail

ROOT_DIR='/home/ubuntu'
RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
FROM_COLLECTOR_SHA='e6c814a377f39d9d101eba927e3e72bf1d33722e8e922f24d3f54b87bcdd05af'
TARGET_COLLECTOR_SHA='f575681a9aa36610855e3610666e1be428f838fffdc12bfc5cfd9ca6759a0010'
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /home/ubuntu/update_r71239.XXXXXX)"
BACKUP="/home/ubuntu/backup_r71239_${STAMP}"
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
    [[ -f "$BACKUP/collector.py" ]] && cp -p -- "$BACKUP/collector.py" "$ROOT_DIR/collector.py"
  fi
  sudo systemctl restart "$SERVICE" >/dev/null 2>&1 || true
  echo "적용 실패: collector.py만 직전 백업으로 복원했습니다. DB·기존 픽·채점은 변경하지 않았습니다. 백업: $BACKUP" >&2
  exit 1
}
trap cleanup EXIT
trap rollback ERR

echo '[1/4] GitHub 새 collector 소스를 임시 위치에서 검증합니다.'
curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
  "${RAW_BASE}/collector.py?r71239=${STAMP}" -o "$STAGE/collector.py"
echo "$TARGET_COLLECTOR_SHA  $STAGE/collector.py" | sha256sum -c -
python3 -m py_compile "$STAGE/collector.py"
grep -Fq 'COLLECTOR_PATCH_VERSION = "R7.12.39-world-fast-publication"' "$STAGE/collector.py"
grep -Fq 'def _publish_world_dashboard(reason=""):' "$STAGE/collector.py"
grep -Fq 'WORLD_ANALYSIS_CHECKPOINT_EVERY", "1"' "$STAGE/collector.py"

echo '[2/4] 운영 파일·서비스·DB 보존 조건을 확인합니다.'
[[ -f "$ROOT_DIR/collector.py" ]] || { echo '기존 collector.py가 없어 중단합니다.'; exit 1; }
[[ -f "$ROOT_DIR/ai_predictions.db" && -f "$ROOT_DIR/api_runtime.db" ]] || { echo '운영 DB 또는 API 장부가 없어 중단합니다.'; exit 1; }
sudo systemctl cat "$SERVICE" >/dev/null
CURRENT_COLLECTOR_SHA="$(sha256sum "$ROOT_DIR/collector.py" | awk '{print $1}')"
if [[ "$CURRENT_COLLECTOR_SHA" == "$TARGET_COLLECTOR_SHA" ]]; then
  echo 'R7.12.39가 이미 적용되어 있습니다. 같은 명령을 반복하지 않습니다.'
  exit 0
fi
[[ "$CURRENT_COLLECTOR_SHA" == "$FROM_COLLECTOR_SHA" ]] || { echo "현재 collector.py 지문이 R7.12.38과 달라 중단합니다: $CURRENT_COLLECTOR_SHA" >&2; exit 1; }
PREDICTION_INODE_BEFORE="$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")"
RUNTIME_INODE_BEFORE="$(stat -c '%d:%i' "$ROOT_DIR/api_runtime.db")"

echo '[3/4] collector.py만 백업하고 수집기를 재시작합니다.'
mkdir -p "$BACKUP"
cp -p -- "$ROOT_DIR/collector.py" "$BACKUP/collector.py"
sudo systemctl stop "$SERVICE"
install -m 0644 "$STAGE/collector.py" "$ROOT_DIR/collector.py"
INSTALLED=1
python3 -m py_compile "$ROOT_DIR/collector.py"
[[ "$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")" == "$PREDICTION_INODE_BEFORE" ]]
[[ "$(stat -c '%d:%i' "$ROOT_DIR/api_runtime.db")" == "$RUNTIME_INODE_BEFORE" ]]
sudo systemctl start "$SERVICE"
sleep 8
sudo systemctl is-active --quiet "$SERVICE"

echo '[4/4] 적용값·DB 보존·서비스 상태를 확인합니다.'
[[ "$(sha256sum "$ROOT_DIR/collector.py" | awk '{print $1}')" == "$TARGET_COLLECTOR_SHA" ]]
[[ "$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")" == "$PREDICTION_INODE_BEFORE" ]]
[[ "$(stat -c '%d:%i' "$ROOT_DIR/api_runtime.db")" == "$RUNTIME_INODE_BEFORE" ]]
sudo systemctl is-active --quiet "$SERVICE"

echo "R7.12.39 적용 완료 · $SERVICE active"
echo "DB 보존 · 소스 백업: $BACKUP"
echo 'WORLD는 최신 일정·배당 대기열을 분석 전에 즉시 웹으로 게시합니다.'
echo '분석이 끝난 경기는 한 경기마다 체크포인트로 게시합니다. 미완료 경기는 고객 픽으로 공개하지 않습니다.'
