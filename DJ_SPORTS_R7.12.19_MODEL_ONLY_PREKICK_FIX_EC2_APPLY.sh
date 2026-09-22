#!/usr/bin/env bash
# R7.12.19 — 배당/fixture 미수신 시작 전 카드의 팀 데이터 모델픽 복구
# GitHub에 collector.py, app.py, 이 파일을 저장한 뒤 EC2에서 실행합니다.
set -Eeuo pipefail

APP_DIR="/home/ubuntu"
RAW_BASE="https://raw.githubusercontent.com/chleowhd77-ops/-/main"
VERSION="R7.12.19-model-only-prekick-fix"
STAMP="$(date +%Y%m%d_%H%M%S)"
WORK_DIR="$(mktemp -d /tmp/dj-r71219.XXXXXX)"
BACKUP_DIR="${APP_DIR}/backup_r71219_${STAMP}"
COLLECTOR_SERVICE="dj-collector.service"
DB_MAIN="${APP_DIR}/ai_predictions.db"
DB_RUNTIME="${APP_DIR}/api_runtime.db"
DB_MAIN_BEFORE=""
DB_RUNTIME_BEFORE=""

cleanup() {
  case "${WORK_DIR}" in
    /tmp/dj-r71219.*)
      [[ -d "${WORK_DIR}" ]] && rm -rf -- "${WORK_DIR}"
      ;;
  esac
}
trap cleanup EXIT

if [[ -f "${DB_MAIN}" ]]; then DB_MAIN_BEFORE="$(stat -c '%d:%i' "${DB_MAIN}")"; fi
if [[ -f "${DB_RUNTIME}" ]]; then DB_RUNTIME_BEFORE="$(stat -c '%d:%i' "${DB_RUNTIME}")"; fi

echo "[1/5] GitHub 적용 파일을 확인합니다."
curl -fsSL "${RAW_BASE}/collector.py" -o "${WORK_DIR}/collector.py"
curl -fsSL "${RAW_BASE}/app.py" -o "${WORK_DIR}/app.py"

echo "[2/5] 문법을 확인합니다."
python3 -m py_compile "${WORK_DIR}/collector.py" "${WORK_DIR}/app.py"

echo "[3/5] 현재 수집기 코드만 안전하게 백업합니다."
mkdir -p "${BACKUP_DIR}"
cp -p "${APP_DIR}/collector.py" "${BACKUP_DIR}/collector.py"

echo "[4/5] 수집기를 교체하고 재시작합니다."
install -m 0644 "${WORK_DIR}/collector.py" "${APP_DIR}/collector.py"
if ! sudo systemctl restart "${COLLECTOR_SERVICE}"; then
  cp -p "${BACKUP_DIR}/collector.py" "${APP_DIR}/collector.py"
  sudo systemctl restart "${COLLECTOR_SERVICE}" || true
  echo "적용 실패: 이전 collector.py로 복원했습니다." >&2
  exit 1
fi
sleep 3
if ! sudo systemctl is-active --quiet "${COLLECTOR_SERVICE}"; then
  cp -p "${BACKUP_DIR}/collector.py" "${APP_DIR}/collector.py"
  sudo systemctl restart "${COLLECTOR_SERVICE}" || true
  echo "적용 뒤 수집기가 비정상입니다. 이전 collector.py로 복원했습니다." >&2
  exit 1
fi

echo "[5/5] 기록 DB 보존과 서비스 상태를 확인합니다."
if [[ -n "${DB_MAIN_BEFORE}" && "$(stat -c '%d:%i' "${DB_MAIN}")" != "${DB_MAIN_BEFORE}" ]]; then
  echo "경고: ai_predictions.db inode가 달라졌습니다. DB는 교체하지 않았습니다." >&2
fi
if [[ -n "${DB_RUNTIME_BEFORE}" && "$(stat -c '%d:%i' "${DB_RUNTIME}")" != "${DB_RUNTIME_BEFORE}" ]]; then
  echo "경고: api_runtime.db inode가 달라졌습니다. DB는 교체하지 않았습니다." >&2
fi
echo "${VERSION} 적용 완료 · ${COLLECTOR_SERVICE} active · 코드 백업: ${BACKUP_DIR}"
echo "DB inode 유지: ai_predictions=${DB_MAIN_BEFORE:-없음}, runtime=${DB_RUNTIME_BEFORE:-없음}"
