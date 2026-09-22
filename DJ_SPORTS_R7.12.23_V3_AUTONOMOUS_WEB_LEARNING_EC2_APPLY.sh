#!/usr/bin/env bash
# Independent V3 learner only. It does not stop or alter dj-collector.service,
# the official pick, robot pick, V2 pick, or the source prediction database.
set -euo pipefail

ROOT_DIR="/home/ubuntu"
REPO_RAW="https://raw.githubusercontent.com/chleowhd77-ops/-/main"
V3_OUTPUT="${ROOT_DIR}/v3_learning_picks.json"
TEMP_META="$(mktemp /tmp/official_meta_v3.XXXXXX.py)"
TEMP_AUTO="$(mktemp /tmp/official_meta_v3_autopilot.XXXXXX.py)"
TEMP_PUBLISH="$(mktemp /tmp/official_meta_v3_publish.XXXXXX.py)"
TEMP_UNIT="$(mktemp /tmp/dj-v3-learning.XXXXXX.service)"
TEMP_TIMER="$(mktemp /tmp/dj-v3-learning.XXXXXX.timer)"

cleanup() {
  rm -f "${TEMP_META}" "${TEMP_AUTO}" "${TEMP_PUBLISH}" "${TEMP_UNIT}" "${TEMP_TIMER}"
}
trap cleanup EXIT

cd "${ROOT_DIR}"

echo "[1/6] GitHub의 독립 V3 자동학습 파일을 임시로 검사합니다."
curl -fsSL --retry 2 "${REPO_RAW}/official_meta_v3.py" -o "${TEMP_META}"
curl -fsSL --retry 2 "${REPO_RAW}/official_meta_v3_autopilot.py" -o "${TEMP_AUTO}"
curl -fsSL --retry 2 "${REPO_RAW}/official_meta_v3_publish.py" -o "${TEMP_PUBLISH}"
python3 -m py_compile "${TEMP_META}" "${TEMP_AUTO}" "${TEMP_PUBLISH}"

echo "[2/6] 기존 공식픽·로봇·알파고·수집 서비스는 그대로 둡니다."
if [[ ! -f "${ROOT_DIR}/ai_predictions.db" ]]; then
  echo "V3_AUTONOMOUS_DB_NOT_FOUND"
  exit 1
fi

echo "[3/6] 독립 V3 자동학습기만 설치합니다."
cp "${TEMP_META}" "${ROOT_DIR}/official_meta_v3.py"
cp "${TEMP_AUTO}" "${ROOT_DIR}/official_meta_v3_autopilot.py"
cp "${TEMP_PUBLISH}" "${ROOT_DIR}/official_meta_v3_publish.py"

cat > "${TEMP_UNIT}" <<'UNIT'
[Unit]
Description=DJ SPORTS visible V3 autonomous learning picks
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu
ExecStart=/usr/bin/python3 /home/ubuntu/official_meta_v3_autopilot.py --db /home/ubuntu/ai_predictions.db --output /home/ubuntu/v3_learning_picks.json
ExecStartPost=/usr/bin/python3 /home/ubuntu/official_meta_v3_publish.py --file /home/ubuntu/v3_learning_picks.json
UNIT

cat > "${TEMP_TIMER}" <<'TIMER'
[Unit]
Description=Run DJ SPORTS visible V3 autonomous learning picks every six hours

[Timer]
OnCalendar=*-*-* 00,06,12,18:15:00
Persistent=true
RandomizedDelaySec=180
Unit=dj-v3-learning.service

[Install]
WantedBy=timers.target
TIMER

sudo install -m 0644 "${TEMP_UNIT}" /etc/systemd/system/dj-v3-learning.service
sudo install -m 0644 "${TEMP_TIMER}" /etc/systemd/system/dj-v3-learning.timer
sudo systemctl daemon-reload
sudo systemctl enable --now dj-v3-learning.timer

echo "[4/6] V3가 지금 한 번 학습하고 웹 표시용 픽을 만듭니다."
sudo systemctl start dj-v3-learning.service

echo "[5/6] 기존 수집 서비스 상태는 변경 없이 확인만 합니다."
if ! sudo systemctl is-active --quiet dj-collector.service; then
  echo "DJ_COLLECTOR_NOT_ACTIVE"
  sudo systemctl --no-pager --full status dj-collector.service | tail -n 20
  exit 1
fi

echo "[6/6] 자동학습 타이머와 V3 웹 파일을 확인합니다."
sudo systemctl is-active --quiet dj-v3-learning.timer
test -s "${V3_OUTPUT}"
echo "V3_AUTONOMOUS_WEB_LEARNING_READY"
echo "V3_TIMER=active"
echo "V3_OUTPUT=${V3_OUTPUT}"
echo "OFFICIAL_ROBOT_V2_UNCHANGED"
