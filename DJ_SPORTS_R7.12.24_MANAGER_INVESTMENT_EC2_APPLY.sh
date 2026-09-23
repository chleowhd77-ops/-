#!/usr/bin/env bash
# Independent manager investment ledger only.  It never edits ai_predictions.db,
# customer official picks, robot/V2/V3 picks, Toto14 marks, or dj-collector.service.
set -euo pipefail

ROOT_DIR="/home/ubuntu"
REPO_RAW="https://raw.githubusercontent.com/chleowhd77-ops/-/main"
OUTPUT_FILE="${ROOT_DIR}/manager_investment_picks.json"
TEMP_ENGINE="$(mktemp /tmp/manager_investment_autopilot.XXXXXX.py)"
TEMP_PUBLISH="$(mktemp /tmp/manager_investment_publish.XXXXXX.py)"
TEMP_UNIT="$(mktemp /tmp/dj-manager-investment.XXXXXX.service)"
TEMP_TIMER="$(mktemp /tmp/dj-manager-investment.XXXXXX.timer)"

cleanup() {
  rm -f "${TEMP_ENGINE}" "${TEMP_PUBLISH}" "${TEMP_UNIT}" "${TEMP_TIMER}"
}
trap cleanup EXIT

cd "${ROOT_DIR}"

echo "[1/6] GitHub의 관리자 투자 장부 파일을 임시로 검사합니다."
curl -fsSL --retry 2 "${REPO_RAW}/manager_investment_autopilot.py" -o "${TEMP_ENGINE}"
curl -fsSL --retry 2 "${REPO_RAW}/manager_investment_publish.py" -o "${TEMP_PUBLISH}"
python3 -m py_compile "${TEMP_ENGINE}" "${TEMP_PUBLISH}"

echo "[2/6] 기존 예측 DB와 고객/로봇/V2/V3 픽은 읽기 또는 보존만 합니다."
if [[ ! -f "${ROOT_DIR}/ai_predictions.db" ]]; then
  echo "MANAGER_INVESTMENT_DB_NOT_FOUND"
  exit 1
fi

echo "[3/6] 관리자 전용 장부 작업 파일만 설치합니다."
backup_dir="${ROOT_DIR}/backup_manager_investment_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${backup_dir}"
for file in manager_investment_autopilot.py manager_investment_publish.py; do
  if [[ -f "${ROOT_DIR}/${file}" ]]; then
    cp -p "${ROOT_DIR}/${file}" "${backup_dir}/${file}"
  fi
done
cp "${TEMP_ENGINE}" "${ROOT_DIR}/manager_investment_autopilot.py"
cp "${TEMP_PUBLISH}" "${ROOT_DIR}/manager_investment_publish.py"

cat > "${TEMP_UNIT}" <<'UNIT'
[Unit]
Description=DJ SPORTS independent manager investment ledger
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu
TimeoutStartSec=120
ExecStart=/usr/bin/python3 /home/ubuntu/manager_investment_autopilot.py --db /home/ubuntu/ai_predictions.db --output /home/ubuntu/manager_investment_picks.json
ExecStartPost=/usr/bin/python3 /home/ubuntu/manager_investment_publish.py --file /home/ubuntu/manager_investment_picks.json
UNIT

cat > "${TEMP_TIMER}" <<'TIMER'
[Unit]
Description=Refresh DJ SPORTS manager investment ledger every six hours

[Timer]
OnCalendar=*-*-* 00,06,12,18:25:00
Persistent=true
RandomizedDelaySec=120
Unit=dj-manager-investment.service

[Install]
WantedBy=timers.target
TIMER

sudo install -m 0644 "${TEMP_UNIT}" /etc/systemd/system/dj-manager-investment.service
sudo install -m 0644 "${TEMP_TIMER}" /etc/systemd/system/dj-manager-investment.timer
sudo systemctl daemon-reload
sudo systemctl enable --now dj-manager-investment.timer

echo "[4/6] 관리자 장부를 한 번 생성·게시합니다."
sudo systemctl start dj-manager-investment.service

echo "[5/6] 기존 수집 서비스는 변경 없이 상태만 확인합니다."
if ! sudo systemctl is-active --quiet dj-collector.service; then
  echo "DJ_COLLECTOR_NOT_ACTIVE"
  sudo systemctl --no-pager --full status dj-collector.service | tail -n 20
  exit 1
fi

echo "[6/6] 투자 장부 파일과 자동 갱신 타이머를 확인합니다."
sudo systemctl is-active --quiet dj-manager-investment.timer
test -s "${OUTPUT_FILE}"
echo "MANAGER_INVESTMENT_READY"
echo "MANAGER_INVESTMENT_TIMER=active"
echo "MANAGER_OUTPUT=${OUTPUT_FILE}"
echo "CUSTOMER_OFFICIAL_ROBOT_V2_V3_TOTO14_UNCHANGED"
