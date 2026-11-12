# Baseline

이 폴더는 유저스터디 비교 조건인 baseline 시스템입니다. 제안 시스템의 11.12-v2 상태 추정·세션 기록·검색 구성요소를 공유하되, 상태 기반 응답과 상태 패널은 제공하지 않습니다.

## 원격 배포

저장소 루트의 `render.yaml`이 제안 시스템과 함께 별도 Render 서비스로 배포합니다. 배포 전 루트의 [배포 안내](../docs/DEPLOYMENT_RENDER.md)를 확인하세요. baseline 서비스는 `CONDITION=baseline`, `SHADOW_ESTIMATE=sync`로 실행되며, 11.12-v2 상태 추정은 분석용 기록으로만 저장합니다. SQLite 데이터는 제안 시스템과 분리된 영구 디스크에 저장됩니다.

## 로컬 실행

로컬 실행은 저장소 루트의 `setup.command`와 `run_baseline.command`를 참고하세요. 이 폴더는 두 조건을 한 저장소에서 배포하기 위한 baseline 실행 코드입니다.
