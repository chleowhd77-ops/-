#!/usr/bin/env bash
# Source update and resume normal collection after API recharge. Upload the twenty-four release files to GitHub before running.
set -Eeuo pipefail

APP_ROOT='/home/ubuntu'
RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
FILES=(collector.py api_engine.py scorecard_core.py manager_investment_autopilot.py official_meta_v3_autopilot.py official_meta_v3.py v2_ml_engine.py analyst_products.py football_model.py learning_state.py learning_worker.py learning_runtime.py runtime_publisher.py manager_investment_publish.py official_meta_v3_publish.py analyst_curriculum.py v2_market_learning.py offline_mode.py learning_audit.py)
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /tmp/dj-r71315.XXXXXX)"
BACKUP="$APP_ROOT/backup_r71315_$STAMP"
CHANGED=0

cleanup() { rm -rf -- "$STAGE"; }
rollback() {
  trap - ERR
  set +e
  if [[ "$CHANGED" == 1 ]]; then
    sudo systemctl stop "$SERVICE"
    for file in "${FILES[@]}"; do
      if [[ -f "$BACKUP/$file" ]]; then
        cp -p -- "$BACKUP/$file" "$APP_ROOT/$file"
      elif [[ -f "$BACKUP/$file.absent" ]]; then
        rm -f -- "$APP_ROOT/$file"
      fi
    done
    if [[ -f "$BACKUP/offline_flag_was_absent" ]]; then
      rm -f -- "$APP_ROOT/.dj_offline_learning"
    else
      touch "$APP_ROOT/.dj_offline_learning"
    fi
    sudo systemctl start "$SERVICE"
    echo "적용 실패: 직전 소스 복원 및 서비스 재시작을 시도했습니다. 백업: $BACKUP" >&2
    sudo systemctl is-active "$SERVICE"
  else
    echo '검증 실패: 운영 소스와 서비스는 변경하지 않았습니다. GitHub에 24개 파일이 함께 올라갔는지 확인하세요.' >&2
  fi
  exit 1
}
trap cleanup EXIT
trap rollback ERR

cd "$APP_ROOT"
exec 9>"$APP_ROOT/.dj_deploy.lock"
flock -n 9
test -f "$APP_ROOT/collector.py"
test -f "$APP_ROOT/ai_predictions.db"
sudo systemctl cat "$SERVICE" >/dev/null

echo '[1/3] GitHub 소스 다운로드 · 버전과 문법 확인'
for file in "${FILES[@]}"; do
  curl -fSsL --retry 3 --connect-timeout 15 --max-time 90 \
    "$RAW_BASE/$file?r71315=$STAMP" -o "$STAGE/$file"
done
(
  cd "$STAGE"
  sha256sum -c <<'DJ_SOURCE_HASHES'
920ed75a34b1c39bf05c47ef788b9397a9a461b860edeebca38b14cf6b71d4dd  collector.py
42c68ddace4c77d303f7a84306fcf0efff9b488e91cfb62484fc96ca94feefe3  api_engine.py
0b2620ad8795969918bc330555b777ab00770db71fc693ec7903623bb5cd6adc  scorecard_core.py
35c70191ed01b5f29bcd4428912f7861327d200bc5cdc6be3db11a79e0ed4e51  manager_investment_autopilot.py
ea5253214c9962902f99086839032313a8c7975093042003c9aee3ad18a1ef6a  official_meta_v3_autopilot.py
226abde276e8da82e57ab61f9981e59fb0a0be433da23913c5a735eb7a6addf5  official_meta_v3.py
19c1c1bc31708105c1999779abb7438e859b288b2a9f44b737fbe8704a588732  v2_ml_engine.py
25d5203caca90dc5f8e17ef97e8366aeb8c5d64ba6da08ab9bda9c8b6a51424b  analyst_products.py
a8521a5fdc806c86a4a1ce285b821b289365aec318814e84f0cbd02b95299ff3  football_model.py
d0f92e3739dce0f5d51420f227500f4d15db9616e2fad874b0a8ab1c566157f1  learning_state.py
45a29210b02c6d18738e56116ceca1c723d93ba14dd0e536277b7cc93b01a716  learning_worker.py
45c4a994e6e153f08644d334756ce481e48888428ccfee9a833ef9fff2b309a1  learning_runtime.py
3dd9ec6deeb667f29abaed973f7c34115d59d93a733507a930ea39b82142b281  runtime_publisher.py
b23da31a1a62fcee0e478789be286dae1d45232e11c5c7adaa382144af9cb869  manager_investment_publish.py
dd5b436233792f9a4d97c2e91271ec2c0bf04c0464779cf9137070e03564883e  official_meta_v3_publish.py
8df8603596eb65a54e8a02bc916b0d9db22854a2b59dafc78ad03fcfa27c87a6  analyst_curriculum.py
d34ddeb4e7fec939aab8c1bc71ca30dd54e3395b0d9f4fd8433654dc220263f7  v2_market_learning.py
97422e072b50ef658cfc43c19099f2ba18a683373a3b2551f502dd204fc13810  offline_mode.py
e75160c2f2b9496e4b548c7405932d24af9e23a766a6b2d887397279cc52be5f  learning_audit.py
DJ_SOURCE_HASHES
  python3 -m py_compile "${FILES[@]}"
)

python3 -c 'import sklearn, scipy, pandas, threadpoolctl' || { echo '학습 패키지가 없어 적용을 중단했습니다. 기존 소스는 유지됩니다.' >&2; exit 1; }

echo '데이터 전용 runtime-data 브랜치 준비 (main 웹 코드 보존)'
python3 "$STAGE/runtime_publisher.py"

echo '[2/3] 기존 소스 백업 · 서버 파일 19개 교체'
mkdir -p "$BACKUP"
for file in "${FILES[@]}"; do
  if [[ -f "$APP_ROOT/$file" ]]; then
    cp -p -- "$APP_ROOT/$file" "$BACKUP/$file"
  else
    touch "$BACKUP/$file.absent"
  fi
done
if [[ ! -f "$APP_ROOT/.dj_offline_learning" ]]; then
  touch "$BACKUP/offline_flag_was_absent"
fi
DB_ID="$(stat -c '%d:%i' "$APP_ROOT/ai_predictions.db")"
CHANGED=1
sudo systemctl stop "$SERVICE"
for file in "${FILES[@]}"; do
  install -m 0644 "$STAGE/$file" "$APP_ROOT/$file"
done
[[ "$(stat -c '%d:%i' "$APP_ROOT/ai_predictions.db")" == "$DB_ID" ]]

rm -f -- "$APP_ROOT/.dj_offline_learning"
echo '[3/3] 일반 수집·학습 모드로 서비스 재시작'
sudo systemctl start "$SERVICE"
sleep 5
sudo systemctl is-active --quiet "$SERVICE"
echo "R7.13.15 소스 적용 완료 · $SERVICE active · 백업: $BACKUP"
echo 'DB·기존 픽·모델 보존. API 충전 후 일반 수집·학습·채점 모드 적용. 학습 완료는 실시간 로그에서 확인.'
echo 'active는 소스 적용 완료입니다. 학습 완료·웹 복구는 각각 로그와 화면에서 확인하세요.'
echo '실시간 로그: sudo journalctl -u dj-collector.service -f -n 100 --no-pager'
