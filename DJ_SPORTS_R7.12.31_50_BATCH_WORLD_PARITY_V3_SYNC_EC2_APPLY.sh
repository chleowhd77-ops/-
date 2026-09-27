#!/usr/bin/env bash
# R7.12.31 — PROTO/WORLD 동일 정밀분석 + 최대 50경기 자동브레이크 + V3 카드 동기화
# GitHub main 루트에 이 스크립트와 아래 5개 Python 파일을 올린 뒤 EC2에서 한 번만 실행합니다.
set -Eeuo pipefail

cd /home/ubuntu
exec 9>/home/ubuntu/.dj_deploy.lock
flock -n 9 || { echo '다른 교체 작업이 실행 중입니다. 잠시 뒤 다시 실행하세요.'; exit 1; }

RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
V3_SERVICE='dj-v3-learning.service'
V3_TIMER='dj-v3-learning.timer'
FROM_VERSION='R7.12.30-score-release-dispatch'
VERSION='R7.12.31-50-batch-world-parity-v3-sync'
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /home/ubuntu/update_r71231.XXXXXX)"
BACKUP="/home/ubuntu/backup_r71231_${STAMP}"
FILES=(api_engine.py collector.py app.py official_meta_v3_autopilot.py official_meta_v3_publish.py)
INSTALLED=0
V3_UNIT_EXISTED=0
V3_TIMER_EXISTED=0

cleanup() { rm -rf -- "$STAGE"; }
rollback() {
  trap - ERR
  set +e
  if [[ "$INSTALLED" -eq 1 ]]; then
    sudo systemctl stop "$V3_TIMER" "$V3_SERVICE" >/dev/null 2>&1 || true
    sudo systemctl stop "$SERVICE" >/dev/null 2>&1 || true
    for file in "${FILES[@]}"; do
      [[ -f "$BACKUP/$file" ]] && cp -p -- "$BACKUP/$file" "/home/ubuntu/$file"
    done
    if [[ "$V3_UNIT_EXISTED" -eq 1 && -f "$BACKUP/dj-v3-learning.service" ]]; then
      sudo cp -p -- "$BACKUP/dj-v3-learning.service" /etc/systemd/system/dj-v3-learning.service
    elif [[ "$V3_UNIT_EXISTED" -eq 0 ]]; then
      sudo rm -f /etc/systemd/system/dj-v3-learning.service
    fi
    if [[ "$V3_TIMER_EXISTED" -eq 1 && -f "$BACKUP/dj-v3-learning.timer" ]]; then
      sudo cp -p -- "$BACKUP/dj-v3-learning.timer" /etc/systemd/system/dj-v3-learning.timer
    elif [[ "$V3_TIMER_EXISTED" -eq 0 ]]; then
      sudo rm -f /etc/systemd/system/dj-v3-learning.timer
    fi
    sudo systemctl daemon-reload || true
  fi
  sudo systemctl restart "$SERVICE" || true
  if [[ -f /etc/systemd/system/dj-v3-learning.timer ]]; then
    sudo systemctl enable --now "$V3_TIMER" >/dev/null 2>&1 || true
  fi
  echo "적용 실패: 기존 코드/타이머로 자동 복원했습니다. DB는 변경하지 않았습니다. 백업: $BACKUP" >&2
  exit 1
}
trap cleanup EXIT
trap rollback ERR

echo '[1/6] GitHub 최종 5파일을 임시 위치에서 검증합니다.'
for file in "${FILES[@]}"; do
  curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
    "${RAW_BASE}/${file}?r71231=${STAMP}" -o "$STAGE/$file"
done
(cd "$STAGE" && sha256sum -c - <<'HASHES'
13ca4b4d1c6843c6b318a5a5d23ce419b6d091eb64eb192a09e26446d4003164  api_engine.py
bb46d0b0fbf5352d57b8a35162141cf14ca4babd42cb5e7575f5bc7ea2fd9be5  collector.py
d0ba3444bfb8b04f404b8033f7bd6a030c943b76a21b81e4256692514d3fe8c5  app.py
b3201ac9e0cc6d740eace42827cc5a1ba9e1eb816793e0af30f00face2e38811  official_meta_v3_autopilot.py
aa02de54bfdbb6e4a1b94594d06bdac11345f7fb43bc3da575711832730d4423  official_meta_v3_publish.py
HASHES
)
python3 -m py_compile \
  "$STAGE/api_engine.py" "$STAGE/collector.py" "$STAGE/app.py" \
  "$STAGE/official_meta_v3_autopilot.py" "$STAGE/official_meta_v3_publish.py"
grep -Fq 'SYSTEM_VERSION = "R7.12.31-50-batch-world-parity-v3-sync"' "$STAGE/api_engine.py"
grep -Fq 'MASTER_ANALYSIS_NEW_MATCHES_PER_PASS = 50' "$STAGE/collector.py"
grep -Fq 'WORLD_ANALYSIS_NEW_MATCHES_PER_PASS = 50' "$STAGE/collector.py"
grep -Fq 'WORLD_ANALYSIS_VERSION = ANALYSIS_VERSION' "$STAGE/collector.py"
grep -Fq 'near_kickoff_context = True' "$STAGE/collector.py"
grep -Fq 'WORLD 우선 게시 체크포인트' "$STAGE/collector.py"
grep -Fq 'world_dashboard_card' "$STAGE/official_meta_v3_autopilot.py"
grep -Fq 'V3_WEB_LEARNING_UNCHANGED' "$STAGE/official_meta_v3_publish.py"
grep -Fq 'world_dashboard_data.get("matches"' "$STAGE/app.py"

for file in "${FILES[@]}"; do
  [[ -f "/home/ubuntu/$file" ]] || { echo "기존 $file 파일이 없어 중단합니다."; exit 1; }
done
[[ -f /home/ubuntu/official_meta_v3.py ]] || { echo '기존 official_meta_v3.py가 없어 중단합니다.'; exit 1; }
[[ -f /home/ubuntu/ai_predictions.db ]] || { echo '운영 DB가 없어 중단합니다.'; exit 1; }
[[ -f /home/ubuntu/dashboard_data.json ]] || { echo 'dashboard_data.json이 없어 중단합니다.'; exit 1; }
[[ -f /home/ubuntu/world_dashboard.json ]] || { echo 'world_dashboard.json이 없어 중단합니다.'; exit 1; }
sudo systemctl cat "$SERVICE" >/dev/null
CURRENT_VERSION="$(grep -oE 'SYSTEM_VERSION = "[^"]+"' /home/ubuntu/api_engine.py | head -1 || true)"
if [[ "$CURRENT_VERSION" != "SYSTEM_VERSION = \"$FROM_VERSION\"" && "$CURRENT_VERSION" != "SYSTEM_VERSION = \"$VERSION\"" ]]; then
  echo "현재 서버 버전이 예상과 달라 중단합니다: $CURRENT_VERSION" >&2
  exit 1
fi

echo '[2/6] 현재 코드와 V3 systemd 설정만 백업합니다. DB는 복사하지 않습니다.'
mkdir -p "$BACKUP"
for file in "${FILES[@]}"; do cp -p -- "/home/ubuntu/$file" "$BACKUP/$file"; done
if [[ -f /etc/systemd/system/dj-v3-learning.service ]]; then
  V3_UNIT_EXISTED=1
  sudo cp -p /etc/systemd/system/dj-v3-learning.service "$BACKUP/dj-v3-learning.service"
fi
if [[ -f /etc/systemd/system/dj-v3-learning.timer ]]; then
  V3_TIMER_EXISTED=1
  sudo cp -p /etc/systemd/system/dj-v3-learning.timer "$BACKUP/dj-v3-learning.timer"
fi
DB_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)"
RUNTIME_INODE_BEFORE=''
[[ -f /home/ubuntu/api_runtime.db ]] && RUNTIME_INODE_BEFORE="$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)"

cat > "$STAGE/dj-v3-learning.service" <<'UNIT'
[Unit]
Description=DJ SPORTS V3 learning picks - PROTO/WORLD/TOTO14
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=300
ExecStart=/usr/bin/python3 /home/ubuntu/official_meta_v3_autopilot.py --db /home/ubuntu/ai_predictions.db --output /home/ubuntu/v3_learning_picks.json --dashboard /home/ubuntu/dashboard_data.json --world-dashboard /home/ubuntu/world_dashboard.json
ExecStartPost=/usr/bin/python3 /home/ubuntu/official_meta_v3_publish.py --file /home/ubuntu/v3_learning_picks.json
UNIT

cat > "$STAGE/dj-v3-learning.timer" <<'TIMER'
[Unit]
Description=Refresh DJ SPORTS V3 card picks every 10 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Persistent=true
RandomizedDelaySec=20
Unit=dj-v3-learning.service

[Install]
WantedBy=timers.target
TIMER

echo '[3/6] 코드 5개와 V3 카드동기화 타이머만 교체합니다.'
sudo systemctl stop "$V3_TIMER" "$V3_SERVICE" >/dev/null 2>&1 || true
sudo systemctl stop "$SERVICE"
for file in "${FILES[@]}"; do install -m 0644 "$STAGE/$file" "/home/ubuntu/$file"; done
sudo install -m 0644 "$STAGE/dj-v3-learning.service" /etc/systemd/system/dj-v3-learning.service
sudo install -m 0644 "$STAGE/dj-v3-learning.timer" /etc/systemd/system/dj-v3-learning.timer
INSTALLED=1
python3 -m py_compile \
  /home/ubuntu/api_engine.py /home/ubuntu/collector.py /home/ubuntu/app.py \
  /home/ubuntu/official_meta_v3_autopilot.py /home/ubuntu/official_meta_v3_publish.py
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]
sudo systemctl daemon-reload
sudo systemctl start "$SERVICE"
sudo systemctl enable --now "$V3_TIMER" >/dev/null
sleep 8
sudo systemctl is-active --quiet "$SERVICE"
sudo systemctl is-active --quiet "$V3_TIMER"
MAINPID="$(sudo systemctl show -p MainPID --value "$SERVICE")"
[[ "${MAINPID:-0}" -gt 0 ]]

echo '[4/6] 실제 적용값과 DB 보존을 확인합니다.'
grep -F 'SYSTEM_VERSION = "R7.12.31-50-batch-world-parity-v3-sync"' /home/ubuntu/api_engine.py
python3 - <<'PY'
import collector
assert collector.MASTER_ANALYSIS_NEW_MATCHES_PER_PASS == 50
assert collector.WORLD_ANALYSIS_NEW_MATCHES_PER_PASS == 50
assert collector.DATA_PREFETCH_MATCH_LIMIT >= 50
assert collector.WORLD_ANALYSIS_INTERVAL_MINUTES == 5
assert collector.WORLD_ANALYSIS_VERSION == collector.ANALYSIS_VERSION
print('PROTO_MAX_PER_PASS=50')
print('WORLD_MAX_PER_PASS=50')
print('WORLD_ANALYSIS_INTERVAL_MINUTES=5')
print('PROTO_WORLD_ANALYSIS_VERSION=' + str(collector.ANALYSIS_VERSION))
PY
[[ "$(stat -c '%d:%i' /home/ubuntu/ai_predictions.db)" == "$DB_INODE_BEFORE" ]]
if [[ -n "$RUNTIME_INODE_BEFORE" && -f /home/ubuntu/api_runtime.db ]]; then
  [[ "$(stat -c '%d:%i' /home/ubuntu/api_runtime.db)" == "$RUNTIME_INODE_BEFORE" ]]
fi

# Essential collector deployment is now safe. A transient V3 GitHub publish
# failure must not roll back working customer picks or touch the prediction DB.
trap - ERR

echo '[5/6] V3를 한 번 즉시 갱신합니다. 실패해도 수집기/DB는 되돌리지 않습니다.'
V3_REFRESH='warning'
if sudo systemctl start "$V3_SERVICE"; then
  V3_REFRESH='ok'
else
  echo '⚠️ V3 즉시 갱신은 실패했습니다. 10분 타이머가 자동 재시도합니다.'
fi

echo '[6/6] 현재 시작 전 카드와 V3 장부 연결 수를 읽기 전용으로 확인합니다.'
if [[ -s /home/ubuntu/v3_learning_picks.json ]]; then
python3 - <<'PY'
import json,time
from pathlib import Path
now=time.time()
def load(path):
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception: return {}
v3=load('/home/ubuntu/v3_learning_picks.json')
pick_ids={str(k) for k,v in (v3.get('picks') or {}).items() if isinstance(v,dict)}
proto=load('/home/ubuntu/dashboard_data.json')
world=load('/home/ubuntu/world_dashboard.json')
proto_ids=set()
for name in ('proto','top3'):
    for card in proto.get(name,[]) or []:
        if not isinstance(card,dict): continue
        match=card.get('match') or {}
        try: kickoff=float(card.get('timestamp') or 0)
        except Exception: continue
        if kickoff>10_000_000_000: kickoff/=1000.0
        if kickoff>now and match.get('id') not in (None,''):
            proto_ids.add(str(match.get('id')))
world_ids=set()
for card in world.get('matches',[]) or []:
    if not isinstance(card,dict): continue
    match=card.get('match') or {}; analysis=card.get('analysis') or {}
    if not isinstance(analysis,dict) or str(analysis.get('analysis_stage') or card.get('analysis_stage') or '') in ('','market-preview'):
        continue
    try: kickoff=float(card.get('timestamp') or 0)
    except Exception: continue
    if kickoff>10_000_000_000: kickoff/=1000.0
    if kickoff>now and match.get('id') not in (None,''):
        world_ids.add(str(match.get('id')))
print(f'V3_PROTO_FUTURE_CARDS={len(proto_ids)}')
print(f'V3_PROTO_MATCHED={len(proto_ids & pick_ids)}')
print(f'V3_WORLD_ANALYZED_FUTURE_CARDS={len(world_ids)}')
print(f'V3_WORLD_MATCHED={len(world_ids & pick_ids)}')
print('V3_STATUS=' + str(v3.get('status') or 'UNKNOWN'))
PY
else
  echo 'V3_STATUS=NO_OUTPUT_YET'
fi

echo "R7.12.31 적용 완료 · $SERVICE active · MainPID=$MAINPID"
echo "예측 DB 보존 · 코드/타이머 백업: $BACKUP"
echo "V3 즉시갱신=$V3_REFRESH · V3 타이머=10분"
echo 'PROTO/WORLD는 각각 한 주기 최대 50경기이며, 시간/메모리 브레이크가 먼저 걸리면 다음 5분 차례에서 자동 재개합니다.'
echo 'PROTO와 WORLD는 같은 ANALYSIS_VERSION/full-context 정책을 사용하고 WORLD도 10경기마다 중간 게시합니다.'
echo '같은 적용 명령을 반복 실행하지 말고 이 마지막 출력 화면을 보내주세요.'
