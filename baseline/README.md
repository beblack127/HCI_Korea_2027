# Baseline

이 폴더는 유저스터디 비교 조건인 baseline 시스템입니다. 제안 시스템과 같은 Gemini/OpenAlex 설정을 사용하며, 상태 기반 응답과 상태 패널을 제공하지 않습니다.

## 원격 배포

저장소 루트의 `render.yaml`이 제안 시스템과 함께 별도 Render 서비스로 배포합니다. 배포 전 루트의 [배포 안내](../docs/DEPLOYMENT_RENDER.md)를 확인하세요. baseline 서비스는 `CONDITION=baseline`, `SHADOW_ESTIMATE=sync`로 실행되며, SQLite 데이터는 제안 시스템과 분리된 영구 디스크에 저장됩니다.

## 로컬 실행

원본 baseline 로컬 실행 안내는 별도 로컬 프로젝트의 `docs/BASELINE_STUDY.md`를 참고하세요. 이 폴더는 원본 실행 사본과 별도로, 두 조건을 한 저장소에서 배포하기 위한 코드입니다.
