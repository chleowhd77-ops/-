#!/usr/bin/env bash
# R7.12.28 — master 소량 배치 + 공정 대기열
# GitHub main 루트에 api_engine.py / collector.py / 이 파일을 올린 뒤 EC2에서 이 파일만 한 번 실행합니다.
set -Eeuo pipefail

cd /home/ubuntu
exec 9>/home/ubuntu/.dj_deploy.lock
flock -n 9 || { echo '다른 교체 작업이 실행 중입니다. 잠시 뒤 다시 실행하세요.'; exit 1; }

RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
VERSION='R7.12.28-batched-master-fair-queue'
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /home/ubuntu/update_r71228.XXXXXX)"
BACKUP="/home/ubuntu/backup_r71228_${STAMP}"
FILES=(api_engine.py collector.py)
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

echo '[1/4] GitHub 최종 2파일을 임시 위치에서 검증합니다.'
for file in "${FILES[@]}"; do
  curl -fSsL --retry 3 --connect-timeout 15 --max-time 90     "${RAW_BASE}/${file}?r71228=${STAMP}" -o "$STAGE/$file"
done
(cd "$STAGE" && sha256sum -c - <<'HASHES'
01d318f975d9ba987b62927799390968de0aba6dda918acca33f395c359459b8  api_engine.py
917c68f59f619121a7c6ae39c9ce074cc40ebc99aa0c1a19af4e4802f0c3520d  collector.py
HASHES
)
python3 -m py_compile "$STAGE/api_engine.py" "$STAGE/collector.py"
grep -Fq 'SYSTEM_VERSION = "R7.12.28-batched-master-fair-queue"' "$STAGE/api_engine.py"
grep -Fq 'MASTER_ANALYSIS_NEW_MATCHES_PER_PASS' "$STAGE/collector.py"
grep -Fq 'MASTER_PRIORITY_AGE_SECONDS' "$STAGE/collector.py"

for file in "${FILES[@]}"; do
  [[ -f "/home/ubuntu/$file" ]] || { echo "기존 $file 파일이 없어 중단합니다."; exit 1; }
done
[[ -f /home/ubuntu/ai_predictions.db ]] || { echo '운영 DB가 없어 중단합니다.'; exit 1; }
sudo systemctl cat "$SERVICE" >/dev/null

echo '[2/4] 현재 코드만 백업합니다. DB는 복사하지 않습니다.'
mkdir -p "$BACKUP"
for file in "${FILES[@]}"; do cp -p -- "/home/ubuntu/$file" "$BACKUP/$file"; done
DB_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)"
RUNTIME_INODE_BEFORE=''
[[ -f /home/ubuntu/api_runtime.db ]] && RUNTIME_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)"

echo '[3/4] 코드 2개를 교체하고 수집기를 한 번 재시작합니다.'
sudo systemctl stop "$SERVICE"
for file in "${FILES[@]}"; do install -m 0644 "$STAGE/$file" "/home/ubuntu/$file"; done
INSTALLED=1
python3 -m py_compile /home/ubuntu/api_engine.py /home/ubuntu/collector.py
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]
sudo systemctl start "$SERVICE"
sleep 8
sudo systemctl is-active --quiet "$SERVICE"
MAINPID="$(sudo systemctl show -p MainPID --value "$SERVICE")"
[[ "${MAINPID:-0}" -gt 0 ]]

echo '[4/4] 새 버전·DB 보존·배치/대기열 코드를 확인합니다.'
grep -F 'SYSTEM_VERSION = "R7.12.28-batched-master-fair-queue"' /home/ubuntu/api_engine.py
grep -F 'MASTER_ANALYSIS_NEW_MATCHES_PER_PASS' /home/ubuntu/collector.py >/dev/null
grep -F 'MASTER_PRIORITY_AGE_SECONDS' /home/ubuntu/collector.py >/dev/null
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]
if [[ -n "$RUNTIME_INODE_BEFORE" && -f /home/ubuntu/api_runtime.db ]]; then
  [[ "$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)" == "$RUNTIME_INODE_BEFORE" ]]
fi
trap - ERR

echo "R7.12.28 적용 완료 · $SERVICE active · MainPID=$MAINPID"
echo "예측 DB 보존 · 코드 백업: $BACKUP"
echo 'master는 가까운 경기부터 한 번에 최대 6경기만 새 정밀분석하고 화면을 게시합니다.'
echo 'master가 3분 이상 대기하면 새 LIVE/score 요청보다 다음 빈 DB 차례를 먼저 받습니다.'
echo '같은 명령을 반복 실행하지 말고 이 마지막 5줄을 캡처해서 보내주세요.'
