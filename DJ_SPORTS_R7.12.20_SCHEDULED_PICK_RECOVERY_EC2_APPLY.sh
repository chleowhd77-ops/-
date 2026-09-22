#!/usr/bin/env bash
# R7.12.20 — 시작 전 저장픽 공통 화면 복구
# GitHub main 루트에 collector.py와 이 파일을 올린 뒤 EC2에서 실행합니다.
# 기존 예측 DB, 동결본, 채점 기록은 읽기만 하며 교체하거나 초기화하지 않습니다.
set -Eeuo pipefail

APP_DIR="/home/ubuntu"
RAW_BASE="https://raw.githubusercontent.com/chleowhd77-ops/-/main"
VERSION="R7.12.20-scheduled-pick-recovery"
STAMP="$(date +%Y%m%d_%H%M%S)"
WORK_DIR="$(mktemp -d /tmp/dj-r71220.XXXXXX)"
BACKUP_DIR="${APP_DIR}/backup_r71220_${STAMP}"
COLLECTOR_SERVICE="dj-collector.service"
DB_MAIN="${APP_DIR}/ai_predictions.db"
DB_RUNTIME="${APP_DIR}/api_runtime.db"
DB_MAIN_BEFORE=""
DB_RUNTIME_BEFORE=""
INSTALLED=0

cleanup() {
  case "${WORK_DIR}" in
    /tmp/dj-r71220.*)
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

if [[ -f "${DB_MAIN}" ]]; then DB_MAIN_BEFORE="$(stat -c '%d:%i' "${DB_MAIN}")"; fi
if [[ -f "${DB_RUNTIME}" ]]; then DB_RUNTIME_BEFORE="$(stat -c '%d:%i' "${DB_RUNTIME}")"; fi

echo "[1/6] GitHub collector.py를 임시 위치에서 확인합니다."
curl -fsSL --retry 2 "${RAW_BASE}/collector.py" -o "${WORK_DIR}/collector.py"

echo "[2/6] 새 파일의 Python 문법을 먼저 확인합니다."
python3 -m py_compile "${WORK_DIR}/collector.py"

echo "[3/6] 현재 수집기 코드만 안전하게 백업합니다."
mkdir -p "${BACKUP_DIR}"
cp -p "${APP_DIR}/collector.py" "${BACKUP_DIR}/collector.py"

echo "[4/6] 수집기를 멈춘 상태에서 코드만 교체합니다."
sudo systemctl stop "${COLLECTOR_SERVICE}"
install -m 0644 "${WORK_DIR}/collector.py" "${APP_DIR}/collector.py"
INSTALLED=1

echo "[5/6] 외부 API 없이 저장된 시작 전 픽을 화면 데이터에 즉시 복구합니다."
if ! python3 "${APP_DIR}/collector.py" --mode recovery; then
  restore_previous_collector
  sudo systemctl start "${COLLECTOR_SERVICE}" || true
  echo "복구 작업 실패: 이전 collector.py로 되돌렸습니다." >&2
  exit 1
fi

echo "[6/6] 정규 수집기를 다시 시작하고 DB 보존을 확인합니다."
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
if [[ -n "${DB_MAIN_BEFORE}" && "$(stat -c '%d:%i' "${DB_MAIN}")" != "${DB_MAIN_BEFORE}" ]]; then
  echo "경고: ai_predictions.db inode가 달라졌습니다. 이 스크립트는 DB를 교체하지 않습니다." >&2
fi
if [[ -n "${DB_RUNTIME_BEFORE}" && "$(stat -c '%d:%i' "${DB_RUNTIME}")" != "${DB_RUNTIME_BEFORE}" ]]; then
  echo "경고: api_runtime.db inode가 달라졌습니다. 이 스크립트는 DB를 교체하지 않습니다." >&2
fi
echo "${VERSION} 적용 완료 · ${COLLECTOR_SERVICE} active · 코드 백업: ${BACKUP_DIR}"
echo "DB inode 유지: ai_predictions=${DB_MAIN_BEFORE:-없음}, runtime=${DB_RUNTIME_BEFORE:-없음}"
