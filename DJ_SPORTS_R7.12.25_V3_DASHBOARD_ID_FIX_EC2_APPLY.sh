#!/usr/bin/env bash
# R7.12.25: make visible V3 picks use the current dashboard match.id.
# This changes neither the customer official pick nor robot/V2/Toto14 data.
set -euo pipefail

ROOT_DIR="/home/ubuntu"
REPO_RAW="https://raw.githubusercontent.com/chleowhd77-ops/-/main"
V3_FILE="${ROOT_DIR}/official_meta_v3_autopilot.py"
V3_OUTPUT="${ROOT_DIR}/v3_learning_picks.json"
DASHBOARD_FILE="${ROOT_DIR}/dashboard_data.json"
TEMP_AUTO="$(mktemp /tmp/official_meta_v3_autopilot.XXXXXX.py)"
BACKUP_DIR="${ROOT_DIR}/backup_r71225_v3_dashboard_id_$(date +%Y%m%d_%H%M%S)"

cleanup() {
  rm -f "${TEMP_AUTO}"
}
trap cleanup EXIT

cd "${ROOT_DIR}"

echo "[1/5] GitHub의 V3 공통 경기 ID 보완 파일을 임시 검사합니다."
curl -fsSL --retry 2 "${REPO_RAW}/official_meta_v3_autopilot.py" -o "${TEMP_AUTO}"
python3 -m py_compile "${TEMP_AUTO}"

echo "[2/5] 시작 전 카드 원본과 기존 V3 장부를 확인합니다."
test -s "${ROOT_DIR}/ai_predictions.db"
test -s "${DASHBOARD_FILE}"
test -f "${V3_FILE}"
mkdir -p "${BACKUP_DIR}"
cp -p "${V3_FILE}" "${BACKUP_DIR}/official_meta_v3_autopilot.py"

echo "[3/5] V3 자동학습기만 교체합니다. 수집기와 고객 픽은 건드리지 않습니다."
cp "${TEMP_AUTO}" "${V3_FILE}"

echo "[4/5] V3를 한 번 실행해 현재 카드 ID로 픽을 고정·게시합니다."
sudo systemctl start dj-v3-learning.service
sudo systemctl is-active --quiet dj-v3-learning.timer
test -s "${V3_OUTPUT}"

echo "[5/5] 현재 시작 전 카드와 V3 장부의 경기 ID 연결을 확인합니다."
python3 - "${DASHBOARD_FILE}" "${V3_OUTPUT}" <<'PY'
import json
import sys
import time

dashboard_path, v3_path = sys.argv[1:]
with open(dashboard_path, encoding="utf-8") as stream:
    dashboard = json.load(stream)
with open(v3_path, encoding="utf-8") as stream:
    v3 = json.load(stream)

now = time.time()
card_ids = set()
for collection in ("proto", "top3"):
    for card in dashboard.get(collection, []) or []:
        if not isinstance(card, dict):
            continue
        match = card.get("match") or {}
        try:
            kickoff = float(card.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if kickoff > 10_000_000_000:
            kickoff /= 1000.0
        if kickoff > now and match.get("id") not in (None, ""):
            card_ids.add(str(match["id"]))
pick_ids = {str(key) for key, value in (v3.get("picks") or {}).items() if isinstance(value, dict)}
matched = sorted(card_ids & pick_ids)
print(f"V3_CURRENT_CARD_COUNT={len(card_ids)}")
print(f"V3_CURRENT_CARD_MATCHED_PICK_COUNT={len(matched)}")
print("V3_CURRENT_CARD_MATCHED_IDS=" + ",".join(matched))
if card_ids and not matched:
    raise SystemExit("V3_DASHBOARD_ID_MATCH_FAILED")
PY

if ! sudo systemctl is-active --quiet dj-collector.service; then
  echo "DJ_COLLECTOR_NOT_ACTIVE"
  sudo systemctl --no-pager --full status dj-collector.service | tail -n 20
  exit 1
fi

echo "V3_DASHBOARD_ID_FIX_READY"
echo "V3_TIMER=active"
echo "V3_OUTPUT=${V3_OUTPUT}"
echo "V3_CODE_BACKUP=${BACKUP_DIR}"
echo "OFFICIAL_ROBOT_V2_TOTO14_UNCHANGED"
