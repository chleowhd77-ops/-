#!/usr/bin/env bash
# Source-only update. Upload the twenty-two release files to GitHub before running.
set -Eeuo pipefail

APP_ROOT='/home/ubuntu'
RAW_BASE='https://raw.githubusercontent.com/chleowhd77-ops/-/main'
SERVICE='dj-collector.service'
FILES=(collector.py api_engine.py scorecard_core.py manager_investment_autopilot.py official_meta_v3_autopilot.py official_meta_v3.py v2_ml_engine.py analyst_products.py football_model.py learning_state.py learning_worker.py learning_runtime.py runtime_publisher.py manager_investment_publish.py official_meta_v3_publish.py analyst_curriculum.py v2_market_learning.py)
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="$(mktemp -d /tmp/dj-r71313.XXXXXX)"
BACKUP="$APP_ROOT/backup_r71313_$STAMP"
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
    sudo systemctl start "$SERVICE"
    echo "적용 실패: 직전 소스 복원 및 서비스 재시작을 시도했습니다. 백업: $BACKUP" >&2
    sudo systemctl is-active "$SERVICE"
  else
    echo '검증 실패: 운영 소스와 서비스는 변경하지 않았습니다. GitHub에 22개 파일이 함께 올라갔는지 확인하세요.' >&2
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
    "$RAW_BASE/$file?r71313=$STAMP" -o "$STAGE/$file"
done
(
  cd "$STAGE"
  sha256sum -c <<'DJ_SOURCE_HASHES'
11b114d4745e5e7d2ebb47dc2c924e63cc6c9fa73b4b4b1988ad6a7fdd93e8d4  collector.py
543d857f36ebc3e2999b0264ca72c6b1a46f4d23148659e5ec2c683c392c6267  api_engine.py
68500ed018325116518eb7391d74f215131c13b21bbff35eae3b44bf506b8425  scorecard_core.py
4d49d358296e67c7a20f21cb009f664557df2b657cfc936aa2bea4bab6300e8b  manager_investment_autopilot.py
ea5253214c9962902f99086839032313a8c7975093042003c9aee3ad18a1ef6a  official_meta_v3_autopilot.py
226abde276e8da82e57ab61f9981e59fb0a0be433da23913c5a735eb7a6addf5  official_meta_v3.py
19c1c1bc31708105c1999779abb7438e859b288b2a9f44b737fbe8704a588732  v2_ml_engine.py
a21a0f6e1ce98831dcbe0a2fb2cd9ba5b8cd19f128058e264ae2b5863c72edc8  analyst_products.py
a8521a5fdc806c86a4a1ce285b821b289365aec318814e84f0cbd02b95299ff3  football_model.py
4bb1311800dd08d95b194dde5dcd016992809e12ac115ff76b2d2d21bfd8b35e  learning_state.py
6e3162a3dd102476280693d8de082dda2f7e76bc71d6f42a71af0841abb674c7  learning_worker.py
45c4a994e6e153f08644d334756ce481e48888428ccfee9a833ef9fff2b309a1  learning_runtime.py
3dd9ec6deeb667f29abaed973f7c34115d59d93a733507a930ea39b82142b281  runtime_publisher.py
b23da31a1a62fcee0e478789be286dae1d45232e11c5c7adaa382144af9cb869  manager_investment_publish.py
dd5b436233792f9a4d97c2e91271ec2c0bf04c0464779cf9137070e03564883e  official_meta_v3_publish.py
e094181ecad44db91e82f43bb2ce8dfa6482bd2f869eac8f1dbb9f55b87db1ba  analyst_curriculum.py
d34ddeb4e7fec939aab8c1bc71ca30dd54e3395b0d9f4fd8433654dc220263f7  v2_market_learning.py
DJ_SOURCE_HASHES
  python3 -m py_compile "${FILES[@]}"
)

python3 -c 'import sklearn, scipy, pandas, threadpoolctl' || { echo '학습 패키지가 없어 적용을 중단했습니다. 기존 소스는 유지됩니다.' >&2; exit 1; }

echo '데이터 전용 runtime-data 브랜치 준비 (main 웹 코드 보존)'
python3 "$STAGE/runtime_publisher.py"

echo '[2/3] 기존 소스 백업 · 서버 파일 17개 교체'
mkdir -p "$BACKUP"
for file in "${FILES[@]}"; do
  if [[ -f "$APP_ROOT/$file" ]]; then
    cp -p -- "$APP_ROOT/$file" "$BACKUP/$file"
  else
    touch "$BACKUP/$file.absent"
  fi
done
DB_ID="$(stat -c '%d:%i' "$APP_ROOT/ai_predictions.db")"
CHANGED=1
sudo systemctl stop "$SERVICE"
for file in "${FILES[@]}"; do
  install -m 0644 "$STAGE/$file" "$APP_ROOT/$file"
done
[[ "$(stat -c '%d:%i' "$APP_ROOT/ai_predictions.db")" == "$DB_ID" ]]

echo '[3/3] 수집기 재시작'
sudo systemctl start "$SERVICE"
sleep 5
sudo systemctl is-active --quiet "$SERVICE"
echo "R7.13.13 소스 적용 완료 · $SERVICE active · 백업: $BACKUP"
echo 'DB·기존 픽·모델 보존. 자료 복구 대기열 · 학습 재개 · V2 시장 확장 · 문제집 생성 코드 적용.'
echo 'active는 소스 적용 완료입니다. 학습 완료·웹 복구는 각각 로그와 화면에서 확인하세요.'
echo '실시간 로그: sudo journalctl -u dj-collector.service -f -n 100 --no-pager'
