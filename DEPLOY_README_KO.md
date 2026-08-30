# D.J SPORTS ANALYTICS v1.6 설치 안내

이번 버전은 세 가지를 함께 고쳤습니다.

- 휴대폰과 PC 모두 새로고침해도 로그인이 유지됩니다.
- 관리자가 사진과 글을 넣은 이벤트 팝업을 만들 수 있습니다.
- API 사용량 초기화 시간을 실제 제공업체 기준인 UTC로 맞췄습니다.

기존 회원·게시글·사진 자료가 들어 있는 `users.db`는 그대로 사용합니다.

## 1. GitHub에 올리기

압축을 푼 뒤 아래 항목만 GitHub 저장소 첫 화면에 올립니다.

1. `app.py`
2. `member_system.py`
3. `api_engine.py`
4. `assets` 폴더
5. `DEPLOY_README_KO.md`
6. `STREAMLIT_SECRETS_EXAMPLE.toml`

GitHub에서 `Add file`을 누른 다음 `Upload files`를 누릅니다.
같은 이름이 나오면 덮어쓰고, 화면 아래의 초록색 `Commit changes` 버튼을 누릅니다.

### 절대로 올리지 말아야 하는 파일

- `users.db`를 GitHub에 올리지 마세요.
- 운영 중인 `users.db`를 지우지 마세요.
- 이번 압축파일에는 `users.db`가 들어 있지 않습니다.

## 2. 로그인 유지 확인하기

GitHub 업로드가 끝나고 Streamlit 화면이 다시 열리면 확인합니다.

1. 휴대폰에서 한 번 로그인합니다.
2. 화면을 새로고침합니다.
3. 로그인이 그대로인지 확인합니다.
4. PC에서도 한 번 로그인한 뒤 새로고침합니다.

각 기기에서 처음 한 번은 로그인해야 합니다. 그 뒤에는 최대 30일 동안 새로고침해도
유지되며, 사용자가 `로그아웃`을 눌렀을 때 해당 기기의 로그인이 풀립니다.

로그인한 상태에서는 브라우저의 전체 주소를 다른 사람에게 보내지 마세요.

## 3. 사진 이벤트 팝업 만들기

1. 관리자 계정으로 로그인합니다.
2. 왼쪽의 `관리자 도구`를 엽니다.
3. `공지·팝업 관리`를 엽니다.
4. 표시 방식에서 `팝업`을 선택합니다.
5. 제목과 내용을 적고 JPG·PNG·WEBP 사진을 1장 올립니다.
6. 공개 대상과 기간을 정한 뒤 등록합니다.

방문자가 사이트에 들어오면 사진·제목·내용이 들어간 팝업이 나타납니다.

## 4. AWS 수집 로봇 고치기

GitHub 업로드가 끝난 뒤 AWS 검은 화면에 아래 내용을 한 줄씩 붙여 넣습니다.

```bash
cd /home/ubuntu
```

```bash
pkill -u ubuntu -f '[p]ython3.*collector.py' || true
```

```bash
cp api_engine.py api_engine.py.before_v1_6_20260830
```

```bash
curl -fL 'https://raw.githubusercontent.com/chleowhd77-ops/-/main/api_engine.py?v=20260830v16' -o /tmp/api_engine.py.v16
```

```bash
python3 -m py_compile /tmp/api_engine.py.v16
```

위 명령 뒤에 빨간 오류가 없으면 계속합니다.

```bash
cp /tmp/api_engine.py.v16 /home/ubuntu/api_engine.py
```

```bash
nohup python3 -u collector.py > collector_v1_6.log 2>&1 &
```

```bash
sleep 8
```

```bash
pgrep -af '[p]ython3.*collector.py'
```

```bash
tail -n 80 /home/ubuntu/collector_v1_6.log
```

정상이라면 `python3 -u collector.py`가 한 줄 보이고 수집 로그가 이어집니다.
API 대시보드의 `Requests used` 숫자는 수집이 시작된 뒤 1~3분 안에 올라갈 수 있습니다.
계속 0이면 마지막 AWS 화면을 캡처해서 보내주세요.

## API가 0으로 보였던 이유

이전 코드는 API 하루 한도를 한국시간 자정에 새로 생기는 것으로 판단했습니다.
실제 API-Sports 한도는 UTC 자정, 한국시간 오전 9시에 초기화됩니다. 그래서 한도가
초기화된 뒤에도 로봇이 예전의 `한도 소진` 기록을 보고 호출을 막을 수 있었습니다.
이번 버전은 실제 초기화 시간과 같은 기준으로 계산합니다.

## 이번 수정에서 유지되는 기능

- `오늘의 TOP3`, `프로토 LIVE`, `전체 경기` 메뉴 분리
- `승무패 14`, `채점 노트`, `인증 게시판`
- 회원가입·로그인·후원회원·관리자 등급
- 게시글 사진 첨부와 관리자 공지
- 기존 분석·확률 계산과 마지막 정상 경기 자료 보존

## 상용화 전에 꼭 옮겨야 하는 자료

현재 Streamlit의 회원 데이터는 재배포나 서버 교체 때 사라질 수 있습니다. 실제 회원을
모집하기 전에는 `users.db`와 게시판 사진을 AWS 영구 저장소나 PostgreSQL로 옮겨야 합니다.
