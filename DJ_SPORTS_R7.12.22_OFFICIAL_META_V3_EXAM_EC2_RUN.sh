#!/usr/bin/env bash
# Run only the offline V3 challenger audit/exam.  This script deliberately
# does not stop services, change collector/app/model code, install packages,
# or write to the prediction database.
set -euo pipefail

ROOT_DIR="/home/ubuntu"
REPO_RAW="https://raw.githubusercontent.com/chleowhd77-ops/-/main"
STAMP="$(date +%Y%m%d_%H%M%S)"
TEMP_FILE="$(mktemp /tmp/official_meta_v3.XXXXXX.py)"
REPORT_DIR="${ROOT_DIR}/official_meta_v3_reports"
REPORT_FILE="${REPORT_DIR}/official_meta_v3_${STAMP}.json"

cleanup() {
  rm -f "${TEMP_FILE}"
}
trap cleanup EXIT

cd "${ROOT_DIR}"
mkdir -p "${REPORT_DIR}"

echo "[1/4] GitHub에서 독립 V3 시험기를 임시로 확인합니다."
curl -fsSL --retry 2 "${REPO_RAW}/official_meta_v3.py" -o "${TEMP_FILE}"
if ! python3 -m py_compile "${TEMP_FILE}"; then
  echo "V3_EXAM_SOURCE_SYNTAX_FAILED"
  exit 1
fi

echo "[2/4] 운영 DB는 읽기 전용으로만 사용합니다."
if [[ ! -f "${ROOT_DIR}/ai_predictions.db" ]]; then
  echo "V3_EXAM_DB_NOT_FOUND"
  exit 1
fi

TEXTBOOK_ARGS=()
if [[ -f "${ROOT_DIR}/master_training_data.csv" ]]; then
  TEXTBOOK_ARGS=(--textbook "${ROOT_DIR}/master_training_data.csv")
  echo "[3/4] V2 3년 교과서 열/기간도 읽기 전용으로 점검합니다."
else
  echo "[3/4] V2 교과서 파일은 발견되지 않았습니다. DB 스냅샷 시험만 진행합니다."
fi

echo "[4/4] 시간순 V3 학습·연습시험·봉인 최종시험을 실행합니다."
python3 "${TEMP_FILE}" \
  --db "${ROOT_DIR}/ai_predictions.db" \
  "${TEXTBOOK_ARGS[@]}" \
  --report "${REPORT_FILE}"

python3 - "${REPORT_FILE}" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
exam = report.get("exam") or {}
final = exam.get("final") or {}
print("V3_REPORT=", sys.argv[1])
print("V3_STATUS=", exam.get("status"))
print("V3_QUALIFICATION=", exam.get("qualification", "NOT_EXAMINED"))
if final:
    print("V3_FINAL_MATCHES=", final.get("matches"))
    print("V3_FINAL_ACCURACY=", final.get("meta_accuracy"))
    print("V3_CURRENT_OFFICIAL_ACCURACY=", final.get("baseline_accuracy"))
print("V3_PROMOTION=", exam.get("promotion", "KEEP_CURRENT_OFFICIAL_PICK"))
PY

echo "V3_EXAM_COMPLETE_NO_LIVE_PICK_CHANGED"
