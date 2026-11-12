# 유저스터디 배포 준비

현재 저장소에 제안 시스템과 baseline을 함께 배포할 Render Blueprint를 준비했습니다. 배포가 만들어지면 서로 다른 HTTPS 주소와 영구 저장 디스크를 사용하는 두 웹 서비스가 생성됩니다.

## 현재 상태

- `render.yaml`: 제안 시스템과 baseline을 별도 서비스로 정의합니다.
- `baseline/`: baseline 실행 코드 사본입니다. Git 이력, `.env`, SQLite 데이터는 포함하지 않았습니다.
- 각 서비스는 `/var/data` 디스크에 SQLite 기록과 세션 서명 키를 보관하도록 설정합니다.
- Gemini/OpenAlex 키는 Render 서비스 환경변수로 입력하며 소스 코드나 브라우저로 전달하지 않습니다.
- 각 조건은 접속 코드를 입력해야 쓸 수 있도록 잠금 화면을 포함합니다.
- 자동 배포는 꺼 두었습니다. 스터디 중 코드가 커밋될 때 참가자 화면이 예고 없이 바뀌지 않게 하기 위해서입니다.

## Render 계정에서 필요한 마지막 단계

1. Render에서 **New → Blueprint**를 열고 GitHub 저장소 `beblack127/HCI_Korea_2027`의 `main` 브랜치를 연결합니다.
2. `render.yaml`을 확인하고 두 서비스가 포함된 Blueprint를 생성합니다.
3. 각 서비스의 환경 설정에서 `GEMINI_API_KEY`, `OPENALEX_API_KEY`, `STUDY_ACCESS_CODE`를 비밀 값으로 입력합니다. 두 서비스에는 같은 API 키를 넣고, 접속 코드는 조건별로 다르게 만들어 해당 참가자에게만 전달합니다.
4. 유료 인스턴스와 각 서비스의 1GB 영구 디스크가 포함된 비용을 확인한 뒤 생성합니다.
5. 두 서비스의 `/api/status`에서 `mode=gemini`, `openalex_configured=true`와 각 시스템의 버전을 확인합니다. baseline은 `condition=baseline`도 반환합니다.

## 데이터와 비용

SQLite를 사용하는 현재 구조에서 기록 보존을 위해 영구 디스크가 필요합니다. Render 무료 웹 서비스에는 영구 디스크를 붙일 수 없으므로 Blueprint는 두 서비스를 각각 소형 유료 플랜과 1GB 디스크로 설정했습니다. 생성 전에 Render 대시보드의 현재 예상 비용을 확인하세요.

각 조건의 데이터는 별도 디스크에 저장됩니다. 세션 로그는 `/api/session/export`로 해당 브라우저 세션을 내보낼 수 있습니다. 참여자 ID와 세션 ID 매핑은 별도 연구 기록표에 보관해야 합니다.

## 배포 후 확인할 주소

Render가 생성한 두 `onrender.com` 주소가 참가자용 링크입니다. 실제 서비스가 생성되기 전에는 주소를 알 수 없습니다. 한 조건의 주소를 참가자에게만 배포하고, 각 조건의 브라우저 세션과 로그가 분리되는지 먼저 확인한 뒤 스터디를 시작하세요.
