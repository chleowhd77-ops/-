#!/usr/bin/env bash
# Source-only update. Upload the six release files to GitHub before running.
set -Eeuo pipefail

APP_ROOT='/home/ubuntu'
RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
COLLECTOR_SHA='7cddbf27aff50571f006c4aea6a7b85393802bb0799394c8b0d6bd58afc22c70'
CORE_SHA='2c5a6c3f49e567ffef94e95446678c8e261f571baa786d2ca703b25b47efea00'
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /tmp/dj-r7137.XXXXXX)"
BACKUP="$APP_ROOT/backup_r7137_$STAMP"
CHANGED=0

cleanup() { rm -rf -- "$STAGE"; }
rollback() {
  trap - ERR
  set +e
  if [[ "$CHANGED" == 1 ]]; then
    sudo systemctl stop "$SERVICE"
    for file in collector.py scorecard_core.py; do
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
    echo '검증 실패: 운영 소스와 서비스는 변경하지 않았습니다. GitHub에 6개 파일이 함께 올라갔는지 확인하세요.' >&2
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
for file in collector.py scorecard_core.py; do
  curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
    "$RAW_BASE/$file?r7137=$STAMP" -o "$STAGE/$file"
done
echo "$COLLECTOR_SHA  $STAGE/collector.py" | sha256sum -c -
echo "$CORE_SHA  $STAGE/scorecard_core.py" | sha256sum -c -
python3 -m py_compile "$STAGE/collector.py" "$STAGE/scorecard_core.py"

echo '[2/3] 기존 소스 백업 · 서버 파일 2개 교체'
mkdir -p "$BACKUP"
for file in collector.py scorecard_core.py; do
  if [[ -f "$APP_ROOT/$file" ]]; then
    cp -p -- "$APP_ROOT/$file" "$BACKUP/$file"
  else
    touch "$BACKUP/$file.absent"
  fi
done
DB_ID="$(stat -c '%d:%i' "$APP_ROOT/ai_predictions.db")"
CHANGED=1
sudo systemctl stop "$SERVICE"
for file in collector.py scorecard_core.py; do
  install -m 0644 "$STAGE/$file" "$APP_ROOT/$file"
done
[[ "$(stat -c '%d:%i' "$APP_ROOT/ai_predictions.db")" == "$DB_ID" ]]

echo '[3/3] 수집기 재시작'
sudo systemctl start "$SERVICE"
sleep 5
sudo systemctl is-active --quiet "$SERVICE"
echo "R7.13.7 소스 적용 완료 · $SERVICE active · 백업: $BACKUP"
echo 'DB와 기존 픽 파일은 교체하지 않았습니다. 새 채점 집계는 score 작업의 게시 완료 후 웹에 반영됩니다.'
echo '실시간 로그: sudo journalctl -u dj-collector.service -f -n 100 --no-pager'
