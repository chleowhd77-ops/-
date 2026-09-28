#!/usr/bin/env bash
# R7.13.0 — PROTO/TOTO14 shared dossier, public grading season, WORLD remains paused.
set -Eeuo pipefail

ROOT_DIR='/home/ubuntu'
RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
TARGET_COLLECTOR_SHA='787d83dba4aff74caafd759eebfbf53a6cb1de40097ab1eb476cd397d368cbfd'
TARGET_ENGINE_SHA='8adf13fb3d372e02da223ea78a0dbe8606d5a0596796c612cef86d687db7aad8'
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /home/ubuntu/update_r7130.XXXXXX)"
BACKUP="/home/ubuntu/backup_r7130_${STAMP}"
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
    [[ -f "$BACKUP/api_engine.py" ]] && cp -p -- "$BACKUP/api_engine.py" "$ROOT_DIR/api_engine.py"
  fi
  sudo systemctl restart "$SERVICE" >/dev/null 2>&1 || true
  echo "적용 실패: 소스만 직전 백업으로 복원했습니다. DB·동결픽·채점 원본은 보존됩니다. 백업: $BACKUP" >&2
  exit 1
}
trap cleanup EXIT
trap rollback ERR

echo '[1/4] GitHub R7.13.0 소스를 임시 위치에서 검증합니다.'
curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 "${RAW_BASE}/collector.py?r7130=${STAMP}" -o "$STAGE/collector.py"
curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 "${RAW_BASE}/api_engine.py?r7130=${STAMP}" -o "$STAGE/api_engine.py"
echo "$TARGET_COLLECTOR_SHA  $STAGE/collector.py" | sha256sum -c -
echo "$TARGET_ENGINE_SHA  $STAGE/api_engine.py" | sha256sum -c -
python3 -m py_compile "$STAGE/collector.py" "$STAGE/api_engine.py"
grep -Fq 'R7.13.0-proto-toto14-shared-dossier' "$STAGE/collector.py"
grep -Fq 'R7.13.0-proto-toto14-new-public-season' "$STAGE/api_engine.py"

echo '[2/4] 운영 DB·서비스를 보존 조건으로 확인합니다.'
[[ -f "$ROOT_DIR/collector.py" && -f "$ROOT_DIR/api_engine.py" ]] || { echo '운영 소스가 없어 중단합니다.'; exit 1; }
[[ -f "$ROOT_DIR/ai_predictions.db" && -f "$ROOT_DIR/api_runtime.db" ]] || { echo '운영 DB 또는 API 장부가 없어 중단합니다.'; exit 1; }
sudo systemctl cat "$SERVICE" >/dev/null
PREDICTION_INODE_BEFORE="$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")"
RUNTIME_INODE_BEFORE="$(stat -c '%d:%i' "$ROOT_DIR/api_runtime.db")"

echo '[3/4] 소스만 백업·교체하고 수집기를 재시작합니다.'
mkdir -p "$BACKUP"
cp -p -- "$ROOT_DIR/collector.py" "$BACKUP/collector.py"
cp -p -- "$ROOT_DIR/api_engine.py" "$BACKUP/api_engine.py"
sudo systemctl stop "$SERVICE"
install -m 0644 "$STAGE/collector.py" "$ROOT_DIR/collector.py"
install -m 0644 "$STAGE/api_engine.py" "$ROOT_DIR/api_engine.py"
INSTALLED=1
python3 -m py_compile "$ROOT_DIR/collector.py" "$ROOT_DIR/api_engine.py"
[[ "$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")" == "$PREDICTION_INODE_BEFORE" ]]
[[ "$(stat -c '%d:%i' "$ROOT_DIR/api_runtime.db")" == "$RUNTIME_INODE_BEFORE" ]]
sudo systemctl start "$SERVICE"
sleep 8
sudo systemctl is-active --quiet "$SERVICE"

echo '[4/4] 적용값·DB 보존·서비스 상태를 확인합니다.'
[[ "$(sha256sum "$ROOT_DIR/collector.py" | awk '{print $1}')" == "$TARGET_COLLECTOR_SHA" ]]
[[ "$(sha256sum "$ROOT_DIR/api_engine.py" | awk '{print $1}')" == "$TARGET_ENGINE_SHA" ]]
[[ "$(stat -c '%d:%i' "$ROOT_DIR/ai_predictions.db")" == "$PREDICTION_INODE_BEFORE" ]]
[[ "$(stat -c '%d:%i' "$ROOT_DIR/api_runtime.db")" == "$RUNTIME_INODE_BEFORE" ]]
sudo systemctl is-active --quiet "$SERVICE"

echo "R7.13.0 적용 완료 · $SERVICE active"
echo "DB 보존 · 소스 백업: $BACKUP"
echo 'PROTO LIVE와 승무패14 공용 자료수집·독립 분석을 시작합니다. WORLD는 계속 정지입니다.'
echo '채점 화면은 R7.13 새 공개 시즌 0건부터 집계합니다. 이전 원본은 삭제하지 않습니다.'
