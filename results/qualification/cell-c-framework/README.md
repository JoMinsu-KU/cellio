# 세 제조 셀의 공통 프레임워크 검증

CellIO `1.1.0.dev2`는 컨베이어 이송 셀 A, 클램프 가공 셀 B, 브래킷 검사·선별 셀 C를 Python 패키지의 `cellio.scene_plugins`에 등록한다. 세 셀은 `cellio validate`, `cellio run`, `cellio verify-artifact`, `cellio evaluate`를 공유한다. 각 셀이 설비, 제어기, 센서, 요구사항을 제공하고 공통 엔진이 시간 진행, 명령 전달, 실행 기록과 재평가를 담당한다. [플러그인 규약](../../../docs/add_a_cell.md) · [셀 C 시나리오와 실행 명령](../../../docs/cell_c.md)

## 이번 버전에서 새로 실행한 사례

실험 전 정상 A·B 각 1회와 C 4회의 입력, 기대 판정, 확인 기준을 고정했다. 두 부품의 높이가 33.15 → 33.25 mm인 경우와 32.85 → 33.15 mm인 경우를 정상·고장 제어기로 각각 실행했다. 설계 공차는 33.00 ± 0.20 mm다. 고장 조건에서는 양품 판정 직후 다음 판정 갱신을 누락한다. [6개 사례의 검증 계획](../../../protocols/three-cell-framework-v1.json)

셀 C의 제어기는 게이지를 읽으면 판정 소유자와 목적 트레이를 공통 명령 라우터로 보낸다. Isaac Sim은 두 명령을 받을 때까지 해당 부품의 선별 동작을 기다린다. 완료 후 공통 기록에서 실제 적용된 명령과 시점을 읽고, 원본 게이지 이벤트의 ID·목적지 및 PhysX 안착 위치와 대조한다. 30 Hz로 기록된 검사 프레임은 120 Hz 물리 시계의 해당 스텝으로 변환된다. 세 원본 파일의 바이트 체크섬도 완료 아티팩트에 포함된다. [실행 결과 검산 코드](../../../scripts/check_three_cell_framework.py)

| 셀과 조건 | 최초 실행 | 공통 평가 결과 | 관찰한 동작 |
| --- | ---: | --- | --- |
| A, 정상 이송 | 1회 완료 | PASS | 부품 이송 성공, 요구사항 16개 재평가 일치 |
| B, 정상 클램프 가공 | 1회 완료 | PASS | 공정 성공, 요구사항 18개 재평가 일치 |
| C, 양품 → 불량, 정상 제어 | 1회 완료 | PASS | 두 번째 부품의 판정으로 NG 트레이에 안착 |
| C, 양품 → 불량, 고장 제어 | 1회 완료 | FAIL | 첫 부품의 판정으로 두 번째 불량품이 OK 트레이에 안착 |
| C, 양품 → 양품, 정상 제어 | 1회 완료 | PASS | 두 번째 부품의 판정으로 OK 트레이에 안착 |
| C, 양품 → 양품, 고장 제어 | 1회 완료 | FAIL | 두 번째 부품이 OK 트레이에 안착했으나 판정 ID는 첫 부품 |

6회 모두 최초 시도에서 7,200 물리 스텝을 완료했다. 공정은 6회 모두 성공으로 끝났고, 요구사항 평가는 4회 통과·2회 결함 검출이었다. 셀 C 네 시행에서 제어기의 8개 선별 결정 모두 원본 이벤트와 일치했다. 공통 라우터는 첫 부품의 명령을 스텝 1,872에, 두 번째 부품의 명령을 5,140에 적용했다. Windows와 Ubuntu WSL이 같은 설치 휠에서 재평가한 결과는 요구사항 54개 및 최초 증거 시점에서 실시간 판정과 일치했고 최종 6개 사례 요약은 바이트 단위로 같았다. [신규 물리 실행 6건](fresh-three-cell.json) · [운영체제 간 검산](fresh-platforms.json)

## 기존 셀 C 실험의 호환성

이전 버전에서 완료한 24개 Isaac Sim 실행은 원래의 이벤트·이동·결과 파일에서 관측 시점만 가져와 같은 셀 C 판정기로 재평가했다. Windows와 WSL에서 각각 24건을 가져왔고, 부품 48개의 측정값·판정 소유자·목적지와 다섯 요구사항의 판정 및 첫 증거 시점 120개를 확인했다. 두 시스템 모두 통과 18건, 결함 검출 6건을 재현했고 결과 파일은 바이트 단위로 같았다. 이 재평가에는 새 물리 실행이나 당시의 공통 실시간 판정을 소급해 포함하지 않는다. [24개 사례별 대조](history-equivalence.json) · [운영체제 간 일치](history-platforms.json)

## 재현 방법과 적용 범위

소스 커밋 `87f0f63`의 Windows Isaac Sim 6.2 환경에서 저장소 루트 기준으로 다음 명령을 실행한다. 첫 번째 명령의 `--runs`에는 새 디렉터리를 사용한다. 두 번째는 같은 완료 기록을 CPU에서 검산하는 명령이다.

```console
python scripts/check_three_cell_framework.py --run --runs work/three-cell-runs --output work/three-cell-results.json
python scripts/check_three_cell_framework.py --runs work/three-cell-runs --output work/replayed-results.json
```

이 검증은 세 제조 셀에서 공통 소프트웨어의 실행·기록·판정·재생 경로가 동작하는지 시험한다. A·B의 기존 등록 캠페인은 820회이며, 이번 6회는 별도의 통합 검증이다. 셀 C에서 물리는 새 프로세스의 Isaac Sim으로 실행했고, WSL은 Windows에서 완료된 기록을 읽었다. C는 헤드리스·새 프로세스 모드로 검증했다. A·B의 여섯 결함군과 셀 C의 검사 판정 고장은 서로 다른 실험 조건이다. [셀 C 모델의 물리적 범위](../../../docs/cell_c.md) · [이전 A·B 평가](../../publication/candidate-8ad96bd/primary/primary-review.md)

같은 소스에서 두 번 빌드한 휠의 SHA-256은 모두 `c454bf0e9ae9ccf93fbedb23617656897de8aa6a8a7afca3e97629e54c39cb70`이다. Windows·WSL에서 전체 테스트 중 CPU에서 가능한 454개가 모두 통과했다. 나머지 한 개는 실제 Isaac Sim의 하드 리로드 시험으로 Windows에서 별도 실행해 통과했다. 워크스테이션의 Windows `cellio-isaac62`와 WSL `cellio-audit`에 개발판을 편집 가능 설치로 갱신했다. [배포판 정보](distribution.json) · [Windows 테스트](windows-tests.json) · [WSL 테스트](wsl-tests.json) · [Windows 물리 하드 리로드](windows-native-test.json)
