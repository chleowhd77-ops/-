# 회원정보 영구 저장 설정

Streamlit Cloud의 기본 디스크는 재부트 때 초기화됩니다. 회원가입·로그인·등급·게시판 정보를 유지하려면 비공개 AWS S3 버킷을 한 번 연결해야 합니다.

## 1. AWS에서 준비

1. 서울 리전에 비공개 S3 버킷을 만듭니다.
2. 해당 버킷의 지정 파일만 읽고 쓸 수 있는 IAM 사용자를 만듭니다.
3. 액세스 키와 시크릿 키를 발급합니다.

권한 예시는 아래와 같습니다. `버킷이름`만 실제 이름으로 바꿉니다.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::버킷이름/dj-sports/users.db"
    }
  ]
}
```

## 2. Streamlit Secrets에 입력

Streamlit 앱의 `Manage app → Settings → Secrets`에 아래 내용을 추가합니다.

```toml
DJ_MEMBER_S3_BUCKET = "버킷이름"
DJ_MEMBER_S3_KEY = "dj-sports/users.db"
DJ_MEMBER_S3_REGION = "ap-northeast-2"
DJ_MEMBER_S3_ACCESS_KEY_ID = "발급받은 액세스 키"
DJ_MEMBER_S3_SECRET_ACCESS_KEY = "발급받은 시크릿 키"
```

저장 후 앱을 한 번만 재부트합니다. 이후 회원 DB는 비공개 S3에 AES-256 서버 암호화로 저장되며, 앱 재부트 시 자동 복구됩니다. 키 값은 GitHub 코드나 화면에 입력하지 않습니다.
