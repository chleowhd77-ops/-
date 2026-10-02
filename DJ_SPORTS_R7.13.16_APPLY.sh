#!/usr/bin/env bash
# Source update and resume normal collection after API recharge. Upload the twenty-five release files to GitHub before running.
set -Eeuo pipefail

APP_ROOT='/home/ubuntu'
RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
FILES=(collector.py api_engine.py scorecard_core.py manager_investment_autopilot.py official_meta_v3_autopilot.py official_meta_v3.py v2_ml_engine.py analyst_products.py football_model.py learning_state.py learning_worker.py learning_runtime.py runtime_publisher.py manager_investment_publish.py official_meta_v3_publish.py analyst_curriculum.py v2_market_learning.py offline_mode.py learning_audit.py operational_repairs.py)
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /tmp/dj-r71316.XXXXXX)"
BACKUP="$APP_ROOT/backup_r71316_$STAMP"
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
    echo '검증 실패: 운영 소스와 서비스는 변경하지 않았습니다. GitHub에 25개 파일이 함께 올라갔는지 확인하세요.' >&2
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
    "$RAW_BASE/$file?r71316=$STAMP" -o "$STAGE/$file"
done
(
  cd "$STAGE"
  sha256sum -c <<'DJ_SOURCE_HASHES'
d4dd4649186f076ac55c9498812a4ce98328f6c2407d2da49d18e418f26a9491  collector.py
eb3641256bc10726af4df8f7514886540c875d44dd854328dac1a4cc17860d51  api_engine.py
b25e96cba53bac57764e59b33208c20dd80f5788be02b48ec84744bfa2898065  scorecard_core.py
35c70191ed01b5f29bcd4428912f7861327d200bc5cdc6be3db11a79e0ed4e51  manager_investment_autopilot.py
ea5253214c9962902f99086839032313a8c7975093042003c9aee3ad18a1ef6a  official_meta_v3_autopilot.py
88379e61e1435fc549c4d783eb7f452416befd8f24cf4efa74d0500bbd7edd8c  official_meta_v3.py
19c1c1bc31708105c1999779abb7438e859b288b2a9f44b737fbe8704a588732  v2_ml_engine.py
25d5203caca90dc5f8e17ef97e8366aeb8c5d64ba6da08ab9bda9c8b6a51424b  analyst_products.py
226cf6ecc02d95c0687d841f01b5626be2173e36658e0e7bdb665a35f9c72e3e  football_model.py
d0f92e3739dce0f5d51420f227500f4d15db9616e2fad874b0a8ab1c566157f1  learning_state.py
c6be5ad6010b414622b707fb79fe588182b5f77f56e6a04d24efeb5691df478d  learning_worker.py
c86aff00733cf001b0cfd06c8bbaf5c6615ff4264e995f59dd59b8970cfedd40  learning_runtime.py
3cdbe249702dcc240fa1f7ea7768af7ba826becd8e96752e9f86574bc629c184  runtime_publisher.py
b23da31a1a62fcee0e478789be286dae1d45232e11c5c7adaa382144af9cb869  manager_investment_publish.py
dd5b436233792f9a4d97c2e91271ec2c0bf04c0464779cf9137070e03564883e  official_meta_v3_publish.py
4e41024ef9a9cb9bc0797c7e42964e802fae24a8f70b5e75b9a19b52853ec2b8  analyst_curriculum.py
9666c127e9b4efa49d62cf7cdeaf589af4e94ce7ee36fce9e3f6ad60b543df94  v2_market_learning.py
97422e072b50ef658cfc43c19099f2ba18a683373a3b2551f502dd204fc13810  offline_mode.py
e75160c2f2b9496e4b548c7405932d24af9e23a766a6b2d887397279cc52be5f  learning_audit.py
d4087c8e71be8c75dcab695bfec3412babb3ab27fbc42d075681f4d75a69622f  operational_repairs.py
DJ_SOURCE_HASHES
  python3 -m py_compile "${FILES[@]}"
)

python3 -c 'import sklearn, scipy, pandas, threadpoolctl' || { echo '학습 패키지가 없어 적용을 중단했습니다. 기존 소스는 유지됩니다.' >&2; exit 1; }

echo '데이터 전용 runtime-data 브랜치 준비 (main 웹 코드 보존)'
python3 "$STAGE/runtime_publisher.py"

echo '[2/3] 기존 소스 백업 · 서버 파일 20개 교체'
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
echo "R7.13.16 소스 적용 완료 · $SERVICE active · 백업: $BACKUP"
echo 'DB·기존 픽·모델 보존. API 충전 후 일반 수집·학습·채점 모드 적용. 학습 완료는 실시간 로그에서 확인.'
echo 'active는 소스 적용 완료입니다. 학습 완료·웹 복구는 각각 로그와 화면에서 확인하세요.'
echo '실시간 로그: sudo journalctl -u dj-collector.service -f -n 100 --no-pager'
