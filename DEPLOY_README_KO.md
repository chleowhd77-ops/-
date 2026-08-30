# D.J SPORTS ANALYTICS V1.8 설치 안내

## 이번 버전에서 바뀐 점

- 세 가지 추천을 서로 다른 기준으로 완전히 분리했습니다.
  - `확률 높은 픽`: 선택 가능한 분석 픽이 있으면 항상 표시됩니다.
  - `AI 꿀픽`: 배당 가치와 신뢰도 기준을 통과한 경우에만 표시됩니다.
  - `VIP 역배 픽`: 엄격한 역배 기준을 통과한 경우에만 표시됩니다.
- 같은 픽이 세 칸에 중복으로 나타나지 않습니다.
- 꿀픽이나 VIP 역배가 기준에 미달하면 억지로 만들지 않고 `오늘은 기준 충족 픽 없음`으로 표시합니다.
- 이 기능 때문에 API를 추가로 호출하지 않습니다. 이미 수집한 분석 결과만 사용합니다.
- 기존 회원가입, 로그인 유지, 관리자 기능, 공지 팝업, 인증 게시판과 사진 업로드 기능은 그대로 유지했습니다.

## 1. GitHub에 올릴 파일

압축을 푼 뒤 아래 파일과 폴더를 GitHub 저장소의 기존 파일 위에 덮어씁니다.

- `app.py`
- `collector.py`
- `member_system.py`
- `api_engine.py`
- `assets` 폴더
- `requirements.txt`
- `packages.txt`

중요: 기존 회원 정보가 들어 있는 `users.db`는 올리거나 덮어쓰면 안 됩니다.

`app.py`는 웹 화면을 바꾸고, `collector.py`는 실제 세 가지 픽을 계산합니다. 따라서 GitHub만 바꾸고 끝내면 안 되고 AWS의 `collector.py`도 아래 순서대로 교체해야 합니다.

## 2. AWS에서 실행할 명령

AWS 검은 화면에 아래 명령을 한 줄씩 붙여 넣고 엔터를 누르세요.

```bash
cd /home/ubuntu
```

```bash
pkill -u ubuntu -f '[p]ython3.*collector.py' || true
```

```bash
cp collector.py collector.py.before_v1_8_20260830
```

```bash
curl -fL 'https://raw.githubusercontent.com/chleowhd77-ops/-/main/collector.py?v=20260830v18' -o /tmp/collector.py.v18
```

```bash
python3 -m py_compile /tmp/collector.py.v18
```

위 명령 뒤에 아무 오류 없이 입력 줄이 다시 나오면 정상입니다. 이어서 실행하세요.

```bash
cp /tmp/collector.py.v18 /home/ubuntu/collector.py
```

```bash
nohup python3 -u collector.py > collector_v1_8.log 2>&1 &
```

```bash
sleep 8
```

```bash
pgrep -af '[p]ython3.*collector.py'
```

숫자와 함께 `python3 -u collector.py`가 나오면 로봇이 실행 중입니다. 감시 화면은 아래 명령으로 켭니다.

```bash
tail -f /home/ubuntu/collector_v1_8.log
```

첫 수집은 20분 이상 걸릴 수 있습니다. 화면에 `GitHub 동기화 완료: dashboard_data.json`이 나오면 웹 새로고침을 해보세요. 감시 화면에서 나오려면 `Ctrl+C`를 한 번 누르면 됩니다. 로봇 자체는 계속 실행됩니다.

## 3. 처음에는 빈 칸이 보일 수 있는 이유

기존 `dashboard_data.json`에는 새 세 가지 분류 정보가 없습니다. 그래서 새 수집이 끝나기 전에는 `확률 높은 픽`만 기존 데이터로 복구하고, 꿀픽과 VIP 역배는 억지로 만들지 않습니다.

AWS 로봇이 새 수집을 한 번 완료하면 세 가지 분류가 정상 적용됩니다.

## 4. 문제가 생겼을 때 원래 버전으로 복구

아래 명령을 한 줄씩 실행하세요.

```bash
cd /home/ubuntu
```

```bash
pkill -u ubuntu -f '[p]ython3.*collector.py' || true
```

```bash
cp collector.py.before_v1_8_20260830 collector.py
```

```bash
nohup python3 -u collector.py > collector_rollback.log 2>&1 &
```

