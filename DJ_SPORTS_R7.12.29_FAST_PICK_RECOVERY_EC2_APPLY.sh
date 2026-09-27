#!/usr/bin/env bash
# R7.12.29 — P0 픽 복구: master 캐시전용 서빙 + PROTO 우선 체크포인트
# GitHub main 루트에 api_engine.py / collector.py / 이 파일을 올린 뒤 EC2에서 이 파일만 한 번 실행합니다.
set -Eeuo pipefail

cd /home/ubuntu
exec 9>/home/ubuntu/.dj_deploy.lock
flock -n 9 || { echo '다른 교체 작업이 실행 중입니다. 잠시 뒤 다시 실행하세요.'; exit 1; }

RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
FROM_VERSION='R7.12.28-batched-master-fair-queue'
VERSION='R7.12.29-fast-pick-recovery'
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /home/ubuntu/update_r71229.XXXXXX)"
BACKUP="/home/ubuntu/backup_r71229_${STAMP}"
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
  curl -fSsL --retry 3 --connect-timeout 15 --max-time 90     "${RAW_BASE}/${file}?r71229=${STAMP}" -o "$STAGE/$file"
done
(cd "$STAGE" && sha256sum -c - <<'HASHES'
4b53e7cbdfe77b9dc7b8327052538e189849e75893159c535ccba1ca82cb6fec  api_engine.py
b92b8ebefa6af7a932904f77e73434790f477335237f308d935212276a74e875  collector.py
HASHES
)
python3 -m py_compile "$STAGE/api_engine.py" "$STAGE/collector.py"
grep -Fq 'SYSTEM_VERSION = "R7.12.29-fast-pick-recovery"' "$STAGE/api_engine.py"
grep -Fq 'def api_cache_only_context' "$STAGE/api_engine.py"
grep -Fq 'MASTER_CACHE_ONLY_SERVING' "$STAGE/collector.py"
grep -Fq 'def _publish_proto_checkpoint' "$STAGE/collector.py"
grep -Fq 'MASTER_SKIP_TOTO_WHILE_PROTO_BACKLOG' "$STAGE/collector.py"

for file in "${FILES[@]}"; do
  [[ -f "/home/ubuntu/$file" ]] || { echo "기존 $file 파일이 없어 중단합니다."; exit 1; }
done
[[ -f /home/ubuntu/ai_predictions.db ]] || { echo '운영 DB가 없어 중단합니다.'; exit 1; }
sudo systemctl cat "$SERVICE" >/dev/null
CURRENT_VERSION="$(grep -oE 'SYSTEM_VERSION = "[^"]+"' /home/ubuntu/api_engine.py | head -1 || true)"
if [[ "$CURRENT_VERSION" != "SYSTEM_VERSION = \"$FROM_VERSION\"" && "$CURRENT_VERSION" != "SYSTEM_VERSION = \"$VERSION\"" ]]; then
  echo "현재 서버 버전이 예상과 달라 중단합니다: $CURRENT_VERSION" >&2
  exit 1
fi

echo '[2/4] 현재 코드만 백업합니다. DB는 복사하지 않습니다.'
mkdir -p "$BACKUP"
for file in "${FILES[@]}"; do cp -p -- "/home/ubuntu/$file" "$BACKUP/$file"; done
DB_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)"
RUNTIME_INODE_BEFORE=''
[[ -f /home/ubuntu/api_runtime.db ]] && RUNTIME_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)"

echo '[3/4] 코드 2개만 교체하고 수집기를 한 번 재시작합니다.'
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

echo '[4/4] 새 버전·DB 보존·P0 픽복구 코드를 확인합니다.'
grep -F 'SYSTEM_VERSION = "R7.12.29-fast-pick-recovery"' /home/ubuntu/api_engine.py
grep -F 'def api_cache_only_context' /home/ubuntu/api_engine.py >/dev/null
grep -F 'MASTER_CACHE_ONLY_SERVING' /home/ubuntu/collector.py >/dev/null
grep -F 'def _publish_proto_checkpoint' /home/ubuntu/collector.py >/dev/null
grep -F 'MASTER_SKIP_TOTO_WHILE_PROTO_BACKLOG' /home/ubuntu/collector.py >/dev/null
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]
if [[ -n "$RUNTIME_INODE_BEFORE" && -f /home/ubuntu/api_runtime.db ]]; then
  [[ "$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)" == "$RUNTIME_INODE_BEFORE" ]]
fi
trap - ERR

echo "R7.12.29 적용 완료 · $SERVICE active · MainPID=$MAINPID"
echo "예측 DB 보존 · 코드 백업: $BACKUP"
echo 'master는 선수집 캐시를 우선 사용하고, 무거운 재학습/API 재수집을 고객 서빙 경로에서 건너뜁니다.'
echo 'PROTO는 최대 12경기씩 처리 후 TOTO14보다 먼저 dashboard_data.json 체크포인트를 게시합니다.'
echo '같은 명령을 반복 실행하지 말고 이 마지막 5줄을 캡처해서 보내주세요.'
