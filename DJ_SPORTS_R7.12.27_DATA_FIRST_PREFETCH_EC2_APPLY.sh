#!/usr/bin/env bash
# R7.12.27 — 분석자료 선수집 + 배당만으로 임시 최종픽 생성 차단
# GitHub main 루트에 api_engine.py / collector.py / app.py 와 이 파일을 올린 뒤
# EC2에서 이 파일만 한 번 실행합니다.
set -Eeuo pipefail

cd /home/ubuntu
exec 9>/home/ubuntu/.dj_deploy.lock
flock -n 9 || { echo '다른 교체 작업이 실행 중입니다. 잠시 뒤 다시 실행하세요.'; exit 1; }

RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
VERSION='R7.12.27-data-first-prefetch-analysis'
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /home/ubuntu/update_r71227.XXXXXX)"
BACKUP="/home/ubuntu/backup_r71227_${STAMP}"
FILES=(api_engine.py collector.py app.py)
INSTALLED=0

cleanup() { rm -rf -- "$STAGE"; }
rollback() {
  trap - ERR
  set +e
  if [[ "$INSTALLED" -eq 1 ]]; then
    for file in "${FILES[@]}"; do
      [[ -f "$BACKUP/$file" ]] && cp -p -- "$BACKUP/$file" "/home/ubuntu/$file"
    done
  fi
  sudo systemctl restart "$SERVICE" || true
  echo "적용 실패: 기존 코드로 자동 복원했습니다. DB는 변경하지 않았습니다. 백업: $BACKUP" >&2
  exit 1
}
trap cleanup EXIT
trap rollback ERR

echo '[1/4] GitHub 최종 3파일을 임시 위치에서 검증합니다.'
for file in "${FILES[@]}"; do
  curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
    "${RAW_BASE}/${file}?r71227=${STAMP}" -o "$STAGE/$file"
done
(cd "$STAGE" && sha256sum -c - <<'HASHES'
1a6cbefdb80b490e526b9df346a9815fba3bb4dca452b30408e9f1eff7bdbcc1  api_engine.py
5ab4239d45155d4d2c9ddb2469ff272ba585a6e67d352e510b1e8c124998c30e  collector.py
f62e680e4e77cc0bf574fd9c1f43fbb04e621903f07d268fd7591e73476bc688  app.py
HASHES
)
python3 -m py_compile "$STAGE/api_engine.py" "$STAGE/collector.py" "$STAGE/app.py"
grep -Fq 'SYSTEM_VERSION = "R7.12.27-data-first-prefetch-analysis"' "$STAGE/api_engine.py"
grep -Fq 'def _prefetch_upcoming_analysis_inputs' "$STAGE/collector.py"
grep -Fq '분석자료 수집 중' "$STAGE/app.py"

for file in "${FILES[@]}"; do
  [[ -f "/home/ubuntu/$file" ]] || { echo "기존 $file 파일이 없어 중단합니다."; exit 1; }
done
[[ -f /home/ubuntu/ai_predictions.db ]] || { echo '운영 DB가 없어 중단합니다.'; exit 1; }
sudo systemctl cat "$SERVICE" >/dev/null

echo '[2/4] 현재 코드만 백업합니다. 대형 DB 복사는 하지 않습니다.'
mkdir -p "$BACKUP"
for file in "${FILES[@]}"; do cp -p -- "/home/ubuntu/$file" "$BACKUP/$file"; done
DB_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)"
RUNTIME_INODE_BEFORE=''
[[ -f /home/ubuntu/api_runtime.db ]] && RUNTIME_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)"

echo '[3/4] 코드 3개를 교체하고 수집기를 한 번 재시작합니다.'
sudo systemctl stop "$SERVICE"
for file in "${FILES[@]}"; do install -m 0644 "$STAGE/$file" "/home/ubuntu/$file"; done
INSTALLED=1
python3 -m py_compile /home/ubuntu/api_engine.py /home/ubuntu/collector.py /home/ubuntu/app.py
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]
sudo systemctl start "$SERVICE"
sleep 8
sudo systemctl is-active --quiet "$SERVICE"
MAINPID="$(sudo systemctl show -p MainPID --value "$SERVICE")"
[[ "${MAINPID:-0}" -gt 0 ]]

echo '[4/4] 새 버전·DB 보존·선수집 코드 존재를 확인합니다.'
grep -F 'SYSTEM_VERSION = "R7.12.27-data-first-prefetch-analysis"' /home/ubuntu/api_engine.py
grep -F 'def _prefetch_upcoming_analysis_inputs' /home/ubuntu/collector.py >/dev/null
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]
if [[ -n "$RUNTIME_INODE_BEFORE" && -f /home/ubuntu/api_runtime.db ]]; then
  [[ "$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)" == "$RUNTIME_INODE_BEFORE" ]]
fi
trap - ERR

echo "R7.12.27 적용 완료 · $SERVICE active · MainPID=$MAINPID"
echo "예측 DB 보존 · 코드 백업: $BACKUP"
echo '서비스 시작 직후 team 선수집 작업이 자동 대기열에 들어갑니다. 명령을 반복 실행하지 마세요.'
echo '이 마지막 4줄만 캡처해서 보내주세요.'
