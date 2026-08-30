#!/usr/bin/env bash
set -eu

APP_DIR="${1:-/home/ubuntu}"
SERVICE_NAME="dj-collector.service"
SERVICE_SOURCE="$APP_DIR/deploy/$SERVICE_NAME"

if [ ! -f "$APP_DIR/collector.py" ]; then
  echo "ERROR: $APP_DIR/collector.py 파일이 없습니다."
  exit 1
fi

if [ ! -f "$SERVICE_SOURCE" ]; then
  echo "ERROR: $SERVICE_SOURCE 파일이 없습니다."
  exit 1
fi

python3 -m py_compile \
  "$APP_DIR/collector.py" \
  "$APP_DIR/api_engine.py" \
  "$APP_DIR/config.py"

sudo install -m 0644 "$SERVICE_SOURCE" "/etc/systemd/system/$SERVICE_NAME"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

echo
echo "설치 완료: 수집 로봇이 서버 재시작 후에도 자동 실행됩니다."
sudo systemctl --no-pager --full status "$SERVICE_NAME" || true
echo
echo "실시간 로그 보기: sudo journalctl -u $SERVICE_NAME -f"
