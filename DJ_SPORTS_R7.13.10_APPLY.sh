#!/usr/bin/env bash
# Source-only update. Upload the twelve release files to GitHub before running.
set -Eeuo pipefail

APP_ROOT='/home/ubuntu'
RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
FILES=(collector.py api_engine.py scorecard_core.py manager_investment_autopilot.py official_meta_v3_autopilot.py official_meta_v3.py v2_ml_engine.py analyst_products.py)
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /tmp/dj-r71310.XXXXXX)"
BACKUP="$APP_ROOT/backup_r71310_$STAMP"
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
    echo '검증 실패: 운영 소스와 서비스는 변경하지 않았습니다. GitHub에 12개 파일이 함께 올라갔는지 확인하세요.' >&2
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
    "$RAW_BASE/$file?r71310=$STAMP" -o "$STAGE/$file"
done
(
  cd "$STAGE"
  sha256sum -c <<'DJ_SOURCE_HASHES'
a9e98dbb60b586dcb845ffdab787ea8fe5ce3f65b7e4929c3c0e6d9d62818c21  collector.py
e3bdfd015d0b48c09f56e74c45ff76d806ebfa6e926ac8c729fdcb95e8b403fa  api_engine.py
20cd104d17ef15d0dab7d1f715fc508965ae943909bf05663cd84d175fc43d09  scorecard_core.py
8c0ba34d2af578d5977b22f110ead0dff13b3323c34c7f40b95d04d696f7dc66  manager_investment_autopilot.py
b43ea580fc077f5a73a67a7a0969425e27662758efc36e598033cab36c045a6b  official_meta_v3_autopilot.py
b872fe2f7ef49aabe9f8aaee1831c338fb6495bf7c9c75a60a373f559dabe726  official_meta_v3.py
e02f4b397c2b827b5cdb75ae3bd8f9ca35ef4e7445fbaf08516a0a185017f3e0  v2_ml_engine.py
c9929cc71e9dba18af388aae0c4e7ff669dfca10fecdc5182e13979b302dbe24  analyst_products.py
DJ_SOURCE_HASHES
  python3 -m py_compile "${FILES[@]}"
)

echo '[2/3] 기존 소스 백업 · 서버 파일 8개 교체'
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
echo "R7.13.10 소스 적용 완료 · $SERVICE active · 백업: $BACKUP"
echo 'DB와 기존 픽 파일은 보존했습니다. 수집 및 분석 완료 후 새 경기 자료와 픽이 웹에 게시됩니다.'
echo '실시간 로그: sudo journalctl -u dj-collector.service -f -n 100 --no-pager'
