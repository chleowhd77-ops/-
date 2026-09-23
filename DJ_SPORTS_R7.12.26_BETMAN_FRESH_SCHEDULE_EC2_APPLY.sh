#!/usr/bin/env bash
# R7.12.26: retrieve the current Betman sale round before distant future rounds.
# Customer picks, robot/V2/V3/Toto14 logic, frozen records, and DB schema stay intact.
set -euo pipefail

ROOT_DIR="/home/ubuntu"
REPO_RAW="https://raw.githubusercontent.com/chleowhd77-ops/-/main"
COLLECTOR_FILE="${ROOT_DIR}/collector.py"
TEMP_COLLECTOR="$(mktemp /tmp/collector.betman-fresh.XXXXXX.py)"
BACKUP_DIR="${ROOT_DIR}/backup_r71226_betman_fresh_$(date +%Y%m%d_%H%M%S)"

cleanup() {
  rm -f "${TEMP_COLLECTOR}"
}
trap cleanup EXIT

cd "${ROOT_DIR}"

echo "[1/5] GitHub의 배트맨 현재 회차 수집기를 임시 검사합니다."
curl -fsSL --retry 2 "${REPO_RAW}/collector.py" -o "${TEMP_COLLECTOR}"
python3 -m py_compile "${TEMP_COLLECTOR}"

echo "[2/5] 현재 수집기와 기존 배트맨 기록을 안전하게 백업합니다."
test -s "${COLLECTOR_FILE}"
test -s "${ROOT_DIR}/betman_data.json"
mkdir -p "${BACKUP_DIR}"
cp -p "${COLLECTOR_FILE}" "${BACKUP_DIR}/collector.py"
cp -p "${ROOT_DIR}/betman_data.json" "${BACKUP_DIR}/betman_data.before.json"

echo "[3/5] 수집기만 교체합니다. 기존 픽·동결·DB는 변경하지 않습니다."
cp "${TEMP_COLLECTOR}" "${COLLECTOR_FILE}"

echo "[4/5] 수집기를 다시 시작해 현재 배트맨 회차 수집을 예약합니다."
sudo systemctl reset-failed dj-collector.service
sudo systemctl restart dj-collector.service
sleep 6
sudo systemctl is-active --quiet dj-collector.service

echo "[5/5] 시작 상태와 현재 저장자료의 상태 필드를 확인합니다."
python3 - "${ROOT_DIR}/betman_data.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
status = payload.get("source_status") or {}
proto = status.get("proto") or {}
print("BETMAN_DATA_PROTO_COUNT=" + str(len(payload.get("proto_matches") or [])))
print("BETMAN_DATA_COLLECTED_AT=" + str(payload.get("collected_at") or ""))
print("BETMAN_PROTO_LAST_ATTEMPT=" + str(proto.get("last_attempt_at") or ""))
print("BETMAN_PROTO_LAST_SUCCESS=" + str(proto.get("last_success_at") or ""))
print("BETMAN_PROTO_ROUND=" + str(proto.get("round_id") or ""))
PY

echo "BETMAN_FRESH_SCHEDULE_RECOVERY_STARTED"
echo "DJ_COLLECTOR=active"
echo "CODE_BACKUP=${BACKUP_DIR}"
echo "OFFICIAL_ROBOT_V2_V3_TOTO14_FROZEN_HISTORY_UNCHANGED"
