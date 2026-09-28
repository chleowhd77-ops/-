#!/usr/bin/env bash
# R7.12.34 — 공통 분석자료를 T-72부터 선수집, T-30부터 세부자료 갱신
# 변경 범위: collector.py 한 파일. 기존 저장픽/확률/배당/채점/DB/분석 계산식은 변경하지 않습니다.
set -Eeuo pipefail

cd /home/ubuntu
exec 9>/home/ubuntu/.dj_deploy.lock
flock -n 9 || { echo '다른 교체 작업이 실행 중입니다. 잠시 뒤 다시 실행하세요.'; exit 1; }

RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
VERSION='R7.12.34-early-shared-prefetch'
FROM_SYSTEM_VERSION='R7.12.31-50-batch-world-parity-v3-sync'
FROM_COLLECTOR_SHA='936650362810c162f099ef5b8cc9c5395f7be0b3989e057c8fdf794ba3277d1d'
TARGET_COLLECTOR_SHA='b118d1615df7a9048f302fd796813aceb686dd6f3a5766c2c3f106665377acf0'
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /home/ubuntu/update_r71234.XXXXXX)"
BACKUP="/home/ubuntu/backup_r71234_${STAMP}"
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

echo '[1/4] 새 collector.py를 임시 위치에서 검증합니다.'
curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
  "${RAW_BASE}/collector.py?r71234=${STAMP}" -o "$STAGE/collector.py"
echo "$TARGET_COLLECTOR_SHA  $STAGE/collector.py" | sha256sum -c -
python3 -m py_compile "$STAGE/collector.py"
grep -Fq 'COLLECTOR_PATCH_VERSION = "R7.12.34-early-shared-prefetch"' "$STAGE/collector.py"
grep -Fq 'DATA_PREFETCH_EARLY_HORIZON_HOURS' "$STAGE/collector.py"
grep -Fq 'early_core_matches' "$STAGE/collector.py"

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
  echo 'R7.12.34 collector.py가 이미 적용돼 있습니다. 같은 명령을 반복하지 않습니다.'
  exit 0
fi
[[ "$CURRENT_COLLECTOR_SHA" == "$FROM_COLLECTOR_SHA" ]] || {
  echo "현재 collector.py 지문이 R7.12.33과 달라 중단합니다: $CURRENT_COLLECTOR_SHA" >&2; exit 1;
}

echo '[2/4] collector.py만 백업합니다. 운영 DB는 복사·교체하지 않습니다.'
mkdir -p "$BACKUP"
cp -p -- /home/ubuntu/collector.py "$BACKUP/collector.py"
DB_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)"
RUNTIME_INODE_BEFORE=''
[[ -f /home/ubuntu/api_runtime.db ]] && RUNTIME_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)"

echo '[3/4] collector.py 한 파일만 교체하고 전담 수집기를 재시작합니다.'
sudo systemctl stop "$SERVICE"
install -m 0644 "$STAGE/collector.py" /home/ubuntu/collector.py
INSTALLED=1
python3 -m py_compile /home/ubuntu/collector.py
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]
sudo systemctl start "$SERVICE"
sleep 6
sudo systemctl is-enabled --quiet "$SERVICE"
sudo systemctl is-active --quiet "$SERVICE"
[[ "$(sudo systemctl show -p Restart --value "$SERVICE")" == 'always' ]]
MAINPID="$(sudo systemctl show -p MainPID --value "$SERVICE")"
[[ "${MAINPID:-0}" -gt 0 ]]

echo '[4/4] 적용값·DB 보존·서비스 상태를 확인합니다.'
[[ "$(sha256sum /home/ubuntu/collector.py | awk '{print $1}')" == "$TARGET_COLLECTOR_SHA" ]]
grep -F 'COLLECTOR_PATCH_VERSION = "R7.12.34-early-shared-prefetch"' /home/ubuntu/collector.py
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]
if [[ -n "$RUNTIME_INODE_BEFORE" && -f /home/ubuntu/api_runtime.db ]]; then
  [[ "$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)" == "$RUNTIME_INODE_BEFORE" ]]
fi

echo "R7.12.34 적용 완료 · $SERVICE active · MainPID=$MAINPID"
echo "예측 DB 보존 · collector 백업: $BACKUP"
echo '수정 범위: 전담 team worker가 T-72부터 팀 식별·로고·최근전적·폼·상대전적을 공용 캐시에 선수집합니다.'
echo 'T-30 안쪽에서만 배당·세부통계·선수단·순위·부상·예상선발을 기존 정책대로 갱신합니다.'
echo '기존 저장픽·확률·배당·채점·V2/V3/공식/로봇 계산식과 WORLD/API 일일 한도 정책은 이번 적용에서 변경하지 않았습니다.'
echo '재시작 뒤 team worker가 즉시 한 번, 이후 5분마다 실행됩니다. 다음 출력 화면과 웹 두 경기 화면을 보내주세요.'
