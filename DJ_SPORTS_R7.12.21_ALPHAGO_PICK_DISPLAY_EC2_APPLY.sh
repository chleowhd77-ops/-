#!/usr/bin/env bash
# R7.12.21 — V2 알파고픽 화면 전달
# GitHub main 루트에 collector.py와 이 파일을 올린 뒤 EC2에서 실행합니다.
# 기존 예측 DB, 동결본, 채점·학습 이력과 football_model.py는 바꾸지 않습니다.
set -Eeuo pipefail

APP_DIR="/home/ubuntu"
RAW_BASE="https://raw.githubusercontent.com/chleowhd77-ops/-/main"
VERSION="R7.12.21-alphago-pick-display"
STAMP="$(date +%Y%m%d_%H%M%S)"
WORK_DIR="$(mktemp -d /tmp/dj-r71221.XXXXXX)"
BACKUP_DIR="${APP_DIR}/backup_r71221_${STAMP}"
COLLECTOR_SERVICE="dj-collector.service"
INSTALLED=0

cleanup() {
  case "${WORK_DIR}" in
    /tmp/dj-r71221.*)
      [[ -d "${WORK_DIR}" ]] && rm -rf -- "${WORK_DIR}"
      ;;
  esac
}

restore_previous_collector() {
  if [[ "${INSTALLED}" -eq 1 && -f "${BACKUP_DIR}/collector.py" ]]; then
    cp -p "${BACKUP_DIR}/collector.py" "${APP_DIR}/collector.py"
  fi
}

trap cleanup EXIT

echo "[1/4] GitHub의 새 collector.py를 임시 위치에서 확인합니다."
curl -fsSL --retry 2 "${RAW_BASE}/collector.py" -o "${WORK_DIR}/collector.py"
grep -q 'ALPHAGO_PICK_DISPLAY_VERSION = "v2-ai-display-v1"' "${WORK_DIR}/collector.py"

echo "[2/4] 새 파일의 Python 문법을 먼저 확인합니다."
python3 -m py_compile "${WORK_DIR}/collector.py"

echo "[3/4] 현재 collector.py만 안전하게 백업하고 교체합니다."
mkdir -p "${BACKUP_DIR}"
cp -p "${APP_DIR}/collector.py" "${BACKUP_DIR}/collector.py"
sudo systemctl stop "${COLLECTOR_SERVICE}"
install -m 0644 "${WORK_DIR}/collector.py" "${APP_DIR}/collector.py"
INSTALLED=1

echo "[4/4] 수집기를 다시 시작합니다. 다음 정규 분석부터 V2 결과를 카드에 전달합니다."
if ! sudo systemctl start "${COLLECTOR_SERVICE}"; then
  restore_previous_collector
  sudo systemctl start "${COLLECTOR_SERVICE}" || true
  echo "수집기 시작 실패: 이전 collector.py로 되돌렸습니다." >&2
  exit 1
fi
sleep 5
if ! sudo systemctl is-active --quiet "${COLLECTOR_SERVICE}"; then
  restore_previous_collector
  sudo systemctl restart "${COLLECTOR_SERVICE}" || true
  echo "적용 뒤 수집기가 비정상입니다. 이전 collector.py로 되돌렸습니다." >&2
  exit 1
fi

echo "${VERSION} 적용 완료 · ${COLLECTOR_SERVICE} active · 코드 백업: ${BACKUP_DIR}"
echo "DB·동결픽·채점 이력은 이 스크립트에서 읽거나 교체하지 않았습니다."
