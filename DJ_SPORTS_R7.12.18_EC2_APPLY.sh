#!/usr/bin/env bash
set -Eeuo pipefail

cd /home/ubuntu
exec 9>/home/ubuntu/.dj_deploy.lock
flock -n 9 || { echo '다른 교체 작업이 실행 중입니다. 잠시 뒤 다시 실행하세요.'; exit 1; }

files=(api_engine.py collector.py)
service_name='dj-collector.service'
stage=$(mktemp -d /home/ubuntu/update_r71218.XXXXXX)
backup=$(mktemp -d /home/ubuntu/backup_r71218.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT

echo '1/3 GitHub에 올린 픽 표시 수정 파일을 확인합니다.'
verified=0
for attempt in 1 2 3 4; do
  download_ok=1
  for file in "${files[@]}"; do
    if ! curl -fSsL --connect-timeout 15 --max-time 90 \
      "https://raw.githubusercontent.com/chleowhd77-ops/-/main/$file?r71218=$(date +%s)-$attempt" \
      -o "$stage/$file"; then
      download_ok=0
      break
    fi
  done
  if [ "$download_ok" -eq 1 ] && (cd "$stage" && sha256sum -c - <<'HASHES'
72a14177158271f485e4b849ceedbd3c8b27f1b3c21564dbea193182a58e91a4  api_engine.py
65823476768e967c3aa6836e11422fc839c1d64604afd5ec4ff86151ee64f1df  collector.py
HASHES
  ); then
    verified=1
    break
  fi
  [ "$attempt" -eq 4 ] || sleep 5
done
[ "$verified" -eq 1 ] || {
  echo 'GitHub 파일이 최종 전달본과 다릅니다. 기존 코드는 변경하지 않았습니다.'
  exit 1
}
python3 -m py_compile "$stage/api_engine.py" "$stage/collector.py"
grep -Fq 'def _proto_market_preview_item' "$stage/collector.py"
grep -Fq 'def queue_team_identity_retry' "$stage/api_engine.py"

for file in "${files[@]}"; do
  [ -f "/home/ubuntu/$file" ] || { echo "기존 $file 파일이 없어 중단합니다."; exit 1; }
  cp -p -- "/home/ubuntu/$file" "$backup/$file"
done
[ -f /home/ubuntu/ai_predictions.db ] || { echo '운영 DB가 없어 중단합니다.'; exit 1; }
sudo systemctl cat "$service_name" >/dev/null

if (cd /home/ubuntu && sha256sum -c --status - <<'HASHES'
72a14177158271f485e4b849ceedbd3c8b27f1b3c21564dbea193182a58e91a4  api_engine.py
65823476768e967c3aa6836e11422fc839c1d64604afd5ec4ff86151ee64f1df  collector.py
HASHES
); then
  echo '이미 같은 픽 표시 수정본이 적용되어 있습니다. 재시작하지 않고 끝냅니다.'
  exit 0
fi

rollback() {
  trap - ERR
  set +e
  for file in "${files[@]}"; do
    cp -p -- "$backup/$file" "/home/ubuntu/$file"
  done
  sudo systemctl restart "$service_name"
  echo "적용 실패로 기존 코드만 복원했습니다. DB는 변경하지 않았습니다. 백업: $backup"
  exit 1
}
trap rollback ERR

echo '2/3 픽 표시 관련 코드 2개만 교체하고 서비스를 한 번 재시작합니다.'
db_inode_before=$(stat -c '%i' /home/ubuntu/ai_predictions.db)
for file in "${files[@]}"; do
  install -m 0644 "$stage/$file" "/home/ubuntu/$file"
done
[ "$(stat -c '%i' /home/ubuntu/ai_predictions.db)" = "$db_inode_before" ]
sudo systemctl restart "$service_name"
sleep 8
sudo systemctl is-active --quiet "$service_name"

echo '3/3 서비스와 DB 보존을 확인합니다.'
grep -F 'def _proto_market_preview_item' /home/ubuntu/collector.py
[ "$(stat -c '%i' /home/ubuntu/ai_predictions.db)" = "$db_inode_before" ]
main_pid=$(sudo systemctl show -p MainPID --value "$service_name")
[ "${main_pid:-0}" -gt 0 ]
trap - ERR

echo "R7.12.18 픽 표시 수정 적용 완료 · 서비스 active · MainPID=$main_pid"
echo "DB·과거 픽·확률·배당·채점은 변경하지 않았습니다. 코드 백업: $backup"
echo '마지막 세 줄만 캡처해서 보내주세요. 오류가 나오면 명령을 반복하지 마세요.'
