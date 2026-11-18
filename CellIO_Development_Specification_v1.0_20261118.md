# CellIO 개발·검증·논문 산출물 통합 명세서

**문서 버전:** 1.1 / **기준일:** 2026-11-18, Asia/Seoul
**대상 제품:** CellIO 1.0.0 / **설정 스키마:** cellio.config/1.0  
**대상 논문:** *CellIO: Scene-bound I/O fault injection and regression testing for robotic workcells in Isaac Sim*  
**개발 환경:** Windows 11 Pro x86_64, 전용 Conda Python 3.12, NVIDIA Isaac Sim 6.2.0.0 Python 패키지. WSL2 Ubuntu는 독립적인 오프라인 검사에 사용한다.
**기본 실행 모드:** 시뮬레이터 단독, 고정 physics step, Python 참조 제어기  
**문서 성격:** 구현해야 할 규범과 검증 방법을 정의한 개발 명세. 성능 측정 결과나 구현 완료 보고서가 아니다.

---

**개발 착수 시 우선 읽을 부분:** [완료·성능 기준](#sec-23) → [전체 구조](#sec-05) → [데이터·실행 계약](#sec-08) → [구현 순서](#sec-24).

**문서 탐색**

| 장 | 내용 |
|---:|---|
| 0 | [이 문서를 사용하는 방법](#sec-00) |
| 1 | [제품의 목표와 완료 수준](#sec-01) |
| 2 | [범위·기여·비교 계약](#sec-02) |
| 3 | [요구사항 식별자](#sec-03) |
| 4 | [기준 환경과 버전 정책](#sec-04) |
| 5 | [전체 아키텍처](#sec-05) |
| 6 | [좌표·단위·이름·시간의 공통 계약](#sec-06) |
| 7 | [실행 순서와 시간 의미](#sec-07) |
| 8 | [데이터형과 공개 인터페이스](#sec-08) |
| 9 | [M01 설정·스키마·registry](#sec-09) |
| 10 | [M02 clock·event와 M05 signal 계층](#sec-10) |
| 11 | [M06 고장 엔진의 상세 사양](#sec-11) |
| 12 | [M03 Isaac 어댑터와 M04 장면·truth](#sec-12) |
| 13 | [참조 작업셀의 제작 명세](#sec-13) |
| 14 | [M07 명령·전달·구동 계층](#sec-14) |
| 15 | [M08 참조 제어기와 결함 변형](#sec-15) |
| 16 | [M09 독립 검사기: 규칙·시간 의미·기대 판정](#sec-16) |
| 17 | [M10 episode runner와 M11 campaign 실행기](#sec-17) |
| 18 | [M12 기록·artifact·무결성](#sec-18) |
| 19 | [M13 지표·통계·보고서](#sec-19) |
| 20 | [M14 CLI·doctor·사용자 오류](#sec-20) |
| 21 | [M15 확장 포트와 M16 선택적 OpenPLC](#sec-21) |
| 22 | [시험 목록과 동결할 캠페인](#sec-22) |
| 23 | [R3 완료·성능 수락 기준](#sec-23) |
| 24 | [개발 작업 분해·일정·중단 판단](#sec-24) |
| 25 | [저장소·개발 산출물·SoftwareX 원고 연결](#sec-25) |
| 26 | [근거·설계 결정·해석 범위](#sec-26) |
| 27 | [부록: 독립 oracle fixture 생성 명세](#sec-27) |

<a id="sec-00"></a>

## 0. 이 문서를 사용하는 방법

이 문서 하나에 제품 범위, 구조, 데이터 계약, 처리 순서, 모듈의 책임, 장면 제작 규칙, 시험 사례, 성능 목표, 배포 조건을 포함한다. 개발자는 별도 기획자의 구두 설명 없이 명시된 기본값과 오류 규칙으로 구현한다.

### 0.1 규범의 우선순위

1. **MUST:** CellIO 1.0.0 릴리스에서 반드시 충족한다.
2. **SHOULD:** 권장 목표. 미충족하면 측정값·원인·영향·해결 계획을 릴리스 보고서에 기록한다.
3. **OPTIONAL:** 별도 확장. 미구현해도 기본 릴리스는 가능하다.
4. 본문의 정식 데이터 계약·phase 표·합격 기준이 설명용 그림보다 우선한다.
5. 예제 설정은 규범과 일치해야 한다. 구현 편의로 예제만 변경해 규범을 우회하지 않는다.
6. 새 의사결정이 필요한 실제 API 차이·물리 파라미터 조정은 ADR과 변경 기록에 남긴다. 실험 후 합격선을 낮추거나 실패 표본을 제거하지 않는다.

모든 수치 목표는 특별히 출처를 표시한 외부 사실을 제외하면 **이번 프로젝트를 위해 제안한 개발 기준**이다. SoftwareX가 요구하는 공식 성공률·지연시간·시험 횟수로 해석하지 않는다. 이 문서의 제출 수준을 충족해도 논문 채택을 보장하지는 않는다.

### 0.2 현재 상태와 실행 권한

| 항목 | 현재 상태 |
|---|---|
| 주제 선택 | 사용자가 CellIO 기준의 상세 개발 명세 작성을 요청함 |
| 설계 | 이 문서에 기준 구현과 검증 계약을 명시 |
| 소프트웨어 구현 | 진행 중. 실제 상태는 저장소의 커밋과 시험 증거에서 확인 |
| Isaac Sim 실행·성능 측정 | NOT_STARTED |
| 자산·환경 실제 잠금 | 개발 착수 시 manifest를 생성해야 함 |
| 파일럿·본실험 | 실행 증거와 검증된 결과 파일 단위로 기록 |
| 이번 작업 범위 | 워크스테이션 환경에 맞춘 구현, 실험, 재현, 배포 |
| 학술적 주장 상태 | 구현·실험으로 검증할 제안 |

2026-11-18 확인한 워크스테이션은 Windows 11 Pro, i9-14900K, RTX 4090 24GB, RAM 64GiB다. C:는 NVMe, D:와 E:는 HDD다. 기준 실험의 쓰기 경로와 캐시는 SSD를 사용하고, 완결 결과는 검사 후 HDD에 보관할 수 있다. WSL2 Ubuntu 22.04.5는 Linux용 Python 3.12 환경에서 동일 기록을 재판정한다.

### 0.3 용어

| 용어 | 이 문서의 의미 |
|---|---|
| plant / 실제 장면 상태 | Isaac Sim 안에서의 물리·기하 상태. 실물 공장의 실제 상태를 뜻하지 않음 |
| raw signal | 장면에서 정상 매핑한 신호 |
| observed signal | 고장 계층을 통과해 제어기에게 전달하는 신호 |
| command intent | 제어기가 발행한 원래 명령 |
| applied command | 지연·유효성 검사 후 실제 어댑터에 전달한 명령 |
| oracle / 검사기 | 사전 요구사항과 독립적인 장면 근거로 실행을 판정하는 모듈 |
| episode | 초기화부터 종료까지의 한 시험 실행 |
| run | 설정·장면·제어기·초기조건을 고정한 실행 단위 |
| attempt | 같은 run을 수행하려는 개별 시도. 재시도도 원래 기록을 보존 |
| campaign | 사전 고정한 여러 run의 목록 |
| 고장 활성 | 고장 연산자가 예정된 구간에 적용 중인 상태 |
| 고장 발현 | 관측값·전달 시점·물리 상태에서 사전 정의한 변화가 실제 확인된 상태 |
| 회귀시험 | 코드나 설정 변경 전후에 동일 시험 계약을 적용하는 검사 |

---

<a id="sec-01"></a>

## 1. 제품의 목표와 완료 수준

### 1.1 해결할 사용자 작업

제조 로봇 연구자가 자신의 Isaac 작업셀에 센서·설비 신호를 연결하고, 정상 실행 및 고장 조건을 반복 실행한 다음 다음 질문에 답할 수 있게 한다.

- 요청한 고장이 정확한 대상과 시점에 적용됐는가?
- 제어기가 본 정보와 장면 상태는 어떻게 달랐는가?
- 제어기는 사전에 정한 순서·중단·시간 제한·복구 규칙을 지켰는가?
- 제어기나 어댑터가 변경됐을 때 같은 시험이 다시 통과하는가?
- 다른 장면을 연결할 때 어느 설정과 어댑터를 작성해야 하는가?

### 1.2 단계별 완료 정의

| 단계 | 산출물 | 다음 단계로 넘어가는 조건 |
|---|---|---|
| R0 계약 원형 | mock plant, 순수 Python clock·fault·oracle, 예제 trace | 물리 엔진 없이 시간·고장·판정 경계 시험 통과 |
| R1 작동 원형 | 실제 Isaac 장면 A, 정상 작업, F1·F6 | 정상 사이클, 센서 오류와 구동 차단의 분리, 독립 판정 확인 |
| R2 기능 완성 | 장면 A·B, F1–F6, CLI, 기록·보고서, reset | 이 문서의 필수 기능과 부정 입력·종료 경로 구현 |
| **R3 제출 수준** | 공개 릴리스, 잠근 환경, 검증 캠페인, 재현 문서, 원시 결과 | 필수 정확성·격리·무결성 게이트 통과, 권장 성능의 실측·설명, 외부 재실행 |
| R4 확장 | OpenPLC, 새 로봇, 추가 장면·복합 고장 | 별도 계약과 시험을 추가한 후 지원 범위 확대 |

**이번 개발의 목표는 R3다.** R1의 데모 영상만으로 완료 처리하지 않는다. R3에 OpenPLC·새 VLA 학습·실물 로봇 실험을 추가 조건으로 붙이지 않는다.

### 1.3 정확성과 속도의 관계

정확성·데이터 무결성·초기화 격리는 MUST다. 실행 속도가 빠르더라도 고장이 틀린 대상에 적용되거나 원시 기록이 누락되면 릴리스하지 않는다.

성능은 다음 세 종류로 나눈다.

- **도구 정확성:** 요청과 실행의 일치, 검사기의 기대 판정 일치.
- **시스템 실행 성능:** 오류 없이 실행·초기화·기록하는 능력과 처리 비용.
- **시험 대상 제어기의 성능:** 작업 완료·중단·복구 행동. 고장 조건에서 작업이 중단돼도 도구는 정확하게 동작했을 수 있다.

---

<a id="sec-02"></a>

## 2. 범위·기여·비교 계약

### 2.1 필수 범위

- Isaac Sim 6.2.0의 PhysX 단일 장면·단일 로봇 실행.
- Franka Panda 계열의 공식 지원 자산 1종.
- 서로 다른 제어 순서를 가진 장면 A와 B.
- Python 상태기계 참조 제어기 및 표적 결함 변형.
- 6개 고장 계열: F1 stuck_at, F2 observation_freeze, F3 observation_delay, F4 command_delay, F5 observation_dropout, F6 actuator_block.
- 한 episode에서 최대 한 고장 인스턴스. 같은 대상·여러 대상의 동시 복합 고장은 1.0.0에서 거부한다.
- GUI 시연, headless 시험, 오프라인 trace 판정·집계.
- 공개 설정·장면 생성 코드·검사 규칙·기대 결과·원시 결과.

### 2.2 확장 범위

OpenPLC/Modbus, 외부 에이전트, 복수 로봇, ROS 2, Docker 배포, 카메라 기반 판정, 복합 고장, 새 시뮬레이터는 OPTIONAL이다. 기본 의존성에 포함하지 않는다. 확장은 기존 시험 결과에 섞지 않고 support profile과 캠페인을 구분한다.

### 2.3 학술적 기여 계약

| ID | 제안하는 기여 | 직접 검증 | 주장이 무너지는 관측 |
|---|---|---|---|
| C1 | 장면 상태와 I/O를 연결하면서 센서·전달·구동 오류를 분리하는 재사용 구조 | 고장 연산자의 exact fixture, 실제 장면 발현 기록 | 고장 종류가 같은 flag 변경으로 구현됨, raw 상태와 corrupted 상태가 섞임 |
| C2 | 명령·실제 상태·고장 생애주기를 정렬한 독립 시험 | 사전 라벨 trace·표적 결함·온라인/오프라인 판정 | 제어기의 PASS나 injector 성공 flag를 정답으로 복사함 |
| C3 | 두 작업셀과 교체 가능한 제어기에서 재사용할 수 있는 공개 도구 | 장면 B 연결 변경 목록·외부 실행·오버헤드 | 핵심 코드에 장면별 분기를 계속 추가해야 함 |

“최초의 고장 주입”, “산업 안전 보장”, “실제 PLC와 같은 시간 특성”, “모든 로봇으로 일반화”는 이 계약에 포함하지 않는다.

### 2.4 가까운 기존 구현과 비교 범위

| 비교 ID | 대상 | 사용할 비교 |
|---|---|---|
| B-PRIOR-1 | isaac-plc-digital-twin | 신호 매핑·프로토콜·watchdog 지원 범위 및 실행 기록 기능 대조 [S10] |
| B-PRIOR-2 | industrial-automation-cell-simulator | 고장·제어 순서·복구·회귀시험의 기능과 검증 성숙도 대조 [S11] |
| B-PRIOR-3 | otlab-sim | 태그·통신·시간 지정 신호 고장과 장면 연결 범위 대조 [S12] |
| B-MANUAL | 같은 장면에서 작성한 명시적 수동 고장 스크립트 | 같은 입력·출력·검사·기록량을 맞춘 기능 parity와 재사용 작업량 |
| B-OFF | 같은 장면·제어기를 사용하고 CellIO 계측만 끈 경로 | 총 부가 비용. 물리와 motion 계산은 동일 |
| B-TRACE | 같은 raw trace 기록을 켜고 고장·판정만 끈 경로 | 기록 비용과 fault/oracle 비용 분리 |

B-MANUAL은 연구자가 구성한 기준선으로 표시한다. 기존 도구가 같은 장면·시간 계약을 지원하지 않으면 수치 대결을 강제하지 않고 기능 비교로 한정한다. Isaac Lab으로 실행 환경을 바꿔 얻은 수치를 동일 조건 비교라고 표시하지 않는다.

---

<a id="sec-03"></a>

## 3. 요구사항 식별자

| ID | MUST 요구사항 | 책임 |
|---|---|---|
| FR-01 | 실행 전 설정·자산·단위·버전·신호·명령 참조 검증 | config / doctor |
| FR-02 | 독립 raw snapshot과 controller process image 분리 | truth / signals |
| FR-03 | 정수 step 기반 동일한 phase 순서 | clock / runner |
| FR-04 | 정상 신호의 타입·단위·출처·시각·품질 기록 | signals |
| FR-05 | F1–F6의 정의된 활성·해제·미발현 처리 | faults |
| FR-06 | command intent·queue·apply 결과 분리 | commands |
| FR-07 | 고장 구간 이후 queue·block 상태 제거 | faults / commands |
| FR-08 | 관측과 독립적인 요구사항 검사 | oracle |
| FR-09 | PASS·FAIL·UNKNOWN·NOT_APPLICABLE 구분 | oracle |
| FR-10 | 작업 결과·도구 오류·시험 결과를 독립 저장 | outcome |
| FR-11 | initial/reset/generation token의 episode 격리 | runner / adapter |
| FR-12 | 모든 시도와 재시도 보존 | campaign |
| FR-13 | GUI와 headless에 동일한 physics contract | adapter |
| FR-14 | mock와 실제 Isaac 어댑터를 같은 port로 교체 | core ports |
| FR-15 | 다른 작업셀의 설정·어댑터 추가로 확장 | scene plugin |
| FR-16 | 명시적인 정상·결함 제어기 제공 | controllers |
| FR-17 | trace 무결성과 완결 marker 확인 후 집계 | trace / analysis |
| FR-18 | 기록 실패·queue 초과를 조용히 무시하지 않음 | trace / runner |
| FR-19 | 오프라인 판정과 요약을 raw 결과로 재생성 | oracle / reporting |
| FR-20 | 실험·환경·설정·코드·자산 식별자 보존 | manifest |
| FR-21 | 외부 실행 가능한 README·명령·예상 산출물 제공 | packaging |
| FR-22 | 오류 코드와 종료 코드를 고정 | CLI / errors |
| FR-23 | config에서 임의 코드 실행·임의 import 금지 | config |
| FR-24 | 필수 기능·사례·성능 기준을 테스트 ID에 연결 | verification |

---

<a id="sec-04"></a>

## 4. 기준 환경과 버전 정책

### 4.1 지원 프로파일 P-ISAAC62-WIN-CONDA

| 항목 | 기준 |
|---|---|
| OS | Windows 11 Pro x86_64. WSL2 Ubuntu 22.04.5 x86_64는 오프라인 재판정 환경 |
| Isaac Sim | NVIDIA Python 패키지 isaacsim[all,extscache]==6.2.0.0 |
| Python | Windows Conda cellio-isaac62의 CPython 3.12. WSL2에는 독립적인 cellio-audit 환경을 둔다 |
| 실행 장비 | i9-14900K, RTX 4090 24GB, RAM 64GiB, 실행 중 원시 기록은 SSD |
| GPU worker 수 | 기본 1 |
| 물리 엔진 | PhysX, CPU dynamics, 고정 timestep |
| 렌더러 | benchmark에서 센서 렌더링 OFF; GUI 시연에서만 렌더링 |
| motion | 공식 cuMotion RmpFlowController adapter |
| ROS / PLC / LLM API | 기본 프로파일 의존성 없음 |
| 코드 기본 라이선스 | 자체 작성 코드 MIT; 제3자 코드·자산은 별도 표기 |

Isaac Sim 6.2의 Windows 11 시험 드라이버는 595.97이다. 이 장비에서 조회한 610.88은 compatibility check와 Kit·PhysX·cuMotion 실측 후 환경 잠금에 기록한다. Python 패키지의 6.2.0.0과 제품 버전 6.2.0을 함께 기록한다. [S01–S04]

기존 5.1 예제의 import와 6.2 API를 섞지 않는다. NVIDIA의 API 변경은 adapters/isaac62 안에서 흡수하고, core 모듈에 Omniverse·USD·Warp import를 추가하지 않는다. 문서의 “latest” 링크를 설치 스크립트에서 자동 추적하지 않는다.

### 4.2 의존성 잠금

NVIDIA가 지원하는 CUDA PyTorch와 Isaac Sim의 의존성을 별도 Conda 환경에 설치한다. CellIO core는 Python 표준 라이브러리, PyYAML 6.x, jsonschema 4.x를 사용한다. 개발 시험에는 pytest 8.x, hypothesis 6.x, coverage 7.x, mypy 1.x, ruff 0.x를 사용한다. 플랫폼별 정확한 버전과 배포물 hash는 설치 및 smoke 통과 후 고정한다.

Windows 물리 실행과 WSL2 재판정은 각각 환경 식별자와 잠금 파일을 갖는다. Windows 프로세스가 한 에피소드의 물리, 제어, 기록을 소유한다. WSL2는 COMPLETE와 파일 무결성 확인 후 확정된 기록을 읽어 판정과 witness step을 비교한다. OS·설치 방식·드라이버가 다른 물리 측정은 별도 평가 그룹으로 관리한다.

필수 생성 파일:

- environment.lock.json: OS·커널·CPU·GPU·driver·Isaac·Kit·Python·PhysX·cuMotion extension 실제 버전
- requirements.lock: CellIO 추가 의존성 exact version과 hash
- assets.lock.json: 사용 USD·material·robot config·참조 자산의 URL/로컬 경로·SHA-256·이용조건 위치
- protocol.lock.json: clock·solver·장면·시나리오·검사 규칙·seed 목록의 hash

잠금 파일의 hash를 임의 예시 문자열로 채우지 않는다. 실제 파일에서 계산한다. physics time이나 코드가 다른 결과는 같은 실험 그룹으로 합치지 않는다.

### 4.3 설치 절차의 계약

다음 경로를 공식 지원한다.

1. Windows에서 새 Conda Python 3.12 환경을 만들고 NVIDIA 6.2 공식 pip 절차로 CUDA PyTorch와 Isaac Sim을 설치한다. 최초 사용 시 NVIDIA 이용조건을 사용자에게 확인한다.
2. CellIO 저장소를 clone하고 해당 환경에 editable install한다.
3. 활성 환경의 python으로 doctor와 공식 호환성 검사를 실행한다.
4. assets resolve로 필요한 자산만 준비하고 checksum을 기록한다.
5. mock smoke, Isaac import smoke, 정상 장면 smoke 순서로 실행한다.
6. confirmatory 실험 전 assets verify --offline가 성공해야 한다.

아래 명령은 Windows 전용 환경에서 실행한다. 개발 중인 후보의 지원 수준은 같은 커밋의 시험 및 수락 보고서에서 확인한다.

~~~powershell
conda activate cellio-isaac62
python -m pip install --require-hashes -r locks/windows-isaac62/requirements.lock
python -m pip install -e . --no-deps
python -m cellio doctor --profile isaac62 --output artifacts/doctor.json
python -m cellio assets resolve --profile franka_panda
python -m cellio validate --config examples/cell_a/healthy.yaml
python -m cellio run --config examples/cell_a/healthy.yaml --headless
~~~

설치 스크립트는 사용자 확인 없이 드라이버를 교체하거나 NVIDIA 이용조건 동의 환경변수를 설정하지 않는다. 코드가 사용자 장비에서 이미 수행됐다는 로그를 포함하지 않는다.

---

<a id="sec-05"></a>

## 5. 전체 아키텍처

### 5.1 논리 계층

~~~mermaid
flowchart TD
    L7["L7 사용자 계층: CLI · GUI view · 보고서"]
    L6["L6 실행 계층: campaign · episode · worker"]
    L5["L5 검증 계층: oracle · metrics · artifact"]
    L4["L4 제어 경계: controller · command pipeline"]
    L3["L3 고장 계층: trigger · schedule · operators"]
    L2["L2 관측 계층: truth snapshot · signal mapping"]
    L1["L1 시뮬레이터 계층: Isaac adapter · scene · motion"]
    L0["L0 공통 계약: types · clock · config · errors"]
    L7 --> L6
    L7 --> L5
    L6 --> L4
    L6 --> L3
    L6 --> L2
    L6 --> L1
    L5 --> L0
    L4 --> L0
    L3 --> L0
    L2 --> L0
    L1 --> L0
~~~

계층 그림의 위아래는 책임의 분류다. domain 모듈이 Isaac 객체를 직접 import하는 것을 허용하는 의존성 규칙이 아니다. 모든 domain 간 연결은 L0의 명시적 데이터 구조와 port로 수행한다.

### 5.2 실행 데이터 경로

~~~mermaid
flowchart TD
    W["Isaac 장면 S_k"] --> T["읽기 전용 TruthSnapshot"]
    T --> M["정상 signal mapper"]
    M --> F["관측 고장 F1 F2 F3 F5"]
    F --> C["ControllerView"]
    C --> P["시험 대상 제어기"]
    P --> Q["CommandIntent와 F4 queue"]
    Q --> A["ActuatorAdapter와 F6 block"]
    A --> W
    T --> O["독립 oracle"]
    P --> O
    O --> R["trace · 결과 · 보고서"]
    F --> R
    Q --> R
    A --> R
~~~

검사기는 raw snapshot과 명령 기록을 읽는다. 제어기에는 TruthSnapshot, fault ID, 기대 판정, oracle 결과를 전달하지 않는다. log thread는 mutable USD handle을 읽거나 물리 장면을 변경하지 않는다.

### 5.3 프로세스와 스레드

| 실행 단위 | 소유하는 상태 | 허용 작업 |
|---|---|---|
| supervisor process | campaign plan, attempt ID, timeout, subprocess 목록 | worker 생성·종료, 결과 무결성 확인 |
| Isaac worker main thread | SimulationApp, stage, physics, snapshot, domain runtime | 정해진 phase 순서에 따른 단일 writer |
| trace writer thread | 직렬화된 immutable record queue, 파일 handle | 파일 기록·flush·오류 전달 |
| offline analysis process | 완료된 raw artifact | 검증·판정 재계산·집계·그림 생성 |
| OPTIONAL external PLC process | PLC process image | 별도 wall-clock profile의 protocol 처리 |

한 worker는 동시에 한 episode만 실행한다. 본평가는 매 episode fresh_process를 사용하고, reset-chain·soak 검증에서만 같은 worker의 순차 hard_reload를 허용한다. 1.0.0에서 vectorized scene·한 GPU의 다중 worker는 지원하지 않는다. multiprocessing은 spawn을 사용하고, Kit/CUDA 초기화 후 fork하지 않는다.

supervisor가 GUI 앱을 띄워 결과를 읽는 방식으로 자동화를 구현하지 않는다. GUI는 동일 runtime의 관찰 수단이며 UI 클릭이 시험의 필수 단계가 되지 않는다.

### 5.4 모듈 목록

| ID | 모듈 | 핵심 책임 |
|---|---|---|
| M01 | config | 읽기·schema·의미 검증·정규화·hash |
| M02 | clock/events | step·phase·event 순서 |
| M03 | isaac62 adapter | 앱·stage·physics·handle·reset |
| M04 | scene/truth | 장면 생성·읽기 전용 독립 관측 |
| M05 | signals | typed mapping·source age·process image |
| M06 | faults | trigger·schedule·F1–F6·발현 근거 |
| M07 | commands/actuators | validation·queue·motion·구동 적용 |
| M08 | controllers | 정상 상태기계·표적 결함 제어기 |
| M09 | oracle | raw predicate·시간 요구사항·판정 |
| M10 | episode runner | 전체 phase 실행·종료·reset |
| M11 | campaign/worker | plan·attempt·watchdog·격리 |
| M12 | trace/artifact | raw 기록·hash·완결·검증 |
| M13 | metrics/report | 분모·집계·CI·보고서 |
| M14 | CLI/doctor | 사용자 명령·오류 코드·환경 검사 |
| M15 | scene extension | 새 작업셀 registry와 adapter 등록 |
| M16 | OPTIONAL OpenPLC | 별도 wall-clock bridge |

---

<a id="sec-06"></a>

## 6. 좌표·단위·이름·시간의 공통 계약

### 6.1 공간 계약

| 항목 | 규칙 |
|---|---|
| 길이·속도 | m, m/s |
| 질량·힘·토크 | kg, N, N·m |
| 회전·각속도 | rad, rad/s |
| 세계 좌표 | 오른손 좌표, Z-up |
| stage | metersPerUnit=1.0, upAxis=Z |
| quaternion | domain 경계에서 (w,x,y,z), norm 오차 ≤1e-6 |
| pose | world_T_body. frame_id를 생략하지 않음 |
| 관절 순서 | 이름으로 한 번 정렬한 manifest 순서; API 배열 순서에 의존하지 않음 |
| float | domain은 float64, finite만 허용 |
| 물리 엔진 원본 | 실제 backend dtype을 provenance로 기록하고 변환 |

quaternion의 q와 -q는 같은 회전으로 취급한다. 회전 오차는 2·acos(min(1, abs(dot(q1,q2))))로 계산한다. 입력 norm 오차가 1e-6 이하이면 그대로 사용한다. 1e-6 초과~1e-3 이하이면 한 번 normalize하고 변환을 기록한다. 1e-3 초과 또는 zero norm은 입력 오류다. domain에 저장된 최종 norm 오차는 1e-6 이하여야 한다.

### 6.2 식별자

- 정적 ID: 정규식 [a-z][a-z0-9_.-]{0,95}.
- run_id: 캠페인 계획에서 결정한 고유 문자열.
- attempt_id: 각 시도에 새로 부여한 UUID4. run_id와 attempt_index는 별도 필드.
- episode_id: attempt에서 한 번만 생성하며 재사용하지 않음.
- generation: reset 때 증가하는 uint64. 모든 명령·신호·queue item에 포함.
- entity_id / signal_id / actuator_id / requirement_id는 registry 내부에서 중복 불가.
- FR/T/R/C_M 표기는 문서의 요구·시험·결함 라벨이다. runtime requirement ID는 cell_a.r01.state_valid처럼 셀 prefix와 소문자 label을 사용한다. mutant registry ID는 mutant.c_m1…mutant.c_m6다. family F1…F6와 Verdict 등 고정 enum은 정적 ID 정규식의 적용 대상이 아니다.
- prim path는 절대 USD path이며 prefix pattern을 fault 대상 식별자로 사용하지 않는다.

### 6.3 고정 clock

| 항목 | 기본값 |
|---|---:|
| physics_hz | 120 |
| physics_dt | 정확한 유리수 1/120 s |
| controller_divisor | 4 |
| controller_hz | 30 |
| sensor·oracle 주기 | 매 physics step |
| GUI rendering | 최대 30Hz, 판정과 분리 |
| warmup_steps | 240 |
| episode_horizon_steps | 7200, 즉 60 simulated seconds |
| fault 최대 활성 기간 | 1200 steps |
| signal/command delay 최대 | 120 steps |
| 기본 허용 source age | 12 steps |

누적 부동소수점 시간으로 timeout을 판단하지 않는다. step 정수만 판정에 사용하고, 표시용 seconds = step / 120을 계산한다. sim_time_ns는 유리수 표현을 대체하지 않는다. wall-clock은 perf_counter_ns로 기록하며 timeout·비용 측정에만 사용한다.

canonical config에는 정수 steps만 허용한다. 설정 작성용 순수 함수 steps_from_seconds(decimal_string)는 Decimal로 읽어 ceil(seconds×120)을 반환한다. 이 helper를 사용했다면 원래 값·반올림 정책을 별도 conversion.json에 보존한다. schema에 없는 seconds key를 config에 추가하지 않는다.

warmup은 episode step에 포함하지 않는다. warmup이 끝난 뒤 process image·controller·event edge history·oracle을 다시 초기화하고 S_0를 만든다.

---

<a id="sec-07"></a>

## 7. 실행 순서와 시간 의미

### 7.1 S_k와 phase

S_k는 정확히 k번의 episode physics advance를 완료한 뒤의 장면 상태다. S_0는 초기화·warmup 후 상태다. 길이 H의 실행은 S_0부터 S_H까지 H+1개의 snapshot과 H개의 physics advance를 가진다.

| 순서 | phase | 입력 | 처리 | 출력 |
|---:|---|---|---|---|
| 0 | P0 GUARD | clock·writer 상태 | fatal 오류·generation 검사 | 이번 tick의 실행 허용 |
| 1 | P1 SNAPSHOT | S_k | raw pose·joint·contact·plant state 복사 | TruthSnapshot(k) |
| 2 | P2 SCHEDULE | raw snapshot·이전 event | 종료 고장 해제 후 시작 고장 활성, trigger 평가 | FaultState·world event |
| 3 | P3 OBSERVE | raw snapshot | 정상 mapping → 관측 고장 → process image | ControllerView(k) |
| 4 | P4 CONTROL | ControllerView(k) | k mod 4=0일 때 제어기 한 번 호출 | intents·controller events |
| 5 | P5 COMMAND | 신규 intents·queue | 정렬·지연·generation 검사, 최종 target 선택 | DeliveryDecision 목록 |
| 6 | P6 ACTUATE | P5까지 전달된 effective goal register·fault override | motion servo 120Hz, F6 target override, actuator 적용 | 실제 적용 target·구동 근거 |
| 7 | P7 EVALUATE | 불변 TickEvidenceFrame | 독립 발현 probe → predicate·시간 요구사항 평가 | fault audit·requirement·witness |
| 8 | P8 RECORD | 이번 tick의 immutable 자료 | trace writer에 하나의 tick bundle enqueue | record sequence·checksum |
| 9 | P9 ADVANCE | 적용된 target | physics 1/120초 한 번 진행 | S_(k+1) |

**motion servo도 120Hz다.** 30Hz는 고수준 상태기계·가상 I/O 의사결정 주기이며 관절 servo 주기가 아니다. GUI 렌더링은 P9 뒤에 필요할 때 수행하되 추가 physics step을 만들지 않는다.

최종 k=H에서는 snapshot과 남은 요구사항의 평가·종료만 수행한다. 새 신호 전달·제어 명령·고장 활성·actuation·physics advance를 실행하지 않는다. 예정 종료가 H인 고장은 활성 구간은 끝났지만 정상 경로의 재사용이 관측되지 않았으므로 restoration_status=UNOBSERVED로 닫는다. teardown의 고장 해제는 복구 근거가 아니다.

기본 캠페인은 작업 완료·중단·oracle FAIL 뒤에도 H까지 실행한다. 제어기는 terminal 상태에서 HOLD를 유지한다. 이렇게 해야 예정된 고장 해제와 후속 관측이 결과에 따라 잘리지 않는다. 사용자 취소·인프라 오류만 중간 종료할 수 있다. GUI의 stop-on-task 옵션은 시연 전용이며 확증 캠페인에서는 거부한다.

### 7.2 고장 구간

활성 구간은 **[A,E)**다. A에서는 적용하고 E에서는 적용하지 않는다. A와 E는 정수 physics step이다.

- time trigger의 at_step은 trigger match step b다. 실제 A=b+offset_steps에서 P2에 활성.
- world trigger는 S_k에서 처음 성립하면 같은 k의 P2에 활성.
- controller event trigger는 P4에서 발생한 event를 **k+1부터** 사용할 수 있다.
- 적용 지연 offset_steps는 trigger가 사용 가능한 최초 step에 더한다.
- trigger가 horizon 전에 발생하지 않으면 UNREACHED. 예정대로 적용된 것으로 세지 않는다.
- 한 tick에서 여러 world event가 있으면 event ID 사전순으로 처리하고 원래 snapshot step을 보존한다.
- 종료와 시작이 같은 tick이면 종료를 먼저 처리한다.
- strict rising-edge는 이전 False→현재 True만 인정한다. k=0에서 True인 경우 fire_on_initial=true일 때만 발생한다.

### 7.3 phase 표기

event의 전체 순서 키는 (episode_id, step, phase_order, local_sequence)다. wall-clock timestamp로 이벤트의 인과 순서를 재구성하지 않는다.

command intent를 같은 tick에 적용해도 물리적 결과는 S_(k+1) 이후에서 관측한다. “명령 수신 시점의 인터록”은 S_k로 검사하고 “명령 후 실제 이동”은 이후 snapshot으로 검사한다.

EventBus는 저장 이력과 trigger 전달 queue를 분리한다. world_edges는 현재 S_k의 edge를 한 번 생성한다. pop_ready_for_fault_trigger(k)는 available_step==k인 미소비 controller event만 한 번 반환한다. 두 집합의 event_id 중복은 오류다. 과거 history를 trigger에 재전달하지 않는다. world event는 ID 순서, controller가 순서화한 event는 emission sequence를 유지한다. source step은 원래 발생 step이며 availability 때문에 바꾸지 않는다.

trigger의 사용 가능 step b가 [arm.open_step,arm.close_step)에 있을 때만 match한다. 최초 match를 latch하고 A=b+offset, E=A+duration으로 예약한다. arm은 match만 제한하며 예약된 A가 close 이후여도 취소하지 않는다. match가 없으면 TRIGGER_UNREACHED/NOT_EXERCISED, match가 있었으나 A≥H이면 START_CENSORED/CENSORED다. SCHEDULED 상태와 trigger_match_step을 보존한다.

### 7.4 기준 루프 의사코드

~~~python
def execute_episode(ctx):
    ctx.reset_to_frozen_initial_state()
    previous_advance = None
    for k in range(ctx.horizon_steps):
        tick = ctx.clock.at(k)
        ctx.guard.check()
        truth = ctx.plant.snapshot(tick)
        world_events = ctx.events.world_edges(truth)
        ready_events = ctx.events.pop_ready_for_fault_trigger(k)
        trigger_events = ctx.events.merge_unique(world_events, ready_events)
        transitions = ctx.faults.advance(tick, truth, trigger_events)
        raw = ctx.mapper.sample(truth, tick)
        observed = ctx.faults.transform_observations(raw, tick)
        view = ctx.views.for_controller(observed, tick)
        output = ctx.controller.tick(view, tick) if k % 4 == 0 else None
        controller_events = ctx.events.commit_controller(output, available_step=k + 1)
        intents = ctx.commands.accept(output, tick)
        deliveries = ctx.commands.resolve(intents, ctx.faults, tick)
        actuation = ctx.plant.apply(deliveries, ctx.faults.actuator_overrides(), tick)
        frame = ctx.evidence.build(
            tick=tick, truth=truth, raw=raw, observed=observed,
            controller_view=view if output is not None else None,
            controller_output=output, intents=intents, deliveries=deliveries,
            actuation=actuation, source_events=world_events + controller_events,
            fault_transitions=transitions, previous_advance=previous_advance,
        )
        frame = ctx.faults.observe_realization(frame)
        verdicts = ctx.oracle.observe(frame)
        ctx.trace.append_tick(frame, verdicts)
        if not actuation.accepted:
            raise RuntimeError("SIM_ACTUATION_FAILED")
        previous_advance = ctx.plant.advance_one_step(render=ctx.render_due(k))
        ctx.clock.commit(previous_advance)
    return ctx.finalize_at(ctx.horizon_steps, previous_advance)
~~~

이 의사코드는 호출 순서를 규정한다. 예외 처리의 finally에서도 고장 해제, writer 종료, 결과 확정이 이루어져야 한다. 중간에 종료하더라도 마지막 실제 snapshot을 확보한다. 아직 관측하지 않은 미래의 조건을 PASS로 처리하지 않는다.

EvidenceBuilder는 view hash·controller 호출 여부·source event·delivery·전체 actuation receipt를 값 복사한다. RealizationProbe는 이 frame과 이전 receipt만 읽어 새 fault_audit를 가진 frame을 반환한다. S_k에서 검증하는 물리 효과는 이전 P6 이후의 advance와 연결한다. 현재 P6의 target commit을 S_k의 이동 증거로 사용하지 않는다. P9 AdvanceReceipt는 다음 tick의 previous_advance에 저장하고 마지막 것은 final_snapshot에 넣는다. 실패한 advance는 성공한 S_(k+1)을 생성하지 않는다.

finalize_at은 final_snapshot=true, actuation=null, 빈 신규 I/O·command·event를 가진 S_H frame을 만든다. oracle.observe(final_frame) 후 finalize(complete_horizon=true)를 호출한다. fault 종료가 H인 경우 정상 P2/P5를 실행하지 않았다는 사실을 보존한다.

---

<a id="sec-08"></a>

## 8. 데이터형과 공개 인터페이스

### 8.1 기본 자료형

domain 모델은 frozen dataclass를 사용하고, 내부 배열·사전도 tuple과 읽기 전용 mapping으로 변환한다. 타입 검사에서 bool을 int로 허용하지 않는다. ndarray·Warp array·USD handle을 domain 계층으로 전달하지 않는다.

~~~python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol, TypeAlias

Scalar: TypeAlias = bool | int | float
Vec3: TypeAlias = tuple[float, float, float]
Quat: TypeAlias = tuple[float, float, float, float]

class Quality(StrEnum):
    GOOD = "GOOD"
    MISSING = "MISSING"
    BAD = "BAD"

class CommandKind(StrEnum):
    POSE = "POSE"
    GRIPPER_GAP = "GRIPPER_GAP"
    JOINT_POSITION = "JOINT_POSITION"
    BOOL = "BOOL"
    HOLD = "HOLD"
    CANCEL_OPERATION = "CANCEL_OPERATION"

class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"

@dataclass(frozen=True)
class Tick:
    episode_id: str
    generation: int
    step: int
    hz: int = 120

@dataclass(frozen=True)
class BodyState:
    entity_id: str
    frame_id: str
    position_m: Vec3 | None
    orientation_wxyz: Quat | None
    linear_velocity_mps: Vec3 | None
    angular_velocity_rps: Vec3 | None
    valid: bool

@dataclass(frozen=True)
class JointState:
    joint_id: str
    kind: str
    position: float | None
    velocity: float | None
    measured_effort: float | None
    valid: bool

@dataclass(frozen=True)
class TruthSnapshot:
    tick: Tick
    bodies: Mapping[str, BodyState]
    joints: Mapping[str, JointState]
    contacts: tuple[tuple[str, str], ...]
    plant_state: Mapping[str, Scalar]
    complete: bool

@dataclass(frozen=True)
class SignalSample:
    signal_id: str
    dtype: str
    value: Scalar | None
    quality: Quality
    source_step: int | None
    source_seq: int | None
    publish_step: int | None
    deliver_step: int | None
    transport_seq: int | None
    generation: int
    reason: str | None

@dataclass(frozen=True)
class ControllerView:
    tick: Tick
    signals: Mapping[str, SignalSample]
    previous_command_receipts: tuple[PublicCommandReceipt, ...]
    active_operation_epoch: int

@dataclass(frozen=True)
class CommandIntent:
    command_id: str
    sequence: int
    episode_id: str
    generation: int
    operation_epoch: int
    issued_step: int
    actuator_id: str
    kind: CommandKind
    value: Scalar | tuple[float, ...]
    job_id: int

@dataclass(frozen=True)
class AppliedCommand:
    command_id: str
    actuator_id: str
    generation: int
    issued_step: int
    due_step: int
    applied_step: int | None
    status: str
    reason: str | None
    effective_value: Scalar | tuple[float, ...] | None

@dataclass(frozen=True)
class DeliveryDecision:
    intent: CommandIntent
    channel_generation: int
    due_step: int
    delivery_step: int | None
    status: str
    reason: str | None

@dataclass(frozen=True)
class ActuationReceipt:
    tick: Tick
    accepted: bool
    applied_commands: tuple[AppliedCommand, ...]
    targets_by_actuator: Mapping[str, Scalar | tuple[float, ...]]
    active_override_ids: tuple[str, ...]

@dataclass(frozen=True)
class PublicCommandReceipt:
    command_id: str
    operation_epoch: int
    actuator_id: str
    status: str
    receipt_step: int
    reason: str | None

@dataclass(frozen=True)
class AdvanceReceipt:
    from_step: int
    to_step: int
    engine_time_before_s: float
    engine_time_after_s: float
    success: bool
    rendered: bool

@dataclass(frozen=True)
class TickEvidenceFrame:
    tick: Tick
    truth: TruthSnapshot
    raw_signals: Mapping[str, SignalSample]
    observed_signals: Mapping[str, SignalSample]
    controller_called: bool
    controller_view_hash: str | None
    controller_output: Mapping | None
    intents: tuple[CommandIntent, ...]
    deliveries: tuple[DeliveryDecision, ...]
    actuation: ActuationReceipt | None
    source_events: tuple[Mapping, ...]
    fault_transitions: tuple[Mapping, ...]
    fault_audit: Mapping
    previous_advance: AdvanceReceipt | None
    final_snapshot: bool

@dataclass(frozen=True)
class RequirementResult:
    requirement_id: str
    verdict: Verdict
    first_witness_step: int | None
    last_evaluated_step: int
    obligation_count: int
    violated_count: int
    unresolved_count: int
    reason: str
~~~

JointState.kind는 revolute 또는 prismatic만 허용한다. position/velocity 단위는 각각 rad·rad/s 또는 m·m/s다. measured_effort가 없으면 0을 넣지 않고 null로 저장한다. contact는 설정한 수집 구간 동안 물리 backend가 보고한 body-pair 집합이다. 단순 근접 상태와 contact를 혼동하지 않는다.

### 8.2 추가 레코드의 필드

| 레코드 | 필수 필드 |
|---|---|
| Event | event_id, event_type, episode_id, generation, step, phase, local_sequence, available_step, entity_id?, payload |
| FaultSpec | fault_id, family, model_id, target_id, trigger, arm, offset_steps, duration_steps, parameters, require_exercised |
| FaultRuntime | trigger_match_step?, scheduled_start?, actual_start?, scheduled_end?, actual_end?, application_status, effect_status, restoration_status, first_effect_step?, boundary_effect_status, physical_hold_verdict, reason?, evidence_refs |
| ControllerOutput | new_commands, emitted_events, stage_id, job_id, active_operation_epoch, mission_status, reason? |
| EpisodeResult | run_id, attempt_id, completion_status, task_outcome, compliance_verdict, case_verdict, end_step, faults, requirements, errors, artifact_digest |
| ErrorRecord | code, severity, module, step?, phase?, message, context, exception_type?, traceback_path? |
| PerformanceSample | step, section, wall_ns, process_rss_bytes?, gpu_memory_bytes?, queue_depth |

event payload에는 JSON 호환 값만 사용한다. API 키·환경변수 전체·인증 문자열을 trace에 기록하지 않는다.

### 8.3 공개 인터페이스의 책임

~~~python
class PlantPort(Protocol):
    def load(self, scene_config: Mapping) -> None: ...
    def reset(self, initial_state: Mapping, generation: int) -> None: ...
    def snapshot(self, tick: Tick) -> TruthSnapshot: ...
    def apply(self, commands: tuple[DeliveryDecision, ...],
              overrides: Mapping, tick: Tick) -> ActuationReceipt: ...
    def advance_one_step(self, render: bool = False) -> AdvanceReceipt: ...
    def close(self) -> None: ...

class SignalMapperPort(Protocol):
    def reset(self, generation: int) -> None: ...
    def sample(self, truth: TruthSnapshot, tick: Tick) -> Mapping[str, SignalSample]: ...

class ControllerPort(Protocol):
    def reset(self, config: Mapping, initial_view: ControllerView) -> None: ...
    def tick(self, view: ControllerView, tick: Tick) -> Mapping: ...
    def close(self) -> None: ...

class OraclePort(Protocol):
    def reset(self, requirements: tuple[Mapping, ...], generation: int) -> None: ...
    def observe(self, frame: TickEvidenceFrame) -> tuple[RequirementResult, ...]: ...
    def finalize(self, final_tick: Tick, complete_horizon: bool) -> tuple[RequirementResult, ...]: ...

class TracePort(Protocol):
    def start(self, manifest: Mapping) -> None: ...
    def append_tick(self, frame: TickEvidenceFrame,
                    verdicts: tuple[RequirementResult, ...]) -> None: ...
    def finish(self, result_summary: Mapping, artifact_complete: bool) -> Mapping: ...
~~~

구현에서는 Mapping에 명시적 schema가 있는 typed model을 사용한다. 반환값의 필드를 임의로 늘리거나 줄이지 않는다. core import 시험은 Isaac이 설치되지 않은 환경에서도 성공해야 한다.

### 8.4 제어기의 정보 경계

ControllerView에는 공개 sensor 값, 품질, 수집·전달 시각, 이전 command receipt만 포함한다. 다음 항목을 전달하면 안 된다.

- raw signal, TruthSnapshot, USD prim, scene object
- 고장 시작·종료 예정, family, target, effect flag
- 정답 라벨, oracle 판정, 캠페인의 기대 결과

이 분리는 재현 가능한 시험을 위한 정보 계약이다. 동일 process에서 악의적인 Python 코드를 격리하는 security sandbox를 제공한다는 주장은 포함하지 않는다. 신뢰할 수 없는 외부 제어기의 실행은 별도 사양으로 다룬다.

PublicReceiptProjector는 내부 receipt를 whitelist로 새 객체에 복사한다. public status는 ACCEPTED/APPLIED/REJECTED/CANCELLED, reason은 null/INVALID_VALUE/UNAVAILABLE/CANCELLED만 허용한다. receipt_step은 실제 사건 시점이다. planned due/end, channel_generation, RESTORE_RESUME, fault ID/family, override ID, 내부 target 차이는 공개하지 않는다. 정상적인 ack 시각으로 지연을 추론하는 것은 허용한다. APPLIED는 endpoint의 backend acknowledgement이며 물리 목표 도달을 뜻하지 않는다.

---

<a id="sec-09"></a>

## 9. M01 설정·스키마·registry

### 9.1 내부 계층

| 계층 / 클래스 | 입력 | 출력·책임 | 오류 |
|---|---|---|---|
| A SafeConfigReader | UTF-8 YAML/JSON, 최대 1MiB | YAML safe loader, 중복 key 거부, 기본 자료형 | CFG_PARSE |
| B StructuralValidator | 읽은 객체 | 필수 key·타입·enum·추가 key 검사 | CFG_SCHEMA |
| C RegistryResolver | scene/controller/requirement ID | 등록된 버전의 builder와 선언 | CFG_REFERENCE |
| D SemanticValidator | 확장된 config | 시간·단위·resource·참조·범위 상호 검사 | CFG_SEMANTIC |
| E ConfigFreezer | 유효 config | 정렬된 resolved_config.json, SHA-256 | CFG_FREEZE |

임의 YAML tag, Python expression, eval, URL에서 받은 실행 코드, config 문자열의 module import를 금지한다. plugin은 설치된 entry point에서 미리 등록한 ID로만 선택한다. 파일 참조는 프로젝트 root 안의 정규화된 경로로 제한하고 순환 include는 거부한다. 기본 YAML의 include 기능은 제공하지 않는다.

### 9.2 최상위 schema

모든 object는 additionalProperties=false로 구현한다. 아래 “기본”은 parser가 자동 채울 수 있는 값이다. resolved_config에는 기본값까지 모두 저장한다.

| key | 타입 | 필수/기본 | 제약 |
|---|---|---|---|
| schema_version | string | 필수 | cellio.config/1.0 |
| scenario_id | ID | 필수 | 공통 ID 규칙 |
| profile | enum | isaac62_lockstep | isaac62_lockstep 또는 mock_lockstep |
| scene | object | 필수 | cell_id, revision, variant_id |
| clock | object | 기본값 있음 | physics_hz=120, controller_divisor=4 |
| controller | object | 필수 | id, revision, parameters |
| seeds | object | 필수 | scene, controller, fault: uint32 |
| episode | object | 기본값 있음 | horizon_steps, warmup_steps, reset_mode |
| faults | array | 빈 배열 | 길이 0 또는 1 |
| requirements | object | 필수 | preset, revision, parameter_overrides |
| artifacts | object | 기본값 있음 | output_root, trace_format, video |
| expected | object 또는 null | null | 라벨이 있는 시험에만 독립 기대 결과 |

scene.cell_id는 cell_a 또는 cell_b, revision은 1, variant_id는 canonical을 기본으로 한다. 외부 확장은 새 registry ID를 등록한다. episode.reset_mode의 기본은 fresh_process, 시험 전용으로 hard_reload를 허용한다. measured campaign에서 horizon_steps는 7200으로 고정한다.

requirements.parameter_overrides는 등록된 요구사항의 선언된 매개변수만 수정할 수 있다. controller parameters와 oracle parameters를 같은 객체로 공유하지 않는다. 변경된 요구사항은 별도 hash로 식별한다.

### 9.3 FaultSpec의 완전한 필드

| key | 타입 | 제약 |
|---|---|---|
| fault_id | ID | episode 내 고유 |
| family | enum | F1,F2,F3,F4,F5,F6 |
| model_id | enum | 아래 각 계열의 고정 model ID |
| target_id | ID | 타입·resource와 일치하는 공개 endpoint |
| trigger | object | kind, at_step?, event_type?, event_filter?, predicate_id?, fire_on_initial? |
| arm | object | open_step, close_step; 0≤open<close≤H |
| offset_steps | integer | ≥0 |
| duration_steps | integer | 1…1200 |
| parameters | object | 계열별 허용 key만 |
| require_exercised | bool | 기본 true; coverage 조건이며 task 판정과 별도 |

trigger.kind=at_step이면 at_step만 허용한다. controller_event이면 event_type와 event_filter가 필수다. world_level/world_rising이면 등록된 predicate_id를 사용한다. 조건부 trigger로 실제 E가 H를 넘는 경우 실행은 허용하되 CENSORED로 기록한다. 기본 확증 사례는 arm.close_step+offset_steps+duration_steps+recovery_window_steps≤H가 되도록 구성한다.

공개 필드 별칭 params/effect_rule은 허용하지 않는다. realization_rule_id는 family+model_id의 등록 규칙에서 선택하며 CompiledFaultPlan에 resolved resource, queue limit, rule hash와 함께 저장한다. 사용자 config에서 임의 expression으로 REALIZED를 선언하지 못한다. recovery_window_steps는 기본12이며 이 식의 compiler 상수다.

### 9.4 실행 가능한 형식의 예제 설정

다음은 장면 A의 그리퍼 닫힘 명령을 지연하는 사례다. expected=null은 물리 결과를 미리 생성하지 않는 일반 측정 사례라는 뜻이다.

~~~yaml
schema_version: cellio.config/1.0
scenario_id: cell_a.f4.gripper_delay
profile: isaac62_lockstep
scene:
  cell_id: cell_a
  revision: 1
  variant_id: canonical
clock:
  physics_hz: 120
  controller_divisor: 4
controller:
  id: reference
  revision: 1
  parameters:
    freshness_limit_steps: 12
    wait_timeout_steps: 360
    motion_timeout_steps: 960
    grip_timeout_steps: 240
    terminal_mode: hold
seeds:
  scene: 1000
  controller: 0
  fault: 0
episode:
  horizon_steps: 7200
  warmup_steps: 240
  reset_mode: fresh_process
faults:
  - fault_id: fault_primary
    family: F4
    model_id: fixed_delay_flush_resume_v1
    target_id: actuator.gripper
    trigger:
      kind: controller_event
      event_type: controller.stage_enter
      event_filter:
        stage_id: grasp
    arm:
      open_step: 0
      close_step: 4800
    offset_steps: 0
    duration_steps: 120
    parameters:
      delay_steps: 24
    require_exercised: true
requirements:
  preset: cell_a
  revision: 1
  parameter_overrides: {}
artifacts:
  output_root: artifacts/runs
  trace_format: jsonl_gzip
  video: false
expected: null
~~~

개발자가 임의로 선택할 설정 기본값을 남기지 않는다. 정상 예제는 faults=[]로만 바꾸고 나머지 조건을 유지한다. fault·scene seed를 Python 전역 random에 전달하지 말고 각각 독립 RNG 객체로 관리한다. 기본 6계열은 확률적 고장이 아니므로 fault seed는 추후 확장을 위한 provenance다.

### 9.5 의미 검증 항목

expected가 null이 아니면 completion_status, task_outcome, requirements, fault_coverage 중 하나 이상을 가진다. requirements는 등록 requirement_id→Verdict의 mapping, fault_coverage는 fault_id→coverage enum이다. 명시하지 않은 필드를 사후 기대값으로 추론하지 않는다. expected에는 metric threshold나 Python 식을 넣지 않는다.

controller.parameters의 허용 key는 예제의 freshness_limit_steps, wait_timeout_steps, motion_timeout_steps, grip_timeout_steps, terminal_mode뿐이다. 기본값은 각각12,360,960,240,hold다. 각 step 값은1..7200 정수, terminal_mode는hold만 허용한다. canonical suite는 기본값을 고정한다. initialize1200, wait_part1200, stop-min40, stop-dwell24, output-dwell60은 scene/controller revision에 속한 고정 상수다.

기본 world predicate ID는 world.pickup_present(A의 OBB 교차), world.fixture_loaded(B의 load 조건), world.gripper_closed(실제 gap/qdot/dwell), world.clamp_closed(B의 실제 q/dwell), world.part_dropped(part 중심Z<.700m)다. 존재하지 않는 셀의 predicate는 거부한다. event_filter는 등록 event payload의 scalar 필드에 대한 equality conjunction만 허용한다.

- F1은 bool signal만 대상으로 한다.
- F2/F3/F5는 관측 signal, F4는 명령 endpoint, F6는 지원하는 position actuator만 허용한다.
- truth 전용 필드·oracle 필드·존재하지 않는 prim은 fault target이 될 수 없다.
- step·duration·delay는 bool이 아닌 정수다. 음수와 NaN/Inf를 거부한다.
- response의 lower/upper/hold 구간이 모순되거나 물리 효과를 명령 이전 snapshot에서 요구하면 거부한다.
- 명령 endpoint가 참조하는 실제 DOF와 mimic resource를 resolve한다.
- 1.0.0은 여러 fault 인스턴스를 거부하므로 alias를 이용한 간접 중복도 허용되지 않는다.
- state/fault/requirement ID의 철자 오류를 자동 교정하지 않는다.
- 측정 캠페인의 asset·protocol lock 누락은 preflight 실패다.

**필수 시험:** T-CFG-01 중복 key, T-CFG-02 추가 key, T-CFG-03 잘못된 타입, T-CFG-04 target mismatch, T-CFG-05 시간 모순, T-CFG-06 plugin 미등록, T-CFG-07 hash 재현, T-CFG-08 순환/경로 이탈.

---

<a id="sec-10"></a>

## 10. M02 clock·event와 M05 signal 계층

### 10.1 M02의 내부 계층

| 계층 | 클래스 | 규칙 |
|---|---|---|
| A 시간 | StepClock | step은 runner만 증가. advance 성공 뒤에만 k+1 |
| B 순서 | PhaseGuard | P0→P9 순서를 검사. 되돌아가기·중복 advance 금지 |
| C 사건 | EventBus | world event는 현재 tick, controller event는 다음 tick부터 한 번만 trigger에 전달 |
| D trigger | TriggerEvaluator | raw predicate·기존 event만 사용 |
| E 검증 | ClockProbe | 실제 physics advance 수·절대 sim time·episode offset 대조 |

P9가 실패하면 k+1을 완료한 것처럼 기록하지 않는다. P8의 tick record는 decision record이며 physics advance의 성공과 별도다. 다음 snapshot 또는 별도의 advance receipt가 있어야 advance 완료로 인정한다.

동기식 controller가 느리면 wall-clock 진행이 느려질 뿐 source age를 추가하지 않는다. wall-clock watchdog에 걸리면 completion_status=INFRA_ERROR다. 이를 F3/F4가 발생한 것으로 바꾸지 않는다.

**필수 시험:** T-CLK-01 120회 advance=1초, T-CLK-02 control 30회, T-CLK-03 world/current-controller 인과 구분, T-CLK-04 H에서 명령 없음, T-CLK-05 double advance 거부, T-CLK-06 GUI/headless phase 동일.

### 10.2 M05의 내부 계층

| 계층 | 클래스 | 입력→출력 |
|---|---|---|
| A 바인딩 | SignalRegistry | signal ID→source·dtype·unit·freshness |
| B 정상 관측 | NominalSampler | immutable truth→정상 sample |
| C 전달 | ObservationRouter | 정상 sample→고장 변환/queue→delivered sample |
| D view | ProcessImageBuilder | 마지막 delivered sample→ControllerView |
| E 감사 | ObservationAudit | 정상·전달·소비 표본과 실제 source age 기록 |

정상 sampling은 매 physics step 수행한다. 제어기는 4 step마다 만들어진 view를 한 번 소비한다. “주입된 값”이 제어기의 실제 소비 시점에도 존재했는지는 별도 기록한다. 한 tick짜리 센서 고장이 두 제어 tick 사이에 끝나면 boundary 발현은 있어도 controller 소비는 없을 수 있다.

### 10.3 품질·시각·sample ID

정상 sampler의 signal별 source_seq는 첫 S0에서0, 이후 매 sample마다1 증가한다. raw sample은 아직 전송 전이므로 deliver_step/transport_seq=null이다. router가 실제 전달할 때 transport_seq를0부터 증가시킨다. MISSING placeholder는 value/source_step/source_seq/publish_step/deliver_step/transport_seq가 모두null이다. 이 placeholder를 실제 packet 전달로 세지 않는다.

- GOOD은 타입과 값이 사용 가능한 packet이라는 뜻이다. 물리 상태와 일치한다는 뜻은 아니다.
- BAD는 명시적으로 무효화된 sample이다. value=None, reason=NO_DATA와 같은 일반 품질 사유를 노출한다.
- MISSING은 아직 전달된 sample이 없다는 뜻이다.
- usable(sample,k) = quality==GOOD이고 source_step이 있으며 0≤k−source_step≤freshness_limit_steps.
- stale은 위 식으로 계산한 상태이며 품질 자체를 injector가 임의로 GOOD으로 바꿔 숨기지 않는다.
- 동일 cached sample을 다시 읽어도 source_seq·deliver_step을 갱신하지 않는다.
- deliver_step은 실제 전달이 발생했을 때만 바뀐다. publish_step은 원래 정상 sample이 생성된 시점이다.
- F5에서는 원본 sample을 전달하지 않으므로 source_step/source_seq=None이다. BAD 표시는 현재 step에 전달된 상태 알림이다.

private ScheduledObservation은 episode_id, episode_generation, signal_id, channel_generation, enqueue_seq, due_step, sample을 갖는다. channel_generation은 episode 초기0이며 전달 mode 전환/복원 전에 증가한다. enqueue_seq는 nullable source_seq 대신 queue identity로 쓰는 단조 증가 정수다. due 전달 전 episode·generation·channel_generation을 모두 검사한다. 이미 전달된 같은 episode의 cached sample을 읽는 행위는 queue 재전달이 아니며 metadata를 갱신하지 않는다. 이 envelope는 controller에 노출하지 않는다.

### 10.4 정상 sensor 계산

bool 센서의 geometrical intersection은 물체의 회전이 반영된 8개 꼭짓점 또는 정확한 OBB-AABB 교차 검사로 계산한다. 회전된 물체에 world AABB만 사용하면 coarse sensor임을 명시한다. 공식 예제의 presence sensor는 OBB-AABB SAT 교차로 고정한다.

관절 기반 sensor는 실제 q·qdot을 읽는다. target 값이나 command acknowledged를 실제 열린 상태로 대신하지 않는다. debounce/dwell은 정상 sensor의 일부이며 fault 적용 전에 수행한다. 비대상 signal의 debounce 상태를 fault로 변경하지 않는다.

oracle의 물리 판정은 M05의 bool 출력을 다시 읽지 않는다. raw snapshot에서 별도로 계산한다. 단, 입력 품질·source age 관련 요구는 제어기가 실제로 받은 observed sample을 근거로 검사한다.

**필수 시험:** T-SIG-01 bool/int 구분, T-SIG-02 source age, T-SIG-03 cached 재수신 시각 보존, T-SIG-04 오염 후 raw 불변, T-SIG-05 회전 물체 교차, T-SIG-06 debounce 초기화, T-SIG-07 제어기가 소비한 view hash 일치.

---

<a id="sec-11"></a>

## 11. M06 고장 엔진의 상세 사양

### 11.1 내부 아키텍처

~~~mermaid
flowchart TD
    R["FaultSpec과 target registry"] --> T["TriggerEvaluator"]
    T --> L["FaultLifecycle"]
    L --> O["ObservationOperator"]
    L --> Q["CommandDelayQueue"]
    L --> A["ActuatorOverridePlan"]
    O --> E["독립 발현 근거 수집"]
    Q --> E
    A --> E
    E --> J["FaultReport"]
~~~

| 계층 | 클래스 | 허용 side effect |
|---|---|---|
| A 계획 | FaultCompiler | 설정 검증·resource 해석만 |
| B 시각 | FaultScheduler | start/end·one-shot latch |
| C 변환 | ObservationOperator | sample 값/품질/queue 변경 |
| D 명령 | CommandDelayOperator | 전달 지연·복원 계획 |
| E 구동 | ActuatorHoldOperator | 물리 target override 요청만 |
| F 근거 | RealizationProbe | raw/observed/receipt 차이 계산, 물리 변경 없음 |

fault 모듈이 USD를 직접 조작하지 않는다. F6는 ActuatorPort의 정해진 override로 전달한다. 고장 구현의 성공 flag와 발현 판정을 같은 bool로 저장하지 않는다.

### 11.2 F1 — stuck_at

**model_id:** boolean_stuck_at_v1  
**parameters:** value: bool

활성 구간마다 정상 GOOD bool 값을 value로 치환한다. 정상 source_step/source_seq와 GOOD 품질을 유지하여 물리적으로 틀릴 수 있지만 인터페이스상 유효한 신호를 만든다. 정상 입력이 BAD/MISSING이면 임의로 GOOD으로 복구하지 않는다.

발현은 적어도 한 활성 tick에서 nominal.value와 observed.value가 다른 경우다. nominal이 원래 계속 같은 값이면 APPLIED이지만 VALUE_NOT_REALIZED다. 제어기 소비 tick에서 차이가 있었는지도 별도 계산한다.

E부터 해당 tick의 정상 sample을 전달한다. 물리 상태·다른 signal·controller state를 변경하지 않는다.

### 11.3 F2 — observation_freeze

**model_id:** frozen_sample_with_age_v1  
**parameters:** 빈 object

A에서 정상 sample을 값 복사해 한 번 전달한다. 이후 E 전까지 value·quality·source_step·source_seq·deliver_step·transport_seq를 모두 유지한다. publish_step도 원래 A다. view를 새로 만들어도 이 metadata는 바뀌지 않는다.

발현은 이후 활성 tick에서 source age가 정상 stream보다 커지는 경우다. 값 자체의 차이는 별도 지표다. duration=1이면 활성화돼도 age 차이가 생기지 않을 수 있다.

E에서는 frozen cache를 제거하고 fresh nominal sample을 전달한다. 새로운 timestamp를 붙여 freeze를 숨기는 변형은 1.0.0에 포함하지 않는다.

### 11.4 F3 — observation_delay

**model_id:** fifo_delay_flush_v1  
**parameters:** delay_steps: 1…120

활성 구간에 생성한 정상 sample j를 due_step=j+delay_steps로 queue에 넣는다. queue key는 private envelope의 (due_step, enqueue_seq)다. 원래 source metadata를 보존한다.

최초 지연 sample이 도착하기 전에는 마지막 delivered sample을 유지한다. A=0이고 이전 전달값이 없으면 MISSING이다. 과거 history index에서 더 오래된 값을 꺼내 이미 전달한 표본보다 뒤로 되돌리지 않는다.

E의 P2에서 기존 channel_generation을 무효화하고 queue를 비운다. E에 도착 예정인 packet도 제거한다. E의 fresh sample을 즉시 정상 경로로 전달한다. 폐기한 packet마다 RESTORE_FLUSH를 기록한다.

**기준 trace:** 활성 [4,10), delay=3이면 step 4–6에 source 3을 유지하고, 7·8·9에는 source 4·5·6을 전달한다. step 10에는 source 10을 전달하고 7·8·9의 잔여 packet은 폐기한다.

queue 최대 길이는 delay_steps+2로 잡는다. 초과는 QUEUE_OVERFLOW다. 초과분을 조용히 버려 확률적 packet loss로 만들지 않는다.

F3 발현은 이번 tick의 정상 새 sample이 보류되어 controller-facing sample의 age/availability가 달라진 근거로 판정한다. queue에 넣었다는 함수 호출만으로 발현을 선언하지 않는다.

### 11.5 F4 — command_delay

**model_id:** fixed_delay_flush_resume_v1  
**parameters:** delay_steps: 1…120

활성 구간에 새로 발행된 대상 command만 due_step=issued_step+delay_steps로 넣는다. nominal desired register는 즉시 최신 intent로 갱신하고, effective register는 마지막 delivered command를 유지한다. 이미 전달한 command를 되돌리지 않는다.

queue는 매 physics step 처리한다. 제어기 호출 step에서만 처리하지 않는다. 한 control tick에서 같은 endpoint로 두 command를 발행하면 CTRL_DUPLICATE_COMMAND다.

E에서 queue generation을 무효화한 뒤, E의 controller 업데이트가 있다면 그것까지 반영한 최신 nominal desired command를 한 번 전달한다. operation_epoch 또는 job이 종료됐으면 재개하지 않는다. 이미 effective인 command는 중복 적용하지 않는다. 복원 때문에 예정 due보다 일찍 재개했다면 RESTORE_RESUME과 실제 delay를 기록한다.

**기준 trace:** command가 4·8에 발행되고 활성 [4,10), delay=3이면 첫 command는 7에 전달된다. 둘째는 원래 11 예정이지만 10에 복원되어 한 번 재개된다. 실제 지연 2를 요청 지연 3으로 기록하면 안 된다.

v1에는 packet TTL을 추가하지 않는다. 제어기의 단계 deadline과 operation_epoch 무효화를 사용한다. abort 시 nominal desired를 안전한 HOLD 값으로 바꾸고 이전 operation의 queued command를 취소한다.

F4 발현은 유효 신규 command가 정상 즉시 전달 시점에 실제 보류됐다는 DeliveryDecision과, P6의 effective goal이 이전 상태를 유지한 근거를 함께 사용한다. F4가 지연하는 단위는 상위 intent이며, 이전에 전달된 goal을 따르는 120Hz servo는 계속 실행한다. P6는 미전달 desired를 제어 목표로 직접 읽지 않는다.

### 11.6 F5 — observation_dropout

**model_id:** explicit_invalid_v1  
**parameters:** 빈 object

매 활성 tick에서 BAD, value=None, source_step=None, source_seq=None, publish_step=deliver_step=k, reason=NO_DATA를 새 transport_seq와 함께 전달한다. 마지막 정상값은 private audit에만 보존한다. bool(None) 등으로 False로 바꿔 제어기에 전달하지 않는다.

정상적으로 usable한 sample이 BAD가 되면 availability 발현이다. 정상 입력 자체가 무효였던 구간은 새 고장 효과로 세지 않는다. E부터 fresh sample과 원래 quality를 전달한다.

### 11.7 F6 — actuator_block

**model_id:** position_target_hold_v1  
**parameters:** hold_tolerance_m=0.0005, conflicting_travel_min_m=0.010

A의 raw snapshot에서 독립 position driver의 q_hold 벡터를 복사한다. nominal slew 계산 뒤 F6 override를 적용하고 [A,E)에서 backend position target=q_hold, commanded velocity target=0을 commit한다. 실제 q/qdot에는 쓰지 않는다. stiffness·damping·effort limit을 유지하고 limiter 상태만 q_hold로 재초기화한다. v1 대상은 m 단위 prismatic gripper/clamp이며 revolute target은 CFG_FAULT_TARGET으로 거부한다.

실제 joint 위치를 매 tick teleport하거나 속도를 강제로 0으로 만들지 않는다. 센서는 실제 q와 qdot을 계속 읽는다. nominal desired target과 F6 뒤의 target을 모두 기록한다.

발현 근거는 두 수준이다.

1. **경계 개입:** nominal target과 hold target이 다르고 backend가 hold target을 실제 commit했다.
2. **물리 동작 보류:** 유효한 nominal command가 요청한 actuator coordinate와 캡처 값의 차이가 .010m 이상인 동안, 독립 driver의 max_i(abs(q_i−q_hold_i))≤.0005m가 30 elapsed steps, 즉31개 연속 boundary samples에서 유지됐다.

actuator coordinate는 gripper의 전체 opening gap, clamp의 q다. gap 차이 .010m를 각 finger .010m로 해석하지 않는다. driver/follower 변환은 binding manifest에 고정한다. boundary_effect_status가 확인된 첫 tick에 effect_status=REALIZED, coverage=EXERCISED로 한다. physical_hold_verdict는 PASS/FAIL/UNKNOWN/NOT_APPLICABLE로 별도 저장한다. 가능한 conflict 구간이30steps보다 짧으면 UNKNOWN, conflict 자체가 없으면 NOT_APPLICABLE이다. G05는 별도로 실제 physical hold PASS를 요구한다.

두 번째 조건은 실제 q가 deflect하면 실패할 수 있다. 이것을 로그의 active flag로 덮지 않는다. 이 모델은 위치 target 유지에 의한 구동 제한이며, 기계적인 jam·마찰·파손을 보정한 모델이라고 부르지 않는다.

장면 A는 actuator.gripper의 실제 구동 finger group, 장면 B는 actuator.clamp를 사용한다. mimic joint가 있으면 독립 driver와 follower의 실제 관계를 resolve하고, 서로 충돌하는 target을 쓰지 않는다.

E에서는 override를 제거하고 해당 endpoint limiter를 현재 실제 q로 초기화해 유효한 intent를 기존 limit으로 재개한다. gripper/clamp 고장 복원 때문에 비대상 arm RMP 상태를 reset하지 않는다.

### 11.8 생애주기와 발현 레코드

| 필드 | 값·의미 |
|---|---|
| application_status | REQUESTED, ARMED, SCHEDULED, ACTIVE, ENDED, UNREACHED, CANCELLED, ERROR |
| effect_status | NOT_OBSERVED, REALIZED, NOT_REALIZED, UNKNOWN |
| restoration_status | NOT_DUE, PATH_RESTORED, UNOBSERVED, ERROR |
| coverage | EXERCISED, NOT_EXERCISED, CENSORED, INJECTOR_ERROR |
| actual_start/end | 실제 처리한 step. 예정값과 별도 |
| first_effect_step | family별 독립 근거가 처음 확인된 step |
| controller_exposure_count | corrupted sample을 제어기가 실제 소비한 수 |
| value/age/quality discrepancy_count | 효과의 종류별 수 |
| delayed_packet_count | 실제 지연이 발생한 packet 수 |
| flushed_packet_count | 복원/reset으로 폐기한 수 |
| evidence_refs | trace의 episode·step·field·record sequence |

고장이 발현해도 작업은 성공할 수 있다. 고장이 활성됐지만 target을 쓰는 명령이 없으면 고장시험이 충분히 수행된 것이 아니다.

PATH_RESTORED는 E<H의 정상 tick에서 mode 정상화, 잔여 generation 무효화, 정상 observation/actuation 경로의 성공을 확인한 상태다. F1/2/3/5는 fresh 정상 sample, F4는 queue 폐기 후 정상 router/effective goal 처리, F6는 override 제거 뒤 정상 bounded target commit을 근거로 한다. 새 일반 명령이 없을 때 F4의 functional_reuse_status는 UNOBSERVED로 별도 기록한다. 경로 상태 복원과 실제 새 작업 명령의 성공을 동일시하지 않는다.

F6 physical_hold_verdict는 conflict 구간의 known q 오차가 한 번이라도 tolerance를 넘으면 FAIL이다. 위반 없이 최소30 elapsed steps가 관측되면 PASS, 필요한 관측이 부족하면 UNKNOWN이다. 이 판정은 boundary effect와 분리한다.

### 11.9 필수 경계 시험

각 계열에 A, A+1, E−1, E를 검사하는 fixture를 둔다. 추가로 duration=1, delay=1/120, delay>duration, A=0, E=H, trigger 미도달, source invalid, non-target 보존, reset 중 활성, 남은 queue 폐기, 잘못된 generation을 검사한다.

**시험 ID:** T-F1-01…08, T-F2-01…10, T-F3-01…12, T-F4-01…14, T-F5-01…08, T-F6-01…10. fixture 수는 아래 시험 매핑에서 확정하며 이름만 만든 빈 테스트를 허용하지 않는다.

---

<a id="sec-12"></a>

## 12. M03 Isaac 어댑터와 M04 장면·truth

### 12.1 어댑터 내부 계층

| 계층 | 클래스 | 책임 |
|---|---|---|
| A 부트스트랩 | IsaacApplication | SimulationApp 생성·확장 확인·종료 |
| B 장면 | StageBuilder | 단위·물리 scene·자산 reference·자체 geometry 생성 |
| C handle | HandleRegistry | articulation·body·DOF·mimic·tool frame 해석 |
| D physics | PhysicsStepper | 단일 advance 소유, timestep·solver·clock 확인 |
| E read | IsaacWorldReader | physics-backed pose·velocity·joint를 값 복사 |
| F write | IsaacActuatorAdapter | position target·surface velocity·gripper·clamp drive 적용 |
| G motion | RobotMotionAdapter | RMPflow 설정·좌표 변환·waypoint·실제 도달 검사 |
| H lifecycle | IsaacLifecycle | 초기화·hard reload·handle 무효화·shutdown |

### 12.2 검증한 6.2 API 경계

| 목적 | 사용 API | 구현 규칙 |
|---|---|---|
| 앱 시작 | isaacsim.simulation_app.SimulationApp | 생성 후 Omniverse 관련 import [S04,S24] |
| 물리 구성 | SimulationManager, PhysxScene | 120Hz·TGS·CPU dynamics·gravity 명시 [S05] |
| 관절 읽기/target | isaacsim.core.experimental.prims.Articulation | 이름 기반 get_dof_indices, 실제 state와 target 분리 [S06] |
| rigid state | experimental RigidPrim | authored USD transform 대신 물리 backend 상태 사용 [S06] |
| 자산 root | get_assets_root_path, verify_asset_root_path | 선택한 6.2 catalog를 확인 [S16] |
| 로봇 설정 | load_cumotion_supported_robot("franka") | URDF/XRDF/RMP 설정을 기록 [S08] |
| motion | RmpFlowController, CumotionWorldInterface | reset 성공 후 forward, world transform 갱신 [S07] |
| motion 자료형 | robot_motion.experimental.motion_generation | RobotState·named JointState·SpatialState [S14] |
| clamp | UsdPhysics.PrismaticJoint, DriveAPI | finite-force linear drive [S17] |
| conveyor | PhysxSurfaceVelocityAPI | 접촉으로 dynamic part를 이동 [S18] |

physics 설정은 PhysxScene의 set_steps_per_second(120), set_solver_type("TGS"), set_enabled_gpu_dynamics(False), set_gravity((0,0,-9.81))로 고정하고 실제 readback을 저장한다. SimulationManager의 switch_physics_engine("physx"), setup_simulation(dt=1/120, device="cpu"), enable_fabric(True), initialize_physics()를 순서에 맞게 사용하고 physics view가 유효한지 확인한다. episode에서는 step(steps=1, update_fabric=True)를 어댑터 안에서 사용한다. 확장의 실제 export·시그니처는 import smoke에서 확인하며 실패 시 legacy API로 조용히 대체하지 않는다. [S05]

SimulationApp 생성 전에는 cellio의 순수 Python 계층만 import한다. cellio.adapters.isaac62의 __init__도 앱을 자동 시작하지 않는다.

~~~python
from isaacsim.simulation_app import SimulationApp

app = SimulationApp({
    "headless": True,
    "disable_viewport_updates": True,
    "multi_gpu": False,
})
try:
    from cellio.adapters.isaac62.launcher import run_worker
    run_worker(app)
finally:
    app.close()
~~~

위 run_worker는 개발할 CellIO 함수다. app.update와 explicit physics step을 같은 tick에 중복 호출하지 않는다. 부트스트랩의 event pumping과 episode의 physics advance를 분리한다.

### 12.3 motion 계층

CPU dynamics 선택은 물리 engine device에 대한 계약이다. SimulationManager.get_device()의 Warp Device를 str/공식 속성으로 검사한다. CumotionWorldInterface(device="cpu")의 배열·transform composition 장치와 native RMP solver의 계산 장치를 동일하게 가정하지 않는다. RmpFlowController에 문서에 없는 device 인자를 추가하지 않고, 설치된 확장의 native 설정과 확인 가능한 compute 정보를 provenance로 기록한다. [S05,S14,S25]

하위 pick-and-place 제작은 공식 6.2 Franka 예제를 commissioning 기준으로 삼는다. 공식 standalone 예제 경로는 standalone_examples/api/isaacsim.robot_motion.examples/manipulation/pick_place.py이며 --robot franka 옵션을 사용한다. CellIO는 이 예제의 물체 위치 읽기를 시험 제어기의 숨은 truth 입력으로 재사용하지 않는다. [S15]

- motion target은 명세된 world-frame waypoint다.
- 각 waypoint 시작 시 measured joint state로 RMP reset을 수행한다.
- forward의 시간은 dt가 아니라 실제 platform clock이다. episode offset을 기록한다.
- cuMotion world binding의 transform을 매 servo tick 갱신한다.
- forward 반환 array는 즉시 복사한다. 다음 호출에서 바뀔 수 있는 buffer를 trace에 보관하지 않는다.
- RMP 내부 적분을 actual motion 성공으로 해석하지 않는다. 실제 TCP·관절 관측으로 완료를 판정한다. [S07,S14]
- motion timeout은 command가 실제 전달된 시점부터 960 steps다. 고수준 제어기의 전체 응답 deadline은 별도로 기록한다.

vendor arm joint는 panda_joint1…panda_joint7의 이름 순서로 잠근다. finger joint의 독립 driver·mimic 여부는 실제 자산에서 확인한다. 예측한 순서와 다른 자산을 자동으로 맞춰 실행하지 않는다.

planning_obstacles.yaml의 기본 외부 장애물은 Table과 Ground, padding=0이다. Robot·Part_000·Belt·Source/Destination/Fixture base·두 jaw는 RMP의 외부 obstacle 집합에서 제외한다. 의도적 접촉 구간은 고정 waypoint와 commissioning으로 확인하고 실제 PhysicsCollisionAPI는 그대로 유지한다. 모든 collider에 예제의 일반 padding을 일괄 적용하지 않는다. 이는 범용 충돌 회피 planner의 성능 평가가 아니라 고정 참조 동작이다. [S14]

### 12.4 TCP 보정 계약

CellTCP는 panda_hand 아래의 nonphysical frame으로 만든다. 초기 local translation은 (0,0,0.1034)m, local quaternion은 identity다. 이 값은 **초기 보정값**이며 선택한 자산의 실측된 파지 중심이라고 가정하지 않는다.

cuMotion robot description의 tool_frame_names()에서 선택한 site와 CellTCP 사이의 고정 변환을 구한다. 원하는 CellTCP 목표를 해당 motion site 목표로 변환한다. 모든 waypoint를 단순히 vendor tool frame 좌표로 대입하지 않는다.

commissioning에서는 home과 서로 다른 세 자세에서 다음을 검증한다.

1. FK와 실제 CellTCP 위치·방향 차이.
2. robot base를 알려진 offset만큼 옮겼을 때 world target 변환.
3. finger pad 사이의 실제 파지 중심과 CellTCP의 관계.
4. 수정한 offset과 선택 site를 assets/robot_profile.lock.json에 기록.

목표 downward quaternion은 wxyz=(0,0,1,0)이다. gripper finger와 fixture jaw가 서로 수직한 닫힘 방향을 갖는지 실제 scene에서 확인한다.

CellTCP의 authoritative truth는 world_T_hand(actual) × hand_T_CellTCP(frozen calibration)이다. nonphysical CellTCP Xform에 RigidBodyAPI를 붙이지 않는다. TCP velocity는 hand linear velocity + hand angular velocity×world offset으로 계산하고 angular velocity는 hand와 같다. finger collider의 local geometry와 실제 link pose를 결합해 world bounds를 계산한다. raw trace의 pose와 geometry manifest만으로 이를 재계산할 수 있어야 한다.

### 12.5 Truth 계층

contact reader는 최신 physics advance까지의 접촉 시작·지속·종료를 반영한 active pair 집합을 snapshot에 넣는다. 시작 event만 영구 보관하거나 매 tick event가 없다는 이유로 접촉이 끝났다고 추론하지 않는다. reset에서 active pair 집합을 비운다. static jaw의 contact ID는 해당 collider registry ID로 정규화한다. contact reporting을 사용할 수 없는 profile은 기본 oracle에 적합하지 않으므로 preflight에서 실패한다.

| 하위 계층 | 책임 |
|---|---|
| WorldSnapshotReader | 물리 body·joint·actual actuator state를 한 번 읽기 |
| SnapshotValidator | finite·frame·ID·필수 관측 검사 |
| ImmutableConverter | tuple·read-only mapping으로 고정 |
| IndependentPredicateInput | oracle에 필요한 raw geometry와 state 제공 |
| ProbeRecorder | 외부 라벨 검토에 필요한 원시 근거 식별 |

원시 body의 valid=false이면 측정할 수 없는 수치는 null이다. 0·identity pose로 채워 실제 측정처럼 보이게 하지 않는다. 필수 물리 상태가 누락되면 SIM_STATE_INVALID로 종료하고 진행 중 시간 요구는 UNKNOWN으로 닫는다. 선택적 contact stream만 없으면 이를 사용하는 요구만 UNKNOWN으로 처리할 수 있다.

**필수 시험:** T-ISAAC-01 import 순서, T-ISAAC-02 API export, T-ISAAC-03 DOF/mimic, T-ISAAC-04 TCP 변환, T-ISAAC-05 actual/target 구분, T-ISAAC-06 one-step owner, T-ISAAC-07 array alias 방지, T-ISAAC-08 잘못된 관측 종료.

---

<a id="sec-13"></a>

## 13. 참조 작업셀의 제작 명세

이 절의 좌표·gain·허용오차는 구현을 시작할 **명시적 설계값**이다. 실제 실행으로 검증한 결과가 아니다. commissioning 절차를 통과한 값을 한 번 잠근 뒤 모든 정상·고장 조건에 동일하게 사용한다.

### 13.1 공통 장면

모든 치수는 full dimension, 단위 m다. 별도 표시가 없는 rotation은 identity다.

| prim | 유형 | 위치 또는 중심 | 크기·속성 |
|---|---|---|---|
| /World/PhysicsScene | PhysX scene | — | TGS, CPU, 120Hz, gravity=(0,0,-9.81) |
| /World/Ground | static plane | Z=0 | collision |
| /World/Cell/Table | static box | (0.35,0,0.725) | (1.20,1.10,0.05), 상단 Z=0.750 |
| /World/Cell/Robot | Franka reference | base=(0,0,0.750) | yaw=0, scale=1, fixed base |
| /World/Cell/Parts/Part_000 | dynamic cube | 셀별 정의 | edge=0.040, mass=0.050kg |
| /World/Cell/Zones/* | Xform/visual | 셀별 정의 | collision·rigid body 없음 |
| /World/Cell/Materials/PartContact | physics material | — | static friction=.8, dynamic=.6, restitution=0 |

Franka 자산의 catalog 상대 경로는 /Isaac/Robots_Multiphysics/FrankaRobotics/FrankaPanda/franka/franka.usda다. 실제 reference dependency와 variant를 resolve하고 hash를 기록한다. 자체 primitive 장면은 코드로 제공하고 vendor 자산의 이용조건을 프로젝트 라이선스로 대체하지 않는다. [S13]

home q는 이름 순서로 [0,-0.785398163,0,-2.356194490,0,1.570796327,0.785398163]rad다. finger open target은 독립 finger당 0.040m, close는 0.000m다. vendor arm inertia·joint limit은 유지한다.

초기 gripper 설정: finger target slew=0.040m/s, force cap=20N/독립 finger, linear stiffness=4000N/m, damping=80N·s/m, pad static/dynamic friction=1.0/.8. 실제 USD 단위와 mimic 동작을 readback하고 기록한다.

Windows 프로파일 시운전에서 20 N 조건은 두 셀 모두 파지 안정화 시간 초과를 기록했다. 이후 독립 finger 힘 상한을 4 N으로 낮춰 시험했다. 각 설정의 결과와 변경 이력은 [ADR 002](docs/adr/002-gripper-commissioning.md)에 보존한다. 확증 실험에는 시운전을 마친 후보의 물리 설정을 동결해 사용한다.

PartContact를 Part_000, Table, Belt, Source/Base, Destination/Base, Fixture/Base, FixedJaw, MovingJaw의 실제 collider에 physics-purpose로 bind한다. pad material은 실제 finger pad collider에만 bind한다. friction/restitution combine mode는 모두 average, compliant contact는 OFF다. vendor descendant binding에 덮이지 않는 최종 composed material과 명시 질량을 readback해 잠근다. cube·belt·base·jaw·finger pad의 contactOffset=.001m, restOffset=0을 시작값으로 고정한다. 기타 arm collider의 vendor 설정은 유지한다. [S18]

### 13.2 허용하는 물리 조작

파지는 finger contact와 friction으로 수행한다. episode 안에서 물체를 손에 parenting하거나 fixed joint로 붙이거나 kinematic으로 바꾸거나 pose를 이동해서 성공을 만들지 않는다.

즉시 pose/joint position setter는 초기화에서만 사용한다. episode 중에는 bounded target·force·surface velocity 등 정식 actuator 경로를 사용한다. 실제 collision을 꺼서 fixture 접근을 통과시키지 않는다.

waypoint 초기 제한:

- hover TCP Z=1.030m.
- grasp/release TCP Z=0.815m.
- 빈손 TCP target 이동 cap=.15m/s.
- 운반 중 target 이동 cap=.10m/s.
- 목표까지 수직 거리가 .050m 이하인 마지막 하강 구간의 target cap=.04m/s. 그 이전에는 빈손/운반 cap을 적용한다.
- 실제 TCP 오차 ≤.004m, 회전 오차 ≤.05rad가 12 step 동안 유지되면 도달.
- 12 step 지속은 13개 연속 boundary sample을 뜻한다.

### 13.3 장면 A — conveyor pickup and transfer

| prim suffix /World/Cell 아래 | 유형 | 중심 | 크기 |
|---|---|---|---|
| Conveyor/Belt | kinematic box + surface velocity | (.45,-.22,.77) | (.14,.50,.04), 상단 .79 |
| Destination/Base | static box | (.45,.25,.77) | (.14,.14,.04) |
| Parts/Part_000 | dynamic cube | (.45,-.41,.815), settling 전 | (.04,.04,.04) |
| Zones/PickupBeam | 비물리 sensor volume | (.45,-.099,.81) | (.10,.002,.04) |
| Zones/Destination | 비물리 acceptance volume | (.45,.25,.815) | (.10,.10,.060) |

belt.run=true이면 world surface velocity target=(0,+.06,0)m/s다. 실제 belt body transform은 고정한다. 적용 surface speed는 매 step 최대 1.0m/s²의 가감속으로 target을 따른다. 물체의 속도·pose를 직접 설정해서 이송하지 않는다.

운반 물체의 sleep을 비활성화하거나 문서화한 wake 경로를 사용한다. surface velocity를 바꾸기만 하면 위에 있는 sleeping body가 반드시 깨어난다고 가정하지 않는다. [S18]

pickup.present가 감지되면 belt stop을 요청한다. 실제 정지와 최소 24 steps dwell 후 접근한다. 고정 pickup TCP=(.45,-.116,.815)다. 정상 commissioning에서 실제 정지된 물체 중심은 X∈[.447,.453], Y∈[-.122,-.110]이어야 한다.

정지 분포가 이 범위를 벗어나면 벨트 감속·센서 위치·정상 경로를 수정하고 다시 잠근다. 매 episode 물체를 waypoint에 맞춰 이동시키거나 raw pose를 몰래 controller에 전달하지 않는다.

주요 waypoint:

| 이름 | TCP 위치 |
|---|---|
| park | (.30,0,1.10) |
| pickup_hover | (.45,-.116,1.030) |
| pickup_grasp | (.45,-.116,.815) |
| transfer_mid | (.45,.08,1.030) |
| output_hover | (.45,.25,1.030) |
| output_place | (.45,.25,.815) |

최종 물리 목표: Part_000 전체가 output acceptance box 안에 있고, 수평 중심 오차 ≤.015m, 중심 Z=.810±.006m, 선속도≤.01m/s, 각속도≤.10rad/s, finger open, hand가 물체보다 .08m 이상 높아야 한다. 이 조건을 60 steps 지속해야 한다. destination.present와 controller DONE을 정답으로 사용하지 않는다.

### 13.4 장면 B — fixture load, clamp, process, unload

| prim suffix | 유형 | 중심 | 크기 |
|---|---|---|---|
| Source/Base | static box | (.45,-.24,.77) | (.14,.14,.04) |
| Fixture/Base | static box | (.50,.12,.77) | (.18,.16,.04) |
| Fixture/FixedJaw | static box | (.47,.12,.82) | (.02,.08,.06) |
| Fixture/MovingJaw | dynamic box, .25kg | (.59,.12,.82), open | (.02,.08,.06) |
| Fixture/ClampJoint | world-anchored prismatic | anchor=(.59,.12,.82) | axis X, q∈[-.070,0] |
| Destination/Base | static box | (.35,.30,.77) | (.14,.14,.04) |
| Parts/Part_000 | dynamic cube | (.45,-.24,.815), settling 전 | (.04,.04,.04) |
| Zones/FixtureLoad | 비물리 sensor volume | (.507,.12,.815) | (.056,.056,.060) |
| Zones/FixtureSeat | 비물리 최종 seat volume | (.50,.12,.81) | (.046,.056,.048) |

ClampJoint의 body0는 world anchor, body1은 MovingJaw다. localPos0=(.59,.12,.82), localPos1=(0,0,0), local rotation은 identity다. linear force drive는 stiffness=4000N/m, damping=100N·s/m, maxForce=30N, targetVelocity=0으로 시작한다. [S17]

실제 q는 MovingJaw의 physics-backed world X−.590으로 측정하고, qdot은 world 선속도의 X다. 다른 5자유도가 joint로 구속되는지 commissioning에서 확인한다.

clamp.open target=0, clamp.close target=-.065m, target slew=.040m/s다. 부품이 안착하면 접촉으로 실제 q는 대략 -.060m 근처에서 멈춘다. closed sensor가 true라는 사실만으로 부품 존재나 고정력을 보장하지 않는다.

load는 X=.507m에 해제하고 clamp가 fixed jaw 방향으로 밀어 최종 seat X=.500m에 정렬한다. load와 unload waypoint를 혼용하지 않는다. fixture_loaded는 중심X∈[.501,.513], Y∈[.114,.126], Z∈[.804,.816], 선속도≤.01m/s, part corner의 minX≥.481 및 maxX≤.550 조건이다. fixture_clamped는 최종 FixtureSeat box에 전체 corner가 포함되고 실제 q≤-.058m이며, fixed jaw–Part_000와 moving jaw–Part_000의 현재 active contact가 모두 있는 상태다. 이는 가상 process의 접촉·기하 조건이며 holding force나 실제 가공 부하의 유지 성능을 뜻하지 않는다. yaw/XY 극값에서도 load clearance와 접촉 정렬을 commissioning에서 확인한다.

주요 waypoint:

| 이름 | TCP 위치 |
|---|---|
| source_hover / source_grasp | (.45,-.24,1.030) / (.45,-.24,.815) |
| fixture_load_hover / fixture_load_place | (.507,.12,1.030) / (.507,.12,.815) |
| fixture_unload_hover / fixture_unload_grasp | (.500,.12,1.030) / (.500,.12,.815) |
| output_hover / output_place | (.35,.30,1.030) / (.35,.30,.815) |
| park | (.30,0,1.10) |

fixture clearance box는 X=[.39,.66], Y=[.02,.22], Z=[.79,1.00]다. 실제 hand/finger collider bounds와 이 box가 겹치지 않아야 robot.clear_fixture다. finger를 해제하고 충분히 물러난 뒤 clamp를 닫는다. clamp를 실제로 연 뒤 robot이 다시 진입한다.

process는 **가상 120-step dwell 동작**이다. 실제 절삭·가공을 모사하지 않는다. process.enable command를 받으면 plant counter는 시간을 센다. 물리 조건의 진실성은 별도 oracle이 검사하며, plant counter가 raw truth를 사용해 잘못된 명령을 자동으로 막지 않는다.

최종 물리 목표는 출력 중심 (.35,.30,.81)에 대한 장면 A와 같은 안정 배치 조건에 더해, 다음 과정의 독립 증거를 요구한다.

1. 실제 부품 안착.
2. 손이 fixture clearance 밖으로 이동.
3. 실제 clamp closure.
4. 부품 안착·clamp 조건이 120 steps 끊김 없이 유지.
5. 실제 clamp open 뒤 unload 시작.

### 13.5 공개 signal registry

| signal_id | dtype | 정상 source | freshness |
|---|---|---|---:|
| robot.at_waypoint | bool | 실제 TCP tolerance·12-step dwell | 12 |
| robot.motion_failed | bool | 실제 motion deadline과 측정 상태 | 12 |
| robot.completed_command_id | int | 실제 도달로 완료한 command sequence; 아직 없으면 유효한 sentinel -1 | 12 |
| gripper.open | bool | finger q≥.037m 및 abs(qdot)≤.003m/s, 4-step dwell | 12 |
| gripper.closed | bool | 전체 gap≤.055m 및 abs(qdot)≤.003m/s, 12-step dwell | 12 |
| gripper.gap_m | float | 실제 두 finger 사이 gap | 12 |
| destination.present | bool | 물체 OBB와 목적지 sensor box 교차 | 12 |
| pickup.present | bool | 장면 A의 pickup beam 교차 | 12 |
| belt.stopped | bool | 실제 적용 surface speed≤.001m/s | 12 |
| source.present | bool | 장면 B source box와 물체 교차 | 12 |
| fixture.present | bool | fixture load sensor box 교차 | 12 |
| clamp.open | bool | q≥-.002m, abs(qdot)≤.003m/s, 4-step dwell | 12 |
| clamp.closed | bool | q≤-.058m, abs(qdot)≤.003m/s, 4-step dwell | 12 |
| robot.clear_fixture | bool | 실제 hand/finger bounds와 clearance box 비겹침 | 12 |
| process.done | bool | process.enable 뒤 120-step plant counter 완료 | 12 |

qdot·gap 계산은 mimic topology를 반영한다. closed가 true여도 물체를 잡았다는 뜻은 아니다. 이 단순 센서와 더 강한 독립 물리 검사 사이의 차이는 설계상 명시한다.

robot.at_waypoint는 새 active POSE가 아직 없으면 GOOD/false, completed_command_id는 GOOD/-1, motion_failed는 GOOD/false다. 새 POSE 적용 시 at_waypoint=false로 시작하고 이전 completed sequence는 보존한다. 실제 도달 dwell을 마친 경우에만 새 sequence로 갱신한다. 완료를 기다린다는 이유만으로 정상 sample quality를 MISSING으로 만들지 않는다.

source presence box는 Source/Base 중심 XY에 두고 중심 Z=.815, full dimensions=(.10,.10,.060)으로 한다. 장면에 없는 signal은 registry에 넣지 않는다.

### 13.6 초기조건 분포

정상 commissioning은 변동 없는 고정 초기조건으로 시작한다. 이후 본평가 초기조건은 다음 유한한 분포로 고정한다.

| 변수 | 장면 A | 장면 B |
|---|---|---|
| 물체 초기 X offset | Uniform[-.001,.001]m | Uniform[-.002,.002]m |
| 물체 초기 Y offset | Uniform[-.004,.004]m | Uniform[-.002,.002]m |
| 초기 yaw | Uniform[-.03,.03]rad | Uniform[-.03,.03]rad |
| 질량 | .050kg 고정 | .050kg 고정 |
| 마찰·로봇 base·fixture | 고정 | 고정 |

seed마다 실제 초기값을 JSON으로 직렬화한다. 재실행은 숫자 seed를 다시 해석하는 것보다 동결한 initial_state.json을 우선 사용한다.

seed 생성은 SHA-256("cellio/1.0/"+cell_id+"/"+seed+"/scene")의 첫 16바이트를 big-endian 정수로 읽어 독립 RNG를 초기화한다. Python hash()는 사용하지 않는다. 초기 배치에서 geometry가 겹치면 사전에 정한 최대 20번 rejection sampling을 수행하고 모두 기록한다. 20번 실패하면 INIT_SAMPLING_FAILED다. 정상 결과를 보고 유리한 초기조건만 선택하지 않는다.

### 13.7 commissioning 순서

1. 단위·joint·mimic·TCP 변환 시험.
2. 물체 접촉 이송, finger 마찰 파지, 2초 hold, jaw의 실제 왕복.
3. 개별 waypoint reachability 및 실제 collision 확인.
4. 장면별 정상 고정 사례 20회 연속 실행.
5. seed 0–4의 pilot sampling과 별도의 결정론적 분포 극값 사례를 확인.
6. gains·TCP·waypoint·timing·oracle tolerance 잠금.
7. 이후 seed 1000–1019의 본평가를 시작.

조정 순서는 TCP/좌표→geometry clearance→motion cap→drive/solver→공개 sensor 설계다. 8시간의 commissioning 수정으로도 물리 파지가 안정되지 않으면 정상 장면 설계를 수정하고 새 version으로 재검증한다. 손에 물체를 붙이는 shortcut으로 논문 결과를 만들지 않는다.

<a id="sec-14"></a>

## 14. M07 명령·전달·구동 계층

### 14.1 내부 구조

| 계층 | 클래스 | 입력 → 출력 | 책임 |
|---|---|---|---|
| A 유효성 | CommandValidator | ControllerOutput → accepted intents | ID·타입·범위·episode·순서 검사 |
| B 의도 저장 | DesiredRegister | intent → 최신 desired | 아직 전달되지 않은 의도도 보존 |
| C 전송 | CommandRouter | intent·F4 → DeliveryDecision | due step·queue·취소·복원 |
| D 적용 선택 | EffectiveRegister | delivery → 현재 정상 target | 같은 actuator의 전달 순서·중복 처리 |
| E 저수준 | ServoTargetGenerator | 정상 target·실제 상태 → bounded target | RMP·slew·drive limit |
| F 고장 구동 | ActuatorOverride | bounded target·F6 → 최종 target | F6 target hold를 마지막에 적용 |
| G backend | IsaacActuatorAdapter | 최종 target → ActuationReceipt | 실제 API 호출 성공과 유효 target 기록 |

“전달됨”, “backend target 적용됨”, “물리적 목표 도달”은 서로 다른 사건이다. commanded target이 바뀌었다고 물체·관절이 이미 움직였다고 기록하지 않는다.

### 14.2 endpoint와 명령 형식

| actuator_id | kind | value | 범위·의미 |
|---|---|---|---|
| actuator.robot | POSE | (x,y,z,qw,qx,qy,qz) | world CellTCP 목표, m 및 unit quaternion |
| actuator.gripper | GRIPPER_GAP | float | 전체 finger gap, [0,.080]m |
| actuator.clamp | JOINT_POSITION | float | [-.070,0]m, 장면 B만 존재 |
| actuator.belt | BOOL | bool | true=운전, false=정지, 장면 A |
| actuator.process | BOOL | bool | true=공정 dwell 시작/유지, false=중지, 장면 B |
| actuator.robot / gripper / clamp | HOLD | 빈 tuple만 | adapter가 적용 시 실제 q를 캡처; 일반 scalar 0을 HOLD로 해석하지 않음 |
| control.operation | CANCEL_OPERATION | int | 취소할 이전 operation_epoch. command의 epoch는 새 값 |

POSE 허용 workspace는 X=[-.20,.80], Y=[-.55,.55], Z=[.79,1.30]m다. 이는 command validation box이며 collision-free 보장이 아니다. quaternion은 6장의 규칙을 적용한다. 범위를 넘으면 명령을 자동 clamp하지 않고 CTRL_COMMAND_RANGE로 처리한다.

F4의 기본 대상은 POSE·GRIPPER_GAP·JOINT_POSITION·BOOL의 일반 작업 명령이다. HOLD와 CANCEL_OPERATION은 신뢰하는 시험용 제어 경로로 즉시 처리한다. 따라서 이 모델의 결과는 비상 정지 신호까지 동일하게 지연되는 설비의 안전성 근거가 아니다.

### 14.3 ID·순서·취소

operation_epoch의 소유자는 controller의 operation manager다. 최초0이며 abort에서 이전값 e를 취소하는 CANCEL_OPERATION(value=e, operation_epoch=e+1)을 먼저 발행한다. 같은 output의 HOLD·belt/process OFF는 새 epoch e+1을 가진다. router는 current=e일 때 이 취소를 검증하고 e+1을 채택한 뒤 일반 validation/queue poll보다 먼저 e의 queue·desired·trajectory를 지운다. 새 epoch의 HOLD·OFF만 허용하고 같은 abort output의 새 작업 명령은 거부한다. 이미 취소된 같은 요청의 중복은 idempotent다. current epoch를 건너뛴 요청은 CTRL_OPERATION_EPOCH다. ControllerView에는 다음 호출부터 새 active_operation_epoch가 보인다.

- command_id는 attempt_id + ":cmd:" + sequence의 문자열이다. sequence는 episode 안에서 0부터 단조 증가하는 정수다.
- 같은 ID·동일 payload 재전송은 DUPLICATE로 기록하고 target을 재시작하지 않는다.
- 같은 ID·다른 payload, 역행 sequence는 CTRL_COMMAND_CONFLICT다.
- 다른 episode/generation/operation_epoch의 command는 STALE로 기록하며 적용하지 않는다.
- 한 ControllerOutput 안에서 같은 actuator에 서로 다른 일반 명령을 두 개 발행하면 오류다.
- CANCEL_OPERATION은 같은 tick의 일반 명령보다 먼저 처리한다. controller가 선언한 새 operation_epoch를 검증·채택하고 관련 queue·진행 trajectory·pending entry를 무효화한다.
- v1의 참조 실행은 job_id=0, 최초 operation_epoch=0의 작업 하나다. 취소 후 같은 episode에서 새 작업을 시작하지 않는다. 새 작업은 새 episode로 초기화한다.
- 같은 tick에 여러 명령이 같은 actuator에 전달되면 sequence가 가장 큰 유효 명령 하나만 backend에 적용한다. 나머지는 SUPERSEDED이며 APPLIED로 세지 않는다.
- command receipt는 다음 controller 호출에서 보인다. 현재 P4가 아직 실행하지 않은 P6의 receipt를 미리 읽을 수 없다.

### 14.4 명령 상태

정상 상태 전이는 ACCEPTED → QUEUED 또는 READY → DELIVERED → APPLIED다. APPLIED 뒤 물리 목표가 충족되면 별도 motion completion event를 만든다. CANCELLED, STALE, DUPLICATE, SUPERSEDED, REJECTED, BACKEND_ERROR는 전달 경로의 종결 상태다.

F4 queue는 due_step, sequence 순으로 정렬한다. episode의 최대 일반 command 수는 4,096, actuator별 pending queue는 256이다. 초과는 QUEUE_OVERFLOW이며 고장 효과로 위장하지 않는다. 복원 재개는 원래 command_id를 유지하되 reason=RESTORE_RESUME으로 기록한다. 새 command ID를 만들어 지연 시간을 0으로 초기화하지 않는다.

### 14.5 F6와 servo의 접점

F6는 nominal target·motion 계산을 기록한 뒤 최종 position target에 적용한다. hold 활성 시 slew 시작값을 캡처한 실제 q로 초기화하고 해당 drive의 commanded targetVelocity를 0으로 설정한다. 실제 qdot을 setter로 0으로 만들지 않는다. hold 해제 시 slew를 해제 시점의 측정 q에서 다시 시작한다.

mimic gripper는 독립 drive group에만 target을 쓰고 종속 joint는 자산의 coupling을 따른다. 양쪽을 따로 고정하여 mimic 제약과 싸우는 구현을 금지한다. 정상 desired·slew 결과·override 결과·실제 q를 모두 trace에 보존한다.

### 14.6 중단의 책임

runner는 controller가 ABORTED라고 선언했다는 이유만으로 자동 CANCEL/HOLD하지 않는다. 참조 controller가 중단 명령을 발행하고 oracle이 그 결과를 검사한다. 이 구분이 있어야 “중단 상태만 바꾸고 동작은 계속하는” 제어기 결함을 검출할 수 있다.

인프라 오류·사용자 취소 때는 runner가 별도의 teardown 경로로 physics를 정지한다. 이 teardown은 시험 대상 controller의 정상 중단·복구 성과에 포함하지 않는다.

**필수 시험:** T-CMD-01 중복·충돌, 02 stale generation, 03 같은 tick 최신 명령, 04 취소 우선순위, 05 복원 중 취소, 06 target 범위, 07 backend 거부, 08 APPLIED와 물리 완료 분리.

---

<a id="sec-15"></a>

## 15. M08 참조 제어기와 결함 변형

### 15.1 내부 계층과 상태 전이

reset(config, initial_view)는 아직 신호가 전달되지 않은 MISSING initial_view를 받아 내부 state만 초기화한다. 이 시점에 작업 명령·abort를 내지 않는다. 최초 control tick0에서 initialize의 stage_enter를 한 번 발행하고, 첫 entry action은 tick4다. S0 정상 sample을 reset 과정에서 중복 생성해 source_seq를 소모하지 않는다.

| 계층 | 책임 |
|---|---|
| InputGuard | 이번 상태에 필요한 observed signal의 dtype·GOOD·source age 검사 |
| StateMachine | 현재 stage, job_id, entry_pending, terminal 상태 보유 |
| EntryActionFactory | stage별 명령 생성, sequence 부여 |
| CompletionGuard | 실제 측정에서 만들어진 공개 완료 신호와 command ID 대조 |
| DeadlineManager | 상태 진입·명령 발행 기준의 고정 deadline |
| AbortPolicy | cancel·hold·belt/process 정지 명령 생성 |
| MutantPatch | 지정한 결함 하나만 적용하는 명시적 변형 |

controller stage_enter가 control tick k에서 발생하면 그 상태의 진입 명령은 **다음 control tick k+4**에서 처음 발행한다. k+4에서는 입력·취소·interlock을 다시 검사한 뒤 명령을 생성한다. 이 지연을 F4의 주입 지연으로 세지 않는다.

진입 명령을 발행한 호출에서 완료 조건을 검사할 수 있지만, POSE의 완료는 반드시 그 명령의 sequence와 robot.completed_command_id가 일치해야 한다. 과거 waypoint의 true 상태로 새 waypoint가 즉시 완료되면 안 된다. BOOL interlock은 명시된 dwell과 함께 검사한다.

각 controller 호출은 같은 입력에서 동일한 output을 반환한다. wall clock·파일·네트워크·raw truth·oracle 결과를 읽지 않는다. time.sleep을 사용하지 않는다.

A의 wait_stop은 belt off intent 발행 후 최소40 steps도 기다린다. 이는 최대 surface speed .06m/s와 명시된 감속에서 실제 정지 dwell24를 확보하기 위한 참조 정책이다. F1 stuck-high를 실제 정지의 증거로만 믿는 결함 변형과 구별한다.

### 15.2 입력 유효성과 deadline

필요한 입력의 quality가 GOOD이 아니거나 source_step이 없으면 즉시 ABORTED로 전이한다. source age=k-source_step이 12를 초과해도 ABORTED다. bool(None)을 false로 변환해 계속 진행하지 않는다.

대기 중 현재 요구하지 않는 신호까지 모두 검사하여 불필요한 중단을 만들지 않는다. 각 stage의 required_signals는 아래 전이 조건에 나오는 신호와 robot.motion_failed다. motion이 없는 단순 대기에서는 robot.motion_failed를 요구하지 않는다.

deadline의 시작점을 구별한다.

- 상태 대기: stage_enter.step부터 계산.
- grip/clamp 명령 응답: 해당 intent.issued_step부터 계산.
- 하위 motion: 해당 command의 applied_step부터 960 steps.
- 마지막 허용 step의 관측을 먼저 검사하고, 그 뒤 미충족 timeout을 판정한다.
- controller는 4 step마다 실행되므로 deadline 이후 첫 호출까지 최대 3 step의 관측 지연을 허용한다. oracle은 실제 deadline과 controller 반응 step을 따로 기록한다.

terminal DONE/ABORTED는 해당 episode에서 되돌리지 않는다. 정상 복구 성능은 경로 복원과 다음 초기화 사이클로 별도 검증한다.

### 15.3 장면 A FSM

각 motion은 13장의 waypoint를 사용한다. 괄호의 제한은 physics steps다.

| stage_id | 진입 명령 | 다음 상태 조건 | 다음 상태 |
|---|---|---|---|
| initialize | gripper open, belt off, robot park | 해당 robot 명령 완료 및 gripper.open, 제한 1200 | wait_part |
| wait_part | belt on | pickup.present, 제한 1200 | wait_stop |
| wait_stop | belt off | off intent부터 최소40 steps 및 belt.stopped true24 steps, 제한360 | approach |
| approach | pickup_hover | matching robot completion, 960 | descend |
| descend | pickup_grasp | matching robot completion, 960 | grasp |
| grasp | gripper gap=0 | gripper.closed, 240 | lift |
| lift | pickup_hover | matching robot completion, 960 | transfer |
| transfer | transfer_mid 뒤 output_hover를 순차 발행 | 두 matching completion, 각각 960 | place |
| place | output_place | matching robot completion, 960 | release |
| release | gripper gap=.080 | gripper.open, 240 | retreat |
| retreat | output_hover 뒤 park 순차 발행 | 두 matching completion, 각각 960 | verify_output |
| verify_output | 없음 | destination.present와 gripper.open이 60 steps 유지, 제한 360 | done |
| done | robot·gripper HOLD, belt off | terminal | done |
| aborted | CANCEL_OPERATION, robot·gripper HOLD, belt off | terminal | aborted |

여러 actuator의 initialize 명령은 같은 호출에서 발행할 수 있다. transfer/retreat의 두 번째 waypoint는 첫 번째 완료를 확인한 다음 control tick에서 발행한다. 해당 하위 순서도 substage_enter 이벤트를 남기며 상위 stage_enter를 중복 생성하지 않는다.

DONE은 제어기의 판단이다. 최종 task 성공은 별도 oracle의 실제 물체 검사로 결정한다. gripper.closed가 실제 물체 파지를 증명하지 않으므로 빈손 lift·물체 낙하는 oracle에서 실패할 수 있다.

### 15.4 장면 B FSM

| stage_id | 진입 명령 | 다음 상태 조건 |
|---|---|---|
| initialize | gripper open, clamp open, process off, park | open 신호 2개와 matching robot completion, 1200 |
| source_approach | source_hover | matching robot completion, 960 |
| source_descend | source_grasp | matching robot completion, 960 |
| source_grasp | gripper close | gripper.closed, 240 |
| source_lift | source_hover | matching robot completion, 960 |
| fixture_approach | fixture_load_hover | matching robot completion, 960 |
| fixture_place | fixture_load_place | matching robot completion, 960 |
| fixture_release | gripper open | gripper.open, 240 |
| fixture_retreat | fixture_load_hover 뒤 park | 두 matching completion 및 robot.clear_fixture, 각각 motion960, 완료 후clear 대기360 |
| clamp_close | clamp q=-.065 | 진입 명령 발행 완료 |
| wait_clamp | 없음 | clamp.closed 및 fixture.present, close intent부터 360 |
| process | process on | process.done, intent부터 240 |
| clamp_open | process off, clamp q=0 | 진입 명령 발행 완료 |
| wait_open | 없음 | clamp.open, open intent부터 360 |
| unload_approach | fixture_unload_hover | matching robot completion, 960 |
| unload_descend | fixture_unload_grasp | matching robot completion, 960 |
| unload_grasp | gripper close | gripper.closed, 240 |
| unload_lift | fixture_unload_hover | matching robot completion, 960 |
| output_approach | output_hover | matching robot completion, 960 |
| output_place | output_place | matching robot completion, 960 |
| output_release | gripper open | gripper.open, 240 |
| output_retreat | output_hover 뒤 park | 두 matching completion, 각각 960 |
| verify_output | 없음 | destination.present와 gripper.open이 60 steps 유지, 360 |
| done | robot·gripper·clamp HOLD, process off | terminal |
| aborted | CANCEL_OPERATION, robot·gripper·clamp HOLD, process off | terminal |

표의 순서가 다음 상태 순서다. 실패·invalid·timeout이면 aborted로 이동한다. process가 성공한 뒤에만 clamp_open으로 진행한다. wait_clamp와 wait_open은 이전 stage에서 발행한 실제 command ID와 issued_step을 보존하며 새 timer를 시작해 기다림을 늘리지 않는다.

가상 process는 BOOL false→true로 120-step counter를 새로 시작한다. true 재전송은 counter를 재시작하지 않는다. false는 진행 counter를 중단한다. 완료 bit는 false가 적용될 때 지운다. 물리 안착·clamp 상태의 진실은 oracle이 120 steps 전체에 대해 검사한다.

fixture_retreat에서 두 motion이 끝났는데 robot.clear_fixture가 false이면 마지막 motion completion부터360 steps만 추가 대기하고 abort한다. 초기 pose/waypoint 도달 시간과 이 입력 대기 시간을 서로 다시 시작해 무한 대기하지 않는다. process on이 a의 P6에서 적용되면 S_(a+1)의 elapsed는1, S_(a+120)에서 done이다. 실제 물리 조건의120-step 유지에는 S_a…S_(a+120)의121개 boundary 근거를 사용한다.

### 15.5 중단과 복구의 범위

참조 제어기는 오류를 발견한 호출에서 abort 이벤트와 취소·hold 명령을 함께 낸다. oracle 기준으로 abort 이벤트부터 8 steps 안에 취소·hold가 적용돼야 하고, 60 steps 안에 움직임이 정해진 정지 범위에 들어와야 한다. finite-force drive의 실제 감속을 측정한다.

hold는 현재 gripper·clamp q를 목표로 삼는 position-target 정지 정책이다. 실제 q로 target을 옮기면 기존 close target의 preload가 줄 수 있으므로 물체 유지(retention)를 보장하지 않는다. hold 이후 물체 pose와 접촉도 계속 기록한다. 자동으로 gripper를 열지 않는다. 기본 정책은 한 episode에서 자율 재시도하지 않는다. 다음 세 가지는 별도 결과다.

1. 고장 종료 후 신호·명령 경로가 정상 규칙으로 복원됨.
2. simulator hard reset 뒤 정상 셀 작업을 다시 수행할 수 있음.
3. 고장이 발생한 episode 안에서 작업이 계속돼 성공함.

세 번째가 실패해도 앞의 두 가지가 성공할 수 있다. “자동 복구 성공률”이라는 하나의 지표로 합치지 않는다.

### 15.6 표적 결함 제어기

각 변형은 참조 구현에 아래 변경 한 개만 적용한다. 파일·patch·hash를 공개한다. 모든 결함을 동시에 넣은 controller는 사용하지 않는다.

| mutant | 정확한 변경 | 표적 fault | 기대하는 검출 경로 |
|---|---|---|---|
| C_M1 | BAD/MISSING guard를 제거하고 unusable bool을 true로 사용 | F5 | 유효하지 않은 입력으로 작업 명령 발행 |
| C_M2 | 응답 timeout의 abort 전이를 제거하고 기다림 지속 | F6 | 응답 deadline·bounded abort 위반 |
| C_M3 | A wait_stop의 최소40-step·24-step dwell guard를 제거하고 stopped 값으로 즉시 전이; B fixture_retreat에서 matching completion 없이 clear 신호만으로 다음 상태 진행 | F1 stuck-high | belt 정지/fixture clearance 전제 위반 |
| C_M4 | gripper/clamp의 실제 완료 대신 해당 command의 APPLIED receipt를 완료로 취급 | F4 | 파지·clamp 물리 완료 전 후속 작업 |
| C_M5 | ABORTED 상태 전이는 유지하되 CANCEL/HOLD/stop 발행을 생략 | F2 | 중단 선언 뒤 actuator 동작 지속 |
| C_M6 | source age>12 검사를 제거하고 quality=GOOD인 오래된 값을 사용 | F3 | 오래된 입력에 의존한 새 작업 명령 |

M3의 장면별 두 수정은 한 셀의 실행에서는 해당 셀의 한 수정만 적용한다. M4에서 APPLIED는 actuator target을 받았다는 뜻이며 그 사실 자체를 조작하지 않는다. M5는 AbortPolicy의 발행 동작과 그에 수반되는 epoch 증가를 함께 생략하고 기존 active_operation_epoch를 유지한다. CANCEL 없이 새 epoch를 선언해 프로토콜 오류를 내는 변형으로 만들지 않는다.

각 mutant의 결함은 독립적인 작은 trace fixture에서 먼저 관측 가능해야 한다. 실제 시뮬레이션에서 matched reference와 mutant가 모두 같은 요구사항을 실패하면 그 시드는 해당 mutant를 구별한 증거가 아니다. 이를 MUTATION_UNINFORMATIVE로 남긴다. 정상 조건에서 이미 실패하는 mutant도 숨기지 않고 nominal diagnostic 결과를 별도로 제시한다.

**필수 시험:** T-CTRL-01 다음 control tick entry, 02 stale completion ID 차단, 03 invalid input abort, 04 age guard, 05 deadline 경계, 06 command 없는 terminal 선언 검출, 07 한 개 mutant patch, 08 deterministic mock replay.

<a id="sec-16"></a>

## 16. M09 독립 검사기: 규칙·시간 의미·기대 판정

### 16.1 내부 아키텍처

| 계층 | 클래스 | 책임 |
|---|---|---|
| A 입력 | OracleInputAssembler | immutable truth·실제 관측·intent·receipt·event를 결합 |
| B 기하 | GeometryPredicates | world transform·OBB·거리·속도·실제 joint 조건 |
| C 행동 분류 | ActionClassifier | 명령 endpoint·목표·job으로 의미 있는 동작 식별 |
| D 원자 판정 | PredicateRegistry | TRUE/FALSE/UNKNOWN와 근거 반환 |
| E 시간 규칙 | TemporalMonitor | invariant·admission·response·dwell·order |
| F obligation | ObligationStore | 발생 원인마다 독립 timer·witness·검열 상태 보유 |
| G 결과 | VerdictReducer | 요구사항별 판정과 task/compliance 결과 분리 |
| H 증거 | WitnessBuilder | 최초 위반·결정적 구간·trace 참조 생성 |

제어기의 stage 이름·DONE·injector active는 참고 기록이다. 물리적 성공의 정답으로 사용하지 않는다. ActionClassifier는 registry에 있는 실제 command target을 비교하고 unknown target은 OTHER로 기록한다. 새로운 waypoint의 의미를 임의 추정하지 않는다.

OraclePort.observe는 8장의 immutable TickEvidenceFrame 하나를 받는다. RealizationProbe가 읽은 raw/observed/command/actuation/event 근거를 그대로 사용해 독립적으로 검사한다. 이 frame은 oracle·trace 전용이며 ControllerView에 전달하지 않는다. phase는 record에 보존한 source event/receipt의 phase로 확인한다.

### 16.2 규칙 DSL

ActionClassifier는 endpoint·kind·실제 numeric goal과 적용된 명령 이력을 사용한다. gripper close 뒤 release 전 첫 상향 robot 명령은 LIFT, 이후 운반 이동은 CARRY다. gripper close의 직전 robot goal이 pickup/source/unload grasp 중 무엇인지로 위치를 구별한다. B의 load goal X=.507과 unload goal X=.500을 구별한다. clamp/process 명령은 endpoint와 방향으로 직접 분류한다. controller stage는 이 판정을 대신하지 않는다. 지원되지 않는 goal은 UNREGISTERED_ACTION으로 기록하고 action coverage를 UNKNOWN으로 닫는다.

임의 Python·eval·문자열 수식을 실행하지 않는다. 다음 JSON/YAML 구조만 허용한다.

| 필드 | 타입·규칙 |
|---|---|
| id | 고유 string |
| category | task / compliance / coverage |
| kind | invariant / admission / response / bounded_response_or_abort / dwell / order |
| scope | episode / operation / after_event |
| trigger | 등록 event matcher 또는 null |
| predicate | 아래 registry 표현식 |
| window | response 및 bounded_response_or_abort의 {lower_steps:int≥0, upper_steps:int≥lower} |
| dwell_steps | dwell 또는 response 안 지속 요구에 쓰는 int≥0 |
| abort_grace_steps | bounded_response_or_abort만 사용; canonical=3 |
| abort_matcher | bounded_response_or_abort의 같은 job/operation abort event matcher |
| applicability | 등록 predicate; 생략 시 항상 applicable |
| evidence | 기록할 body/joint/signal/command ID 목록 |
| required_coverage | bool; antecedent 미발생을 실험 누락으로 표시할지 |
| parameters | 등록 predicate의 typed parameters만 |

predicate 표현식은 {op: "pred", name: "body_in_box", args: {...}}, {op: "all", items: [...]}, {op: "any", items: [...]}, {op: "not", item: {...}}다. 깊이는 최대 8, 한 규칙의 원자 predicate 최대 64다. unknown parameter·빈 all/any·순환 참조는 CFG_ORACLE_SCHEMA다.

원자 registry:

| predicate | 입력과 계산 |
|---|---|
| body_in_box | body ID, 고정 zone transform·full dimensions; cube의 world 8개 corner를 zone frame으로 변환해 각 축 half-extent 이내인지 검사 |
| body_center_near | body ID, world target, 축별 inclusive tolerance |
| body_speed_below | 실제 선속도 norm 및 각속도 norm의 각각 inclusive 상한 |
| joint_in_range | 실제 joint q의 inclusive [lo,hi] |
| joint_speed_below | 실제 abs(qdot) 상한 |
| actual_gripper_open | 두 물리 finger q≥.037m 및 abs(qdot)≤.003m/s |
| actual_grasp_contact | 양 finger pad–Part_000 contact, gap [.034,.052]m, part–CellTCP 위치차 norm≤.018m |
| hand_clear_of | 실제 hand/finger collider의 world bounds와 지정 box가 비겹침 |
| belt_stationary | 실제 적용 surface speed norm≤.001m/s |
| signal_usable | GOOD, 값 존재, 정확한 dtype, 0≤k-source_step≤12 |
| delivered / applied | 해당 command_id의 실제 receipt 상태; 물리 완료와 별개 |
| expected_operation | event/command의 job_id·operation_epoch 일치 |
| controller_terminal | 기록된 terminal 선언; 물리 성공 판정에는 사용 금지 |
| all_targets_held | 해당 endpoint의 hold 적용 receipt 및 취소된 operation queue가 비어 있음 |
| physical_quiescent | arm 각 abs(qdot)≤.02rad/s, finger/clamp≤.003m/s, belt≤.001m/s |
| fixture_loaded | 13장 load 위치·높이·clearance 조건 |
| fixture_clamped | 13장 final seat·q≤-.058m·양측 jaw의 실제 active contact |
| process_history_valid | process 적용부터 완료까지 fixture_clamped 및 hand clear의 120-step 연속 근거 |
| goal_placed | 13장 output 조건 전체와 60-step dwell |
| path_restored | fault 종료 뒤 정상 신호/명령 경로 재사용 및 이전 generation packet 미적용 |

필수 state가 없거나 invalid이면 해당 원자는 UNKNOWN이다. known value가 임계값을 넘으면 FALSE다. 경계에서 임의로 추가 epsilon을 적용하지 않는다. 거리·속도·joint의 허용 오차는 parameter로 한 번만 적용한다. 부동소수점 비교는 float64, inclusive 범위를 사용하고 fixture에는 정확히 경계·경계±1e-6 사례를 넣는다.

### 16.3 3값 논리와 4종 최종 판정

원자 결과는 TRUE/FALSE/UNKNOWN이다. all은 FALSE가 하나라도 있으면 FALSE, 모두 TRUE일 때 TRUE, 나머지는 UNKNOWN이다. any는 TRUE가 하나라도 있으면 TRUE, 모두 FALSE일 때 FALSE, 나머지는 UNKNOWN이다. not UNKNOWN은 UNKNOWN이다.

시간 요구사항의 내부 상태는 PENDING을 허용하지만 최종 파일에는 다음 값만 저장한다.

| 판정 | 의미 |
|---|---|
| PASS | 선언된 유한 구간에서 요구사항 충족을 뒷받침하는 충분한 증거 |
| FAIL | 선언된 요구사항의 실제 반례 또는 관측 완료된 deadline 위반 |
| UNKNOWN | 관측 누락·미완료 시간창·truth 불명으로 결정 불가 |
| NOT_APPLICABLE | antecedent/applicability가 발생하지 않았음이 확인됨 |

FAIL은 흡수 상태다. 뒤에 물체가 목표에 도착하거나 task reset을 눌러도 이전 위반을 지우지 않는다. antecedent가 한 번도 발생하지 않았다는 사실만으로 response를 PASS로 세지 않는다. required_coverage=true인 NOT_APPLICABLE은 도구의 오판은 아니지만 해당 시험의 coverage는 미충족이다.

### 16.4 시간 알고리즘

**Invariant:** 관측 범위의 한 FALSE로 즉시 FAIL. 전체 범위를 끝까지 관측했고 모두 TRUE면 PASS. FALSE는 없지만 UNKNOWN 또는 조기 중단이 있으면 UNKNOWN.

**Admission:** 특정 명령이 step a의 P4/P5에서 발행·전달될 때 미리 수집한 S_a의 전제조건을 검사한다. 현재 명령이 만든 미래 상태를 전제조건으로 소급 사용하지 않는다.

**Response:** trigger a마다 독립 obligation을 만들고 [a+L,a+U]의 모든 허용 boundary를 검사한다. 물리 명령 효과의 L은 최소 1이다. U의 마지막 snapshot에서 만족해도 PASS다. 마지막 snapshot을 검사한 뒤 실패 여부를 닫는다.

**Bounded response or abort:** R-04 전용 kind다. 물리 응답 witness는 [a+1,a+U] 안에서만 인정한다. 같은 job/operation의 abort는 a부터 a+U+abort_grace_steps까지 인정한다. 물리 응답이 U까지 없으면 abort grace 동안 PENDING을 유지하되 늦은 물리 응답은 성공으로 받지 않는다. deadline+grace의 마지막 event를 처리한 뒤, 물리 window가 전부 known 미충족이고 abort도 없으면 FAIL, 물리 window에 가능한 UNKNOWN witness가 남으면 UNKNOWN이다. abort 선언의 적법한 실제 적용은 R-05/R-06이 별도로 검사한다. 물리 응답U=PASS, 응답U+1만있음=FAIL atU+3, abortU+3=PASS, 종료U+2=UNKNOWN의 네 경계 사례를 T-ORA-11로 고정한다.

**Dwell:** m steps 지속은 m+1개의 연속 boundary sample이다. first_true_step=s, 현재 k이면 k-s≥m일 때 충족한다. FALSE가 나오면 연속 구간을 끊는다. UNKNOWN이 나오면 definite 구간과 possible 구간을 별도 유지한다. deadline까지 definite witness는 없지만 UNKNOWN을 TRUE로 놓았을 때 만족 가능한 구간이 있으면 UNKNOWN이다. 모든 가능한 witness가 사라졌을 때만 FAIL이다.

**Order:** 같은 job_id의 event partial order를 검사한다. 순서 위반은 실제 후행 action이 발생했을 때 FAIL이다. 후행 action이 없으면 별도 deadline 규칙으로 평가한다. 소프트웨어가 보고한 stage 순서만 검사하지 않고 물리 전제와 실제 적용 command를 포함한다.

**여러 obligation:** key=(requirement_id, trigger_event_id, job_id)로 저장한다. 중복 trigger는 같은 event ID만 무시한다. 서로 다른 명령의 timer를 덮어쓰지 않는다.

종합 판정은 해당 category 안에서 FAIL 우선, 그다음 UNKNOWN, applicable PASS, 모두 NOT_APPLICABLE 순서다. task와 compliance category는 합치지 않는다.

### 16.5 기본 요구사항 preset

아래 표가 두 셀 preset의 필수 내용이다. ID의 접두 A/B는 장면별 고유 인스턴스에 붙인다.

| ID | category·kind | 적용 범위와 정확한 조건 |
|---|---|---|
| R-01 STATE_VALID | compliance·invariant | 필수 물리 상태·시간·generation이 유효. 누락은 ERROR와 해당 판정 UNKNOWN |
| R-02 INPUT_FRESH | compliance·admission | 아래 command별 필수 signal이 usable일 때만 새 일반 작업 명령 허용. cancel/hold 제외 |
| R-03 COMMAND_ORDER | compliance·order | 취소된 operation의 일반 명령 적용 금지, monotonically increasing sequence |
| R-04 RESPONSE_OR_ABORT | compliance·bounded_response_or_abort | intent issued a부터 grip240/clamp360/robot960 안 실제 응답 또는 a+deadline+3까지 같은 job abort 선언 |
| R-05 ABORT_APPLIED | compliance·response | abort a에서 a+8까지 cancel·해당 actuator hold·belt/process off 적용 |
| R-06 ABORT_STOPPED | compliance·response+dwell | abort a에서 a+60까지 physical_quiescent 구간이 시작하고12 steps 유지; witness 종료≤a+72 |
| R-07 PICK_PRECONDITION | compliance·admission | pickup/source grasp command에서 해당 실제 part가 지정 grasp 위치 XY±.006, Z±.010m에 있고 robot이 실제 grasp waypoint tolerance 안 |
| R-08 GRASP_BEFORE_LIFT | compliance·admission | lift/unload_lift command 적용 시 actual_grasp_contact |
| R-09 CARRY | compliance·invariant | lift 이후 release/abort까지 part–TCP relative translation이 grasp 당시 값에서 norm .018m 이내. 처음 24 steps는 접촉 settling 구간으로 명시 제외 |
| A-R10 BELT_BEFORE_APPROACH | compliance·admission | pickup_hover/접근 명령 적용 시 실제 belt stationary가 24 steps 유지 |
| B-R10 CLAMP_CLEAR | compliance·admission | clamp close 적용 시 fixture_loaded와 hand_clear_of(clearance) |
| B-R11 PROCESS | compliance·invariant/order | process on 적용 시 실제 clamp closed·seat·clearance, 전체120 steps 유지. source→load→clamp→process 순서를 명령/물리 근거로 확인 |
| B-R12 UNLOAD | compliance·admission | unload approach 적용 전 clamp q≥-.002m 및 4-step 실제 open dwell |
| R-13 DECLARED_DONE | compliance·admission | controller DONE 선언 시 그 시점까지 goal_placed witness가 존재 |
| R-14 GOAL | task·response | 시작부터 H까지 최종 goal_placed. B는 process_history_valid도 필요 |
| R-15 RESTORE | coverage·response | 실제 발현 fault가 E<H에서 종료되면 E..E+12 안 정상 경로 재사용 |
| R-15B NO_TAIL | coverage·invariant | 종료 E부터H까지 이전 channel generation packet 적용·종료된 override의 재적용 없음 |
| R-16 RESET_ISOLATION | coverage·invariant | reset chain의 다음 episode에 이전 generation·queue·latch·oracle 상태가 남지 않음 |

R-04의 응답 검사는 command별이다. 같은 operation이 명시적으로 취소되면 해당 명령의 “완료” 요구를 계속 강제하지 않고 취소 근거로 obligation을 닫는다. cancel 자체가 누락되면 이 예외를 사용하지 못한다. 작업 성공이 필요한 R-14는 취소로 PASS가 되지 않는다.

INPUT_FRESH의 signal mapping:

| 새 동작 | 의사결정에 필요한 신호 |
|---|---|
| A pickup 접근 | pickup.present, belt.stopped |
| grasp / source_grasp / unload_grasp | robot.at_waypoint, robot.completed_command_id |
| lift / source_lift / unload_lift | gripper.closed |
| transfer / output 접근(운반 중) | gripper.closed 및 이전 matching robot completion |
| release | robot.at_waypoint, robot.completed_command_id |
| B clamp close | fixture.present, robot.clear_fixture |
| B process on | fixture.present, clamp.closed, robot.clear_fixture |
| B unload 접근 | clamp.open |
| DONE 선언 | destination.present, gripper.open |

입력 validity를 확인해 abort하는 행위는 stale signal을 작업 결정에 사용한 것으로 세지 않는다. observation이 오래됐다는 이유만으로 controller의 올바른 abort까지 compliance FAIL로 만들지 않는다.

### 16.6 작업 결과와 case 결과

EpisodeResult는 다음 축을 독립적으로 가진다.

- completion_status: COMPLETED / INFRA_ERROR / CANCELLED.
- task_outcome: SUCCESS / CONTROLLED_ABORT / FAILED / UNKNOWN.
- compliance_verdict: PASS / FAIL / UNKNOWN / NOT_APPLICABLE.
- case_verdict: PASS / FAIL / UNKNOWN / NOT_SCORED.
- fault_coverage: EXERCISED / NOT_EXERCISED / CENSORED / INJECTOR_ERROR.

task_outcome 결정 순서는 goal_placed와 B의 과정 조건이 충족되면 SUCCESS, 아니고 적법한 abort·hold·정지가 검증되면 CONTROLLED_ABORT, 완전한 H까지 관측했으나 둘 다 아니면 FAILED, 관측이 부족하면 UNKNOWN이다. controller가 ABORTED라고 말하기만 한 경우는 CONTROLLED_ABORT가 아니다.

case_verdict는 독립 기대 라벨이 있는 fixture/acceptance case에서만 평가한다. 의도한 위반이 검출되어 requirement FAIL과 일치하면 case_verdict=PASS다. 탐색적/기본 본평가의 expected=null은 NOT_SCORED이며 결과를 본 뒤 expected를 채워 정답률을 높이지 않는다.

### 16.7 손으로 검산할 수 있는 경계 사례

메모리는 bounded history로 관리한다. 원자 predicate의 최근1201개 3값 sample, 현재 미종결 obligation 최대4096, 최초 witness의 trace 참조만 보존한다. 전체 TruthSnapshot 배열을 메모리에 누적하지 않는다. 완료 obligation은 결과 요약을 writer에 넘기고 active store에서 제거한다. limit 초과는 ORACLE_CAPACITY다.

| 사례 | 입력 | 반드시 나와야 하는 결과 |
|---|---|---|
| T-GOLD-01 F3 | active [4,10), d=3, nominal sample 매 step | 4–6은 마지막 source=3, 7–9는 source=4,5,6, 10은 source=10; source7–9 폐기 |
| T-GOLD-02 F4 | active [4,10), d=3, cmd at4,8 | cmd4 delivered7, cmd8 RESTORE_RESUME10, 실제 delay 각각3·2 |
| T-GOLD-03 deadline | trigger4, window[5,9], S9에서 최초 TRUE | PASS |
| T-GOLD-04 censor | 위 조건인데 마지막 S8 | UNKNOWN |
| T-GOLD-05 dwell | S7,S8,S9 TRUE | dwell2 PASS, dwell3 아직 미충족 |
| T-GOLD-06 no effect | F1 stuck-false, nominal이 전 구간 false | activated=true, value_effect=false, NOT_EXERCISED |
| T-GOLD-07 horizon | H12, active[6,12) | 최종 S12 평가, 정상 경로 복원은 UNOBSERVED |
| T-GOLD-08 causality | stage_enter4, next control8 | fault activation5, entry command8, 4에 command 없음 |
| T-GOLD-09 unknown | window5..9 중7만 UNKNOWN, 나머지 FALSE, dwell0 | FAIL이 아니라 UNKNOWN |
| T-GOLD-10 absorbing | step8 순서 위반, step40 목표 성공 | task SUCCESS 가능, 해당 compliance FAIL 유지 |

**필수 시험:** T-ORA-01 논리표, 02 admission/effect 구별, 03 inclusive deadline, 04 dwell, 05 censored horizon, 06 다중 obligation, 07 FAIL 흡수, 08 NA/coverage 분리, 09 category 분리, 10 온라인·오프라인 일치, 11 물리 응답 deadline과 abort grace 분리.

<a id="sec-17"></a>

## 17. M10 episode runner와 M11 campaign 실행기

### 17.0 모듈 내부 계층

| 모듈·계층 | 클래스·주요 메서드 | 책임과 경계 |
|---|---|---|
| M10 A 준비 | EpisodeBootstrap.prepare | frozen config/initial state 검증, plant·논리 객체 조립 |
| M10 B 수명주기 | EpisodeLifecycle.transition | 허용 lifecycle 전이와 generation 소유 |
| M10 C 실행 | PhaseExecutor.tick | P0–P9의 단일 호출 소유자 |
| M10 D 근거 | EvidenceBuilder.build | 현재 tick의 불변 frame 구성 |
| M10 E 종결 | EpisodeFinalizer.finalize | final snapshot·oracle closure·result summary |
| M10 F 정리 | ShutdownGuard.close | 예외에도 writer·physics·app 정리, task recovery와 구분 |
| M11 A 계획 | CampaignPlanner.freeze | matrix 전개·initial state·순서·plan hash |
| M11 B ledger | AttemptLedger.transition | PLANNED부터 각 시도의 실제 상태를 원자적 기록 |
| M11 C 배치 | SequentialScheduler.next | GPU1 worker, 계획된 순서, 완료 중복 실행 방지 |
| M11 D 감독 | WorkerSupervisor.launch/poll | spawn·heartbeat·watchdog·종료 |
| M11 E 재개 | ResumePlanner.reconcile | 미실행·실패·재시도 분리, 새 attempt 생성 |
| M11 F 검증 | CampaignFinalizer.audit | plan↔attempt↔artifact 완전한 accounting |

명령 실행 중 plan 파일에는 단일 supervisor의 OS file lock을 유지한다. 두 CLI가 같은 plan을 동시에 실행하면 RUN_PLAN_LOCKED로 거부한다. 이전 supervisor의 RUNNING 레코드에 대응하는 worker가 사라졌으면 WORKER_LOST/INFRA_ERROR로 확정하고, 같은 attempt를 다시 사용하지 않는다.

### 17.1 프로세스 수명주기

| 상태 | 진입 처리 | 정상 다음 상태 | 실패 결과 |
|---|---|---|---|
| PLANNED | run/attempt 레코드를 먼저 생성 | PREFLIGHT | INFRA_ERROR |
| PREFLIGHT | schema·asset hash·공간·profile 검사 | BOOTING | INFRA_ERROR |
| BOOTING | spawn으로 worker·SimulationApp 시작 | LOADING | INFRA_ERROR |
| LOADING | stage 구성·physics/view 초기화 | WARMUP | INFRA_ERROR |
| WARMUP | 초기조건 적용, 240 steps settling | READY | INFRA_ERROR |
| READY | S0 검사, 모든 논리 state 초기화 | RUNNING | INFRA_ERROR |
| RUNNING | P0–P9, H회 advance | FINALIZING | 오류/취소 뒤 FINALIZING |
| FINALIZING | 최종 snapshot·oracle·trace 확정 | CLOSED | 부분 artifact와 오류 |
| CLOSED | writer·physics·app·worker 종료 | 없음 | 종료 오류 별도 기록 |

canonical main campaign은 **fresh_process**다. 매 attempt 새 worker를 시작하고 한 episode가 끝나면 닫는다. GPU별 동시에 worker 하나다. fork를 쓰지 않고 spawn을 쓴다.

hard_reload는 reset-chain·soak 전용 검증 프로파일이다. 같은 worker에서 episode를 순차 실행하되 동시에 한 episode만 존재한다. 실제 Isaac stage와 physics handle을 폐기하고 새로 구성한다. 단순 pose setter reset을 hard_reload라고 부르지 않는다.

### 17.2 물리 초기화와 S0

초기화 순서는 다음으로 고정한다.

1. 앱과 필요한 확장 시작, engine/device를 CPU 프로파일로 선택.
2. 새 stage 생성, metersPerUnit=1, Z-up 설정.
3. physics scene·robot asset·primitive·material·joint 작성.
4. PhysicsScene 설정 및 SimulationManager의 초기화, Fabric 활성화.
5. 실제 physics view가 유효해진 뒤 wrapper·DOF index·actual-state reader 생성.
6. 로봇 home, finger open, clamp open, object 초기 pose·zero velocity를 초기화 경로로 적용.
7. 물체 sleep 정책·drive·collision·material binding readback.
8. motion controller를 measured state로 초기화하고 belt/process off, 논리 fault 없음.
9. 240 physics steps warmup. 초기 접촉 안정화와 state validity 검사.
10. episode generation을 확정하고 신호·event·oracle·command·RNG의 episode 상태를 비운 뒤 S0부터 기록.

초기조건 manifest에는 setter에 입력한 initial_state와 warmup 후 실제 S0를 둘 다 저장한다. 물체는 table과 겹치지 않도록 시작하며 중력 settling이 자연스럽게 일어나게 한다. 초기 pose를 맞추기 위해 warmup 후 물체를 다시 옮기지 않는다.

### 17.3 reset의 정확한 의미

reset 시작 시 먼저 generation을 증가시키고 이전 callback·message·command를 거부한다. 다음 항목을 명시적으로 제거한다.

| 상태군 | reset 후 조건 |
|---|---|
| observation/command queue | 길이 0, 이전 generation 적용 0건 |
| fault | capture cache·active override·trigger latch 없음 |
| FSM | initialize, timer 없음, entry_pending만 초기 상태, job/sequence 초기값 |
| motion | 이전 trajectory 없음, 새 measured state로 reset |
| signal | source sequence 초기값, age는 새 S0 기준, 이전 last-good 없음 |
| oracle | obligation·witness·FAIL 없음; 이전 episode 결과 파일은 보존 |
| plant | robot·object·clamp·surface speed·process counter 초기 상태 |
| event | edge detector 이전 값과 availability queue 초기화 |
| RNG | 독립 scene/controller/fault stream을 동결한 seed로 초기화 |
| trace | 이전 파일 closed, 새 attempt namespace |

hard_reload 후 동일 initial_state의 fresh_process S0와 비교한다. body position 및 prismatic q 최대 오차 1e-4m, revolute q 1e-4rad, orientation geodesic 오차 1e-3rad, 선속도 1e-3m/s, 각속도 1e-3rad/s 이내를 초기 기준으로 한다. 이 범위는 S0 reset 검사이며 전체 trajectory의 bitwise 일치를 요구하지 않는다.

hard_reload가 실패해 fresh_process로 대체해 성공해도 해당 reset 시험은 실패로 남긴다. 지원하지 않는 reset 기능을 성공한 것으로 표시하지 않는다. normal campaign은 fresh_process이므로 reset 기능의 실패가 숨지 않게 두 결과를 분리한다.

### 17.4 watchdog와 종료

| 항목 | 기본 wall-clock 제한 | 처리 |
|---|---:|---|
| 최초 앱 boot/extension 준비 | 900초 | ENV_BOOT_TIMEOUT |
| heartbeat 송신 | 2초마다 | progress의 부재와 worker 생존 구분 |
| RUNNING의 physics 진전 없음 | 60초 | SIM_STALL |
| 한 episode의 RUNNING | 1800초 | RUN_WALL_TIMEOUT |
| writer backpressure 한 번 | 5초 | TRACE_BACKPRESSURE |
| 정상 finalization | 60초 | TRACE_FINALIZE_TIMEOUT |
| graceful worker 종료 | 20초 | 이후 강제 종료, WORKER_TERMINATED |
| soak 전체 보호 한도 | 24시간 | 두 최소 조건 미달이면 INCOMPLETE |

warmup·asset 준비·컴파일 시간은 episode RUNNING 시간에서 제외하고 별도 기록한다. wall watchdog는 프로그램 정지 방지용이며 sim-time fault의 일부가 아니다.

사용자 취소는 CANCELLED, 인프라 예외는 INFRA_ERROR다. 마지막 확보 가능한 snapshot까지만 oracle을 닫는다. 물리 상태를 더 읽을 수 없다면 마지막 완결 trace step을 사용하고 열린 obligation은 UNKNOWN으로 처리한다. 강제 종료 뒤 final snapshot을 지어내지 않는다.

### 17.5 campaign 계획과 재개

CampaignPlanner는 실행 전에 전체 run 목록을 campaign.plan.json에 쓴다. 각 run은 cell, arm, initial_state_id, repeat_index, controller_revision, config_hash, protocol_hash를 가진다.

RunPlan의 추가 필드는 run_id, suite_id, chain_id(nullable), chain_slot(nullable:control/predecessor/post_reset), benchmark_mode(B0/B1/B2 또는null), test_control이다. test_control은 {kind:full_h} 또는 reset suite 전용 {kind:fault_start_offset,steps:4/8}, {kind:fault_end_offset,steps:12}다. 이는 controller config가 아니라 시험 harness의 입력이며 manifest/protocol hash/run_id에 포함한다. canonical suite에서는 full_h만 허용한다. worker의 P0 guard가 미리 잠근 이 조건을 정확한 sim step에 평가해 TEST_RESET_CANCEL로 종결한다. supervisor 메시지의 wall-clock 도착 시각으로 reset step을 정하지 않는다.

- run_id: 위 의미적 설정·코드·환경·자산 hash를 포함한 canonical JSON의 SHA-256 앞 24 hex.
- attempt_id: UUID4. 실행 RNG와 공유하지 않는다.
- repeat_index는 ID에는 포함하되 초기조건 RNG에는 섞지 않는다.
- 동일 run의 재시도는 새 attempt_id, parent_attempt_id, retry_reason을 가진다.
- 자동 재시도 기본값 0. resume는 아직 시작하지 않은 run만 실행한다.
- 실패한 run의 재시도는 --retry-failed 옵션으로만 생성하며 원래 attempt를 지우지 않는다.
- 대표 통계는 사전 지정한 첫 attempt 기준이다. 재시도 성과는 별도 표다.
- 개발 버전을 수정했다면 새 protocol/code hash의 campaign으로 수행한다. 이전 실패를 새 버전의 분모에서 몰래 제거하는 방식으로 해석하지 않는다.

단일 GPU의 run 순서는 initial_state block마다 seed로 permutation한다. 순서 RNG seed=20261118로 고정하고 plan에 실제 순서를 저장한다. 정상 arm부터 항상 실행해 cache·온도·시간 변화와 arm이 결합하지 않게 한다.

### 17.6 오류 분류

| 접두 | 예 | 종료·재시도 정책 |
|---|---|---|
| CFG_ | unknown key, wrong type, invalid target | 실행 전 거부, 자동 재시도 없음 |
| ENV_ / ASSET_ | 버전·dependency·checksum·VRAM 문제 | 인프라 오류, 원인 수정 후 새 attempt |
| CTRL_ | 잘못된 명령·controller exception | 도구 실행 오류로 기록; 고장 robustness 성공 아님 |
| SIM_ | state invalid, lost handle, double step, NaN | 즉시 중단·가능한 artifact 확정 |
| QUEUE_ | overflow, invalid reorder | 즉시 중단, silent drop 금지 |
| TRACE_ | disk full, checksum, writer failure | 불완전 artifact와 parent 오류 기록 |
| ORACLE_ | schema·engine exception | 판정 불가, PASS 금지 |
| EXPECTATION_ | 기대 라벨 불일치 | case FAIL, 실행 자체는 완료될 수 있음 |

사용자가 의도한 소프트웨어 결함 mutant는 CTRL_ 예외를 던지도록 만들지 않는다. 유효한 I/O를 사용하면서 잘못된 판단을 하는 구현으로 검증한다.

**필수 시험:** T-RUN-01 모든 lifecycle 전이, 02 boot 실패, 03 physics stall, 04 취소, 05 writer 실패, 06 final-S_H, 07 resume 중복 방지, 08 재시도 보존, T-RESET-01–12의 상태군별 초기화.

T-RESET 번호는01 generation/늦은callback,02 observation queue,03 command queue/operation,04 faultcache/override,05 FSMtimer,06 motiontrajectory,07 signalage/seq,08 oraclehistory,09 physicalstate/processcounter,10 eventedge/queue,11 RNG/initialstate,12 writerhandle/namespace다. 각 시험은 해당 상태를 비정상 predecessor에서 채운 뒤 reset해 초기값과 비교한다.

---

<a id="sec-18"></a>

## 18. M12 기록·artifact·무결성

### 18.1 내부 계층

| 계층 | 책임 |
|---|---|
| RecordBuilder | tick 자료를 불변 JSON 모델로 복사 |
| SchemaValidator | 타입·필수 필드·finite 값 검사 |
| SequenceHasher | record index와 hash chain 생성 |
| BoundedWriterQueue | 최대 256 tick bundle, 유실 없는 backpressure |
| GzipJsonlWriter | deterministic gzip stream, partial 파일 쓰기 |
| ArtifactFinalizer | flush·fsync·rename·파일별 SHA-256 |
| IntegrityVerifier | 순서·누락·참조·digest·marker 검증 |
| ArtifactReader | 분석용 streaming read, untrusted pickle 미사용 |

raw trace는 JSONL.gz가 기본이다. pandas·Parquet는 기본 runtime 의존성이 아니다. 추후 Parquet export는 원본 hash를 가진 파생 자료이며 raw를 대체하지 않는다.

### 18.2 필수 파일 구조

~~~text
artifacts/<campaign_id>/<run_id>/<attempt_id>/
  manifest.start.json
  config.resolved.json
  environment.lock.json
  requirements.lock
  assets.lock.json
  protocol.lock.json
  initial_state.json
  initial_snapshot.json
  trace.jsonl.gz
  events.jsonl.gz
  commands.jsonl.gz
  faults.json
  requirements.json
  result.json
  performance.json
  errors.jsonl
  manifest.final.json
  COMPLETE
~~~

manifest.start는 시작 전에 쓴다. events/commands는 trace로부터 finalization에서 추출하는 파생 파일이다. 별도 writer 두 개가 서로 다른 사실을 독립 생성하지 않는다. report는 분석 단계에서 reports/ 아래에 만들고 raw 디렉터리를 수정하지 않는다.

선택적 GUI video는 videos/에 저장하며 video 없이 모든 수치·판정을 재생성할 수 있어야 한다. 카메라 영상이 물리 oracle의 유일한 근거가 되면 안 된다.

### 18.3 tick bundle schema

한 JSONL record의 공통 필드:

| 필드 | 내용 |
|---|---|
| schema_version | cellio.trace/1.0 |
| record_index | 0부터 연속 정수 |
| record_type | episode_start / tick / final_snapshot / episode_end |
| previous_advance | 직전 P9 AdvanceReceipt 또는 첫 S0의 null |
| controller_view_hash | 실제 controller 호출에 전달한 view의 canonical hash, 비호출은 null |
| run_id, attempt_id, episode_id, generation | 실행 식별자 |
| step | tick/final snapshot의 정수 k |
| tick_kind | NORMAL / FINAL |
| truth | 8장 TruthSnapshot의 JSON 표현 |
| raw_signals | 정상 mapping의 모든 공개 sample |
| observed_signals | controller에 보인 모든 sample |
| controller_called | bool |
| controller_output | 호출했으면 명령·event·stage·status, 아니면 null |
| intents, deliveries, applied | 신규 command의 세 경계 |
| actuation | ActuationReceipt 전체; final frame은 null |
| actuator_targets | 정상 effective·slew 결과·override 최종 target |
| fault_audit | 활성·발현·종료·복원 사건 및 근거 |
| oracle_updates | 상태 변경과 witness, 생략된 동일 상태는 이전 값 유지 |
| performance | 각 계층 wall_ns, writer queue 깊이 |
| events | 이번 tick의 정렬된 world/controller/tool event |
| previous_hash, record_hash | 아래 hash chain |

final_snapshot은 truth·previous_advance·oracle closure를 갖는다. raw_signals/observed_signals는 빈 object {}, intents/deliveries/applied/events는 빈 array [], actuation/controller_output/controller_view_hash는 null, controller_called=false다. 직전 observed sample을 새로 전달한 것처럼 복사하지 않는다. offline oracle은 이전 기록의 마지막 observed 값을 보유할 수 있지만 source/delivery 시각을 바꾸지 않는다.

매 tick의 truth에는 최소 Part_000·hand·CellTCP·finger link poses·robot 실제 joints·clamp joint·belt/process state가 포함된다. finger bounds는 고정 collider geometry manifest와 이 pose로 재계산한다. contact stream을 쓰는 actual_grasp_contact와 fixture_clamped를 기본 preset으로 사용하므로 finger–part contacts와 장면B의 두 jaw–part contacts는 필수다. contact API가 준비되지 않으면 이 요구를 근거 없이 TRUE로 바꾸지 않고 commissioning을 실패 처리한다.

### 18.4 직렬화와 hash

record의 actuation은 ActuationReceipt 전체를 직렬화한다. applied와 actuator_targets는 이 객체의 파생 projection이며 불일치하면 TRACE_SCHEMA다. fault_transitions와 source_events는 각각 fault_audit.transitions와 events로 일대일 저장한다. readonly Mapping은 JSON object, tuple은 JSON array로 변환한다. final frame에서 object를 []로 바꾸지 않는다. TraceReader는 previous_advance·controller_view_hash를 포함해 TickEvidenceFrame을 손실 없이 재구성한다.

canonical payload는 UTF-8 JSON, key 사전순, ensure_ascii=false, allow_nan=false, separators=(",",":")로 직렬화한다. 객체 필드에 Python repr·메모리 주소·ndarray 문자열을 넣지 않는다. 무한대·NaN은 오류다.

record_hash = SHA256(previous_hash의 32 raw bytes + record_hash를 제외한 canonical record bytes)다. 첫 previous_hash는 32 zero bytes다. record_hash를 자기 자신의 입력에 포함하지 않는다. line 끝의 LF는 record hash 입력에서 제외한다. gzip의 mtime은 0으로 고정한다.

trace의 episode_end record에는 outcome·end step·오류 요약만 넣고 artifact_digest나 manifest hash를 넣지 않는다. trace를 완전히 닫아 마지막 record_hash를 얻은 뒤 result.json의 artifact_digest를 채운다. hash가 자기 자신을 참조하지 않게 한다.

manifest.final은 각 완결 파일의 size와 SHA-256을 가진다. manifest.final 자신과 COMPLETE는 이 파일 목록에 넣지 않는다. COMPLETE의 내용은 manifest.final.json 전체 bytes의 SHA-256 한 줄이다. result.json의 artifact_digest는 manifest digest로 소급 채우지 않고 trace 마지막 record_hash를 사용한다.

### 18.5 원자적 종료·부분 결과

기록 중에는 .partial 확장자를 쓴다. 종료 순서는 writer queue drain → gzip close → flush/fsync → 최종 파일명으로 atomic rename → file hash 계산 → manifest.final atomic write → COMPLETE atomic write다.

COMPLETE가 없는 경로는 파일 확정이 끝나지 않은 부분 artifact다. COMPLETE는 artifact의 원자적 확정을 뜻하며 completion_status의 실행 성공과 별개다. 정상적으로 기록을 닫은 INFRA_ERROR/CANCELLED에도 COMPLETE를 만들 수 있다.

verify-artifact는 completion_status=COMPLETED일 때만 NORMAL tick0…H−1과 final S_H, H회 성공 advance, H+1 snapshot을 요구한다. INFRA_ERROR/CANCELLED는 실제 decision prefix, last_successful_advance, last_valid_snapshot, 종료 phase를 검증한다. P8 뒤 P9 실패의 마지막 decision record는 성공 advance가 없을 수 있다. 이를 삭제하거나 성공으로 간주하지 않는다. final closure가 마지막 유효 snapshot을 다시 사용했다면 reused_snapshot=true와 원본 record 참조를 저장한다. 초기화 중 실패처럼 S0도 없으면 no_snapshot=true, end_step=null을 허용한다. 모든 경우 hash·sequence·command 참조·result의 실제 종료 사유는 일치해야 한다.

disk 여유가 2GiB 미만이면 새 canonical run을 시작하지 않는다. 예상 캠페인 크기는 첫 10 pilot artifact의 bytes/step으로 산정하고 예상 크기의 1.5배+10GiB를 확보한다. 이는 저장 용량 사전검사이며 무제한 디스크 점유를 허용하는 규칙이 아니다.

writer queue가 차면 wall time을 기다리되 physics를 건너뛰거나 sample을 버리지 않는다. 5초 안에 해소되지 않으면 오류로 종료한다. 기록이 느린 상황을 F3 관측 지연 고장으로 계산하지 않는다.

### 18.6 재실행의 세 종류

1. **오프라인 재판정:** 저장된 truth·I/O·command·event로 같은 oracle을 실행. 동일 규칙·버전에서는 요구사항별 판정·witness step이 정확히 같아야 한다.
2. **물리 재실행:** 고정 config·initial_state·환경으로 Isaac episode를 다시 수행. 결과·허용오차·분포를 비교하며 모든 float의 bitwise 일치를 요구하지 않는다.
3. **trace 표시:** 기존 자료를 그래프·표로 보는 작업. 이것을 시뮬레이션 재실행이라고 표시하지 않는다.

v1은 임의 중간 step의 PhysX 내부 상태 checkpoint 복원 기능을 제공하지 않는다. episode 처음부터의 재실행을 지원한다.

**필수 시험:** T-TRACE-01 정상 chain, 02 line 삭제, 03 값 변조, 04 순서 교체, 05 truncate, 06 disk/writer 오류, 07 atomic marker, 08 온라인·offline witness 일치, 09 재시도 namespace, 10 trace 파일만으로 보고서 재생성, 11 원자적으로 확정한 ERROR/CANCELLED와 불완전 파일 구별.

<a id="sec-19"></a>

## 19. M13 지표·통계·보고서

### 19.1 내부 계층

ArtifactReader → EligibilityClassifier → MetricReducer → BlockBootstrap → ComparisonBuilder → ReportRenderer 순서다. 집계는 원시 trace를 검증한 후 수행한다. result.json만 복사해 표를 만드는 경로는 빠른 미리보기로만 허용한다.

각 수치는 numerator, denominator, exclusions_with_reason, unit, source_artifact_ids, metric_revision을 가진다. denominator=0은 0%가 아니라 null/NA다.

### 19.2 필수 분모

| 기호 | 정의 |
|---|---|
| N_plan | 사전 계획한 run 수 |
| N_start | worker가 RUNNING에 진입한 첫 attempt 수 |
| N_eval | 필요한 근거를 가진 완결·평가 가능한 첫 attempt 수 |
| N_faultplan | fault를 지정한 계획 run 수 |
| N_trigger | 예정 trigger가 arming 안에서 실제 발생한 수 |
| N_activate | injector가 실제 활성화된 수 |
| N_realized | 고정 model/effect rule의 발현 근거를 충족한 수 |
| N_clear | E<H에서 fault 종료·정상 경로 복원을 관측한 수 |
| N_error | infrastructure/implementation error가 난 첫 attempt 수 |
| N_recovery_eligible | 사전 정의된 해당 recovery 종류의 대상 수 |

필수 비율:

- 시작률=N_start/N_plan, 평가 가능 비율=N_eval/N_plan.
- trigger 도달률=N_trigger/N_faultplan.
- 활성 성공률=N_activate/N_trigger.
- 활성 조건부 발현률=N_realized/N_activate와 계획 대비 발현률=N_realized/N_faultplan를 함께 표시.
- 유효 고장 시험 비율=발현되고 판정 가능한 run/N_faultplan.
- 인프라 오류율=N_error/N_plan.
- 정상/고장 task 결과 분포는 각 arm의 모든 계획 run을 분모로 SUCCESS/CONTROLLED_ABORT/FAILED/UNKNOWN/ERROR/NOT_STARTED를 표시.

발현하지 않은 고장 사례를 성공 사례로 넣어 robustness를 높이지 않는다. 반대로 발현한 고장을 제어기가 견뎌 성공했다면 정상적인 성공 결과다.

### 19.3 고장·검사·복구 측정

| 지표 | 정확한 계산 |
|---|---|
| 시작·종료 오차 | actual_step−scheduled_step. 정확성 fixture의 목표는 0 |
| 관측 지연 | delivered_step−source_step, 해당 sample ID 기준 |
| 명령 지연 | applied_step−issued_step, 같은 command ID 기준 |
| 복원 재개 지연 | RESTORE_RESUME의 실제 applied−issued. 요청 d로 덮어쓰지 않음 |
| oracle 검출 지연 | 최초 독립 위반 witness의 step부터 FAIL 발행 step까지 |
| 경로 복원 지연 | actual_end부터 정상 경로 재사용이 처음 관측된 step까지 |
| task 완료 시간 | 첫 유효 goal dwell 종료 step/120 |
| abort 반응 | 독립 deadline 또는 invalid 관측 가능 step부터 abort·hold·정지까지 각각 |
| reset 비용 | reset 요청부터 다음 S0 검증까지의 wall time |
| 다음 cycle 복구 | fault/reset chain의 다음 nominal episode가 정상 goal을 충족했는지 |

온라인 invariant/admission은 같은 P7에서 판정하므로 기대 검출 지연은 0 physics step이다. response는 합법적인 window가 끝나기 전 FAIL을 미리 발행하지 않는다. 이것을 긴 검출 지연으로 잘못 해석하지 않는다.

시뮬레이터 reset 전후 sim clock을 빼서 복구 시간을 만들지 않는다. 서로 다른 episode 사이 복구 지연은 wall clock과 각 episode의 sim 시간을 별도 기록한다. 1.0.0 기본 정책에는 in-episode 자율 재시도가 없으므로 그 성공률을 주장하지 않는다.

### 19.4 oracle 정답률과 mutant

독립 라벨 fixture는 PASS/FAIL/UNKNOWN의 3×3 confusion matrix로 보고하고 NOT_APPLICABLE 별도 fixture를 포함한 4×4 표도 제공한다.

- 위반 라벨을 FAIL로 검출: TP.
- 정상 라벨을 FAIL로 판정: FP.
- 위반 라벨을 PASS로 판정: FN.
- UNKNOWN은 abstention으로 따로 세며 FN에서 제외해 검출률을 높이지 않는다.
- 검출 yield=TP/모든 위반 라벨 수.
- false-alarm rate=FP/모든 정상 라벨 수.
- abstention rate=UNKNOWN 판정/전체 대상 수.
- exact-label accuracy=기대 판정과 같은 수/전체 fixture 수.

유한 fixture에서 100% 일치해도 모든 산업 환경에서 oracle가 100% 정확하다는 뜻은 아니다. 수작업 fixture에는 모집단 일반화를 위한 통계 CI를 붙이지 않는다.

mutant kill은 **같은 초기조건·동일 fault에서 해당 요구사항을 reference가 위반하지 않고 mutant가 위반하며, 독립 증거가 결함을 지지하는 경우**다. 분모는 서로 다른 mutant 6개이며 120 episode를 서로 다른 결함 120개로 세지 않는다. mutant별 usable pair, uninformative pair, infra error를 모두 표시한다.

### 19.5 실행 성능 측정

B0=B-TRACE는 같은 plant·motion·controller·sensor mapping·raw trace 양을 유지하고 fault/oracle 경로를 직접 연결한다.

benchmark_mode는 RunPlan에서 조립 경로만 선택한다. B0는 passthrough fault operator와 no-op online oracle을 쓰고 raw evidence는 동일하게 기록한다. B1/B2는 실제 전체 모듈을 사용한다. B0의 온라인 결과가 없으면 task 판정은 offline evaluation revision에서 생성하며 no-op을 PASS로 저장하지 않는다. 이 평가 시간은 RUNNING wall 비교에 넣지 않는다. 빠진 필드는 중립 값으로 채워 record schema·bytes가 비슷하도록 한다. B1은 CellIO 전체 기능, 활성 고장 없음이다. B2는 같은 전체 기능에 F4를 활성화한다. B-OFF는 선택적 진단이며 아래 180회 benchmark에 포함하지 않는다.

전체 고정 H=7200를 측정한다. task가 빨리 끝났다고 측정을 조기 종료하지 않는다. startup/warmup/finalization을 각각 기록하고 RUNNING 비교에서 분리한다.

paired runtime overhead = (T_B1−T_B0)/T_B0 ×100%다. 같은 cell·initial state·repeat끼리 짝을 맞춘다. B2는 고장으로 물리 trajectory가 달라질 수 있으므로 B2−B0 전체를 순수 framework overhead라고 해석하지 않는다.

critical-path CellIO 시간은 clock/event·mapping·fault·command bookkeeping·oracle·record 생성/enqueue의 main-thread exclusive wall time이다. physics solve, NVIDIA motion 계산, state backend readback, renderer, 비동기 writer thread CPU 시간은 이 지표에서 제외하고 별도 항목으로 기록한다. 이 제외 항목을 전체 wall overhead에서도 빼면 안 된다.

30Hz 구간 j의 CellIO 비용은 physics tick 4j..4j+3의 exclusive 시간 합이다. **p99≤3.33ms는 이 4개 tick 합의 목표**다. 120Hz의 각 tick에 3.33ms를 허용하는 기준이 아니다.

분위수는 정렬된 n개 값에 h=(n−1)p를 적용하고 floor(h)/ceil(h) 사이를 선형 보간한다. 셀·mode별 수치를 먼저 보고하고, 각 셀의 B1/B2에서 p99 목표를 판정한다. terminal HOLD 구간을 포함한 전체 값과 controller ACTIVE 구간의 값을 함께 내며 ACTIVE 구간도 목표를 충족해야 한다.

필수 성능 항목은 median/p95/p99/max control-interval cost, paired overhead, RTF=sim_seconds/running_wall_seconds, cold/warm startup, reset, peak RSS/VRAM, trace bytes/episode, writer 최대 backlog다. GPU 연산은 비동기 호출 시간을 실제 GPU 완료 시간으로 잘못 표시하지 않는다. 별도의 동기화 계측을 하면 프로파일을 구분한다.

### 19.6 통계 계약

초기 상태 seed가 통계 block이다. 같은 seed의 arm·repeat·controller 결과를 함께 보존한다. tick과 두 반복을 독립 표본으로 세지 않는다.

- 본평가: 셀별 20개 initial-state block.
- 같은 셀 안에서 block bootstrap 10,000회, seed=20261118.
- 대응 차이는 같은 block의 차이를 먼저 구한 후 resample.
- 셀별 결과를 우선 보고한다. 전체 값은 두 셀 동일 가중 평균을 추가한다.
- 95% interval은 bootstrap의 2.5/97.5 percentile이다.
- 일부 denominator가 0인 재표본은 해당 metric에서 NA로 남기고 비율을 함께 기록한다.
- 2개 셀의 결과로 임의 공장·로봇의 전체 모집단을 대표한다고 주장하지 않는다.

### 19.7 필수 보고서 화면·표

1. 전체 planned/started/evaluable/error 흐름 표.
2. cell×fault arm별 활성·발현·복원·task/compliance 분포.
3. 한 실패 episode의 raw/observed signal, command issue/apply, 실제 q/part pose, stage, fault window, oracle witness가 정렬된 timeline.
4. oracle confusion matrix와 UNKNOWN/NA 이유.
5. mutant별 paired 검출 표.
6. overhead·RTF·RSS·VRAM·writer backlog 분포.
7. reset-chain 누출 검사와 soak 추세.
8. 장면 B 확장 변경 파일·core 수정 여부·작업 시간 기록.

보고서에서 episode를 클릭하거나 식별자를 복사해 해당 trace와 재실행 명령을 찾을 수 있어야 한다. 정적 HTML/Markdown이면 충분하다. 웹 서버·로그인·클라우드 dashboard는 필수 기능이 아니다.

---

<a id="sec-20"></a>

## 20. M14 CLI·doctor·사용자 오류

### 20.0 내부 계층

ArgParser는 문자열 인수를 typed request로만 변환한다. CommandUseCase는 run/plan/evaluate 등 업무 단위를 호출하고, ServiceFactory가 profile에 맞는 adapter를 조립한다. ErrorMapper가 domain error를 고정 종료 코드로 변환하며, TextPresenter/JsonPresenter는 같은 result 객체를 출력한다. CLI parser나 presenter 안에 physics·fault·oracle 판단을 넣지 않는다.

### 20.1 명령 표

Windows 전용 Conda 환경의 python -m cellio로 실행한다. validate/evaluate/report 등 오프라인 명령은 WSL2의 Python 3.12 환경에서도 실행한다.

| 명령 | 필수 입력 | 출력·효과 |
|---|---|---|
| doctor | --profile isaac62, --output FILE | 환경·API·VRAM·의존성 검사 JSON, 자산 무단 다운로드 없음 |
| assets resolve | --profile franka_panda | 허용된 공식 catalog 자산의 로컬 cache와 lock |
| assets verify | --offline | dependency closure와 hash 검사 |
| validate | --config FILE | schema·ID·단위·시점·조건 검사 |
| run | --config FILE, --headless 또는 --gui | 한 run의 새 attempt |
| campaign plan | --suite NAME, --output DIR | 전체 resolved config·initial state·순서와 plan hash |
| campaign run | --plan FILE | 미실행 run 순차 수행 |
| campaign resume | --plan FILE, 선택 --retry-failed | 기존 기록을 보존하며 계속 수행 |
| verify-artifact | --artifact DIR | hash·순서·완결·참조 무결성 검사 |
| evaluate | --artifact DIR, --requirements FILE | 새 evaluation/ 디렉터리, 원본 수정 없음 |
| report | --campaign DIR, --output DIR | metrics.json, tables/, figures/, report.html, report.md |
| compare | --reference DIR, --candidate DIR | 같은 protocol끼리 paired diff |
| benchmark | --suite overhead-v1, --output DIR | 180회 계획 및 실행 기록 |
| acceptance | --level r0/r1/r2/r3, --report FILE | gate별 PASS/FAIL/NOT_RUN과 증거 링크 |
| schema export | --output DIR | config/trace/result JSON Schema |

suite 이름은 canonical-v1, oracle-v1, reset-v1, overhead-v1, soak-v1, reuse-v1로 고정한다. 지원하지 않는 이름은 도움말과 CFG_SUITE_UNKNOWN을 출력한다. 명령 도움말에 기본 horizon·seed·output path·종료 코드를 포함한다.

### 20.2 종료 코드

| 코드 | 의미 |
|---:|---|
| 0 | 명령이 정상 완료. run의 task 실패/제어된 중단과 별개 |
| 2 | 설정·사용법 오류 |
| 3 | 환경·asset preflight 실패 |
| 4 | 실행·writer·backend·oracle 인프라 오류 |
| 5 | 기대 결과 불일치 또는 acceptance MUST 실패 |
| 6 | strict acceptance에서 필요한 증거가 UNKNOWN/NOT_RUN |
| 130 | 사용자 취소 |

run의 --assert-expectations는 expected가 있는 경우만 case_verdict를 종료 코드에 반영한다. 기본 run은 task FAIL이어도 artifact가 정상 완결되면 0이다. 이 정책을 CI에서 설명 없이 뒤집지 않는다.

### 20.3 doctor의 검사 항목

doctor는 세 수준을 반환한다.

- static: OS/architecture, Python/Isaac version, 추가 package, 파일 권한·디스크.
- import: SimulationApp 후 필요한 6.2 API export, 명세된 signature·extension.
- smoke: 작은 primitive 120 steps, time advance, actual state readback, target apply, 종료.

GPU 모델·VRAM·driver·CPU·RAM은 실제 읽은 값과 검사 시각을 기록한다. 지원 표에 없다고 곧바로 성공/실패를 단정하지 않고 공식 compatibility 결과와 smoke 결과를 모두 보인다. Isaac/asset 라이선스 동의·driver 업데이트를 doctor가 대신 수행하지 않는다.

### 20.4 오류 메시지 형식

stderr는 사람이 읽는 한 줄 요약과 artifact 위치, stdout의 --json은 구조화 JSON이다.

~~~json
{
  "code": "CFG_SIGNAL_UNKNOWN",
  "severity": "ERROR",
  "module": "config",
  "path": "faults[0].target_id",
  "message": "signal 'clamp.closed' is not registered for cell_a",
  "suggestion": "Choose a signal from the resolved cell_a registry.",
  "run_id": null
}
~~~

traceback은 디버그 파일에 보관한다. 일반 사용자에게 전체 내부 stack만 보여주고 해결할 입력을 숨기지 않는다.

**필수 시험:** T-CLI-01 도움말, 02 JSON 오류, 03 종료 코드, 04 잘못된 profile, 05 doctor import 순서, 06 resume, 07 offline commands가 Isaac 없이 import, 08 임의 import/eval 차단.

---

<a id="sec-21"></a>

## 21. M15 확장 포트와 M16 선택적 OpenPLC

### 21.0 registry 내부 계층

PluginCatalog는 설치된 allowlist의 metadata를 찾고 DescriptorValidator가 ID·버전·포트·resource 선언을 검사한다. FactoryResolver가 지연 import로 객체를 만들며 ContractHarness가 mock/Isaac 공통 계약을 실행한다. ProvenanceRecorder는 실제 선택한 plugin·code·asset hash를 manifest에 넣는다. factory 선택과 실제 scene generation을 registry 자체의 import side effect로 수행하지 않는다.

### 21.1 새 장면 추가 계약

ScenePlugin의 필수 출력은 다음과 같다.

| 항목 | 정확한 책임 |
|---|---|
| scene_id, revision | 유일 ID와 semantic version |
| build_config | 자체 geometry·asset reference·material·joint 설명 |
| entity_registry | body/joint/actuator/signal/zone/waypoint ID와 타입 |
| create_plant(profile) | PlantPort 구현 생성 |
| create_mapper() | SignalMapperPort 구현 생성 |
| controller_presets | 공개 ControllerPort factory 목록 |
| requirement_presets | declarative oracle preset |
| scenario_presets | 정상·각 fault 설정 |
| initial_state_sampler | seed→실제 initial state, 분포·bounds |
| evidence_manifest | 자산 이용조건·source·유닛·지원 fault |

plugin 등록은 pyproject entry point group cellio.scene_plugins를 사용하되 설치된 allowlist의 이름만 선택한다. YAML에 module.path:function을 넣어 임의 import하는 경로는 허용하지 않는다. plugin import가 SimulationApp을 자동 생성해서도 안 된다.

새 장면은 core/faults·core/clock·core/oracle engine를 수정하지 않고 추가해야 한다. 새 원자 predicate가 필요하면 별도 registered predicate를 추가하고 단위 fixture를 제공한다. 특정 scene_id에 대한 if문을 core에 추가하면 C3 재사용 게이트를 실패 처리한다.

### 21.2 장면 B 재사용 증거

장면 A의 core를 태그한 뒤 B 추가 commit의 diff를 보존한다. 변경을 geometry/config, mapper/adapter, FSM, requirements, shared-core bugfix로 분류한다. shared-core bugfix가 있었다면 숨기지 않고 A의 회귀시험을 다시 실행한다. 코드 줄 수를 억지로 줄이는 것이 목표가 아니라 책임 경계와 재사용 가능한 core를 입증하는 것이 목표다.

실제 개발 시간은 작업일지에서 기록한다. 이 명세의 예상 일정과 실측 개발 시간을 혼동하지 않는다.

### 21.3 선택적 OpenPLC 모듈의 범위

M16은 R4다. 기본 R3의 통과 조건이 아니다. 구현할 경우 Python FSM을 외부 PLC controller로 바꾸는 gateway로 만든다. 실제 PLC 장치·네트워크 설비에는 연결하지 않고 localhost의 별도 Modbus TCP 포트 15020을 사용한다.

외부 scan은 wall-clock이므로 execution_profile=plc_wallclock로 표시한다. 기본 lockstep 결과와 같은 표에 합치지 않는다. PLC scan 목표 20ms·sim target RTF=1은 설정값이며 hard real-time 보장이 아니다.

### 21.4 OpenPLC 내부 계층

| 계층 | 책임 |
|---|---|
| ProcessImageEncoder | 공개 SignalSample을 bit/quality/age register로 변환 |
| ModbusServer | localhost에서 완전한 frame의 원자적 read/write |
| CommandDecoder | frame seq·generation·action ID·범위 검증 |
| ActionRegistry | action ID를 미리 등록한 waypoint/gripper/clamp 명령으로 변환 |
| GatewayController | ControllerPort로 최신 완결 command frame 제공 |
| ClockAudit | wall 수신 시각·적용 sim step·PLC scan seq 기록 |

OpenPLC가 world pose·fault label·oracle를 읽을 수 없게 공개 process image만 제공한다. arbitrary pose는 v1 gateway에서 받지 않는다. action ID는 scene registry의 정해진 waypoint/actuator 명령으로만 변환한다.

### 21.5 고정 register layout

주소는 **0-based PDU 주소**, 모든 register는 uint16, 32-bit 값은 high word→low word, signed 값은 2의 보수다.

Input registers 0–23, FC04:

| offset | 의미 |
|---|---|
| 0 | frame_version_start, 짝수=완결 |
| 1–2 | episode generation uint32 |
| 3–4 | sensor frame seq uint32 |
| 5 | usable mask, bit i=GOOD이고 age≤12 |
| 6 | Boolean value mask |
| 7–14 | 8개 signal의 age_steps, unknown=65535 |
| 15–16 | sim_step uint32 |
| 17–22 | reserved=0 |
| 23 | frame_version_end, start와 같아야 함 |

고정 signal bit: 0 robot.at_waypoint, 1 gripper.open, 2 gripper.closed, 3 destination.present, 4 A pickup.present/B fixture.present, 5 A belt.stopped/B clamp.open, 6 A reserved/B clamp.closed, 7 A reserved/B robot.clear_fixture. 없음은 usable=0,value=0,age=65535다.

Holding registers 100–115, FC16로 **16개를 한 번에** 쓰기:

| offset(100 기준) | 의미 |
|---|---|
| 0 | frame_version_start, 짝수 |
| 1–2 | generation uint32 |
| 3–4 | command seq uint32 |
| 5 | action_id uint16 |
| 6 | mode: 0 NOOP, 1 EXECUTE, 2 HOLD, 3 CANCEL |
| 7–8 | job_id uint32 |
| 9–14 | reserved=0 |
| 15 | frame_version_end |

부분 write·잘못된 길이·홀수 version·start/end 불일치는 적용하지 않고 gateway error counter를 증가시킨다. server는 P3 종료 시 immutable sensor frame을 바꾸고, FC04 요청은 한 snapshot에서 읽는다. command는 frame 전체를 검증한 뒤 다음 P4에 한 번 적용한다. generation 불일치와 duplicate seq는 거부/중복 기록한다.

500ms 동안 새 완결 PLC command heartbeat가 없으면 gateway connection 상태를 LOST로 만들고 별도 정책에 따라 cancel/hold를 발행한다. 이 watchdog는 F1–F6의 고장 모델과 별개이며 이벤트·동작을 trace에 남긴다. 정확한 step 지연을 가정하는 주장은 lockstep 프로파일에만 적용한다.

<a id="sec-22"></a>

## 22. 시험 목록과 동결할 캠페인

### 22.1 시험 단계

| 단계 | 환경 | 검증 대상 | 다음 단계 조건 |
|---|---|---|---|
| U | 순수 Python/mock | schema·clock·fault·queue·oracle 의미 | 모든 필수 fixture의 기대 결과 일치 |
| I | Isaac 최소 primitive | API·actual state·drive·step·material·reset | T-ISAAC·T-CMD adapter 시험 통과 |
| C | 참조 장면 commissioning | 정상 파지·클램프·waypoint·geometry·signal | 장면별 고정 정상20회, pilot·경계 확인 |
| P | 작은 pilot | 발현·판정 근거·측정 비용·artifact | main protocol 잠금 가능 |
| E | 동결 main campaign | 전체 task/compliance/fault 결과 | 분모·무결성·MUST gate 충족 |
| R | 새 환경 재현/soak | 설치·장기 실행·초기화 | R3 수락 보고서 생성 |

### 22.2 62개 고장 계약 시험 ID

각 계열의 01–08은 다음 공통 사건을 해당 연산자에 맞게 검사한다. 이는 62개의 명명된 시험 계약이며 parameterization으로 실제 pytest case 수가 더 많아질 수 있다.

| suffix | 시험 | 기대 |
|---|---|---|
| 01 | A=0 및 정상 A에서 시작 경계 | A 이전 무효과, A부터 정확히 적용 |
| 02 | E−1/E 종료 경계 | E−1 적용, E 정상 경로 |
| 03 | duration=1 | 1 tick만 적용, 미발현 가능성 정확히 표기 |
| 04 | 비대상 signal/actuator 보존 | 같은 overlay 입력의 non-target 변조 0 |
| 05 | 잘못된 target/type/parameter | 실행 전 CFG 오류 |
| 06 | 활성 중 reset | 이전 generation 효과·packet 적용 0 |
| 07 | trigger 미도달 | UNREACHED, NOT_EXERCISED |
| 08 | E=H | active 구간 종료, restoration UNOBSERVED |

추가 계약:

| ID | 시험 |
|---|---|
| T-F2-09 | invalid nominal을 freeze해 GOOD으로 바꾸지 않음 |
| T-F2-10 | value 같아도 age 차이 발현, cached transport identity 보존 |
| T-F3-09 | 최초 due 전 cached sample, A0이면 MISSING |
| T-F3-10 | delay>duration이면 flush와 미전달을 보존 |
| T-F3-11 | due==E인 old packet도 폐기 |
| T-F3-12 | queue overflow·stale channel generation 거부 |
| T-F4-09 | 실제 due에서 전달, 이전 effective goal 유지 |
| T-F4-10 | 이미 전달한 command를 소급 지연하지 않음 |
| T-F4-11 | E의 최신 valid desired 한 번 재개 |
| T-F4-12 | due tick의 cancel 우선 |
| T-F4-13 | E tick의 cancel, old intent 복원 없음 |
| T-F4-14 | queue overflow·중복 command 거부 |
| T-F6-09 | contact deflection으로 physical hold 실패를 active flag와 구별 |
| T-F6-10 | gripper group coordinate/단위/31-sample hold 및 mimic |

F1의 공통 fixture는 stuck-false와 stuck-true를 모두 parameterize한다. F3/F4는 delay=1,24,120을 추가 parameterize한다. 모든 계열에서 observed/raw aliasing 방지와 원본 불변을 검사한다.

### 22.3 canonical arm 설정

모든 arm은 H7200, warmup240, arm window=[0,4800), offset0, one-shot이다. trigger는 controller.stage_enter이며 지정 stage_id를 사용한다. 정상 arm은 fault가 없다.

| arm | Cell A target / trigger | Cell B target / trigger | parameter / duration |
|---|---|---|---|
| healthy | 없음 | 없음 | faults=[] |
| f1_low | belt.stopped / wait_stop | robot.clear_fixture / fixture_retreat | value=false, A480 / B1200 |
| f1_high | belt.stopped / wait_stop | robot.clear_fixture / fixture_retreat | value=true, A480 / B1200 |
| f2 | gripper.closed / grasp | clamp.closed / clamp_close | freeze, 480 |
| f3 | gripper.closed / grasp | clamp.closed / clamp_close | delay24, 480 |
| f4 | actuator.gripper / grasp | actuator.clamp / clamp_close | delay24, 120 |
| f5 | gripper.closed / grasp | clamp.closed / clamp_close | dropout, 480 |
| f6 | actuator.gripper / grasp | actuator.clamp / clamp_close | hold, A480 / B600 |

B의 freeze/delay/dropout는 clamp_close 진입 전 아직 open인 신호에서 시작하며 close 명령은 다음 control tick에 발행한다. stage가 4800 이후에만 도달하면 trigger 미도달로 남긴다. 이를 유리한 step으로 사후 이동하지 않는다.

M1↔f5, M2↔f6, M3↔f1_high, M4↔f4, M5↔f2, M6↔f3의 pairing을 사용한다.

### 22.4 본평가 820 attempts

| 구성 | 계산 | 수 |
|---|---|---:|
| reference 기본 | 2 cells × 8 arms × 20 initial states × 2 repeats | 640 |
| 표적 mutant | 2 cells × 6 mutants × 5 initial states × 2 repeats | 120 |
| mutant nominal 진단 | 2 cells × 6 mutants × 5 initial states × 1 repeat | 60 |
| **합계** | 첫 attempt 기준 | **820** |

reference의 initial state ID는 seed1000–1019에서 생성·동결한다. mutant는1000–1004의 동일 상태를 사용한다. paired reference는640회에 이미 들어 있으므로 또 추가해 수를 부풀리지 않는다.

scene seed가 다르더라도 실제 initial-state vector가 같으면 독립 상태로 세지 않는다. sampled 값을 plan에 저장하고 중복을 사전 검사한다. controller/fault seed는 기본0이다.

### 22.5 별도 검사기 fixture

6개 predicate family는 순서, 취소/명령충돌, 입력 신선도, 응답기한, 물리 완료, 복원/초기화다. 각 셀×family마다 clear PASS4, clear FAIL4, UNKNOWN2의10개를 둔다.

**2×6×10=120개**, 라벨 수는 PASS48/FAIL48/UNKNOWN24다. 추가로 antecedent 미발생의 NOT_APPLICABLE을 셀×family×2=24개 둔다. 이는 simulation820회에 포함하지 않는다. 27장에 각 fixture의 생성 조건을 정의한다.

gold label은 손으로 검산 가능한 event/pose sequence에서 작성하고 production predicate를 호출해 생성하지 않는다. 기대 라벨·근거는 oracle 구현 전 commit하거나 구현과 독립적으로 검토해 잠근다.

### 22.6 reset chain 48개

2 cells×6 families×4 orderings=48 chains다. 각 chain은3 episodes로 고정한다.

1. **control:** fresh_process에서 동결 initial_state의 healthy 실행.
2. **predecessor:** 새 검증 worker에서 아래 ordering의 실행.
3. **post-reset:** 같은 검증 worker에서 hard_reload한 healthy 실행.

ordering은 (a) predecessor healthy, (b) fault active 중 reset 요청, (c) 발현·종료·정상 경로 복원 뒤 reset, (d) outstanding state를 가진 중간 구간에서 reset이다. (d)는 F2 capture cache, F3/F4 pending queue, F6 hold cache, F1/F5 active observation state를 사용한다.

(b)/(d)는 reset 경계 검증을 위한 명시적인 test cancellation이다. 본평가의 임의 조기 종료와 섞지 않는다. chain당3 episodes, 총144 episode 기록이지만 **독립 분석 단위는48 chains**다. control을 chain마다 별도 실행하여 같은 결과 파일을 중복 표본으로 재사용하지 않는다.

post-reset은 control의 S0 허용오차, software state exact clear, goal/요구사항 결과를 비교한다. 이전 episode의 FAIL 기록은 이전 artifact에 남으며 새 oracle state만 초기화한다.

reset suite의 F1 대표 arm은f1_high다. ordering(a)는predecessor healthy를H까지 실행, (b)는fault 시작A+8의P0, (c)는종료E+12의P0, (d)는A+4의P0에서reset 취소한다. 최초 trigger/발현이 필요한 전제에 못 도달하면 chain을 SKIPPED_PRECONDITION으로 남기며 합격 chain으로 세지 않는다. chain_id와slot까지 run identity에 넣어 같은 초기조건의 control 실행을 덮어쓰지 않는다.

### 22.7 overhead·soak·재사용

| suite | 구성 | 평가 단위 |
|---|---|---|
| overhead-v1 | 2 cells×3 modes(B0/B1/B2)×10 states(1000–1009)×3 repeats | 180 runs, paired state block |
| soak-v1 | hard_reload로 A/B 및8arms 순환, 첫10개 cache 안정화 후 측정 | **200 episodes 이상 AND2 wall hours 이상** |
| reuse-v1 | 새 장면 variant1개×8arms×5states(2000–2004) | 선택적40 runs |
| clean-install | 깨끗한 CellIO 의존성 환경에서 각 셀 healthy5회 | 10 quickstarts |

soak는 두 최소 조건을 모두 만족할 때 종료한다. 첫10개 warmup episode는200개에 포함하지 않는다.

유한 suite의 plan_mode는finite이며 전체 run 목록을 먼저 동결한다. soak만 plan_mode=soak_generator로 사전 고정한 생성식과 종료 규칙을 사용한다. warmup은A/B healthy를5회씩 번갈아 실행한다. 이후0-based n에 대해 cell=n mod2, arm=floor(n/2) mod8, initial_state=1000+(floor(n/16) mod20), repeat=floor(n/320)으로 선택한다. 각 run 행을 시작 전에 append·hash하고 generator와 stop rule의 원래 hash를 보존한다. 결과에 따라 arm/seed를 바꾸지 않는다. stop rule은 warmup 제외 completed≥200 AND elapsed≥7200s, 보호 한도86400s이며 인프라 오류가 나면 해당 soak를 실패로 종결한다. N_plan은 실제 생성해 실행 예정으로 기록한 행 수이고, 종료 후 가상의 미실행 행을 분모에 추가하지 않는다. 실행 결과와 무관하게 정한 이 종료 규칙을 보고서에 표시한다. 24시간 보호 한도에서 조건 미달이면 incomplete다. 반복 순환 때문에 같은 state를 많이 실행한 수를 신규 일반화 표본으로 주장하지 않는다.

reuse variant는 core 수정 없이 conveyor속도+10% 또는 fixture 위치X+10mm 중 **하나를 사전에 선택해** 해당 geometry/waypoint/requirement를 일관되게 수정한다. 둘 중 결과가 좋은 것만 사후 채택하지 않는다. 기본 R3에는 두 canonical cell이 필수이며 reuse-v1은 강화 자료다.

워크스테이션 구현은 첫 reuse 실행 전에 Cell A의 conveyor 목표 속도를 0.060 m/s에서 0.066 m/s로 높이는 조건을 선택했다. 같은 픽업 위치에 부품이 먼저 도착하므로 기존 컨트롤러의 위치 감지와 정지 확인을 그대로 시험한다. 변형 이름은 `conveyor_speed_plus_10pct`이며, 8개 arm과 초기 상태 2000–2004의 40회를 `protocols/reuse-v1.yaml`에 기록한다.

### 22.8 요구사항 추적표

| 요구사항 | 시험·증거 |
|---|---|
| FR01,23 | T-CFG01–08, T-CLI08, doctor/asset report |
| FR02,04 | T-SIG01–07, immutable snapshot test |
| FR03,13 | T-CLK01–06, GUI/headless trace |
| FR05,07 | T-F1–F6 전체, reset chain |
| FR06 | T-CMD01–08, command timeline |
| FR08,09 | T-ORA01–11,120+24 oracle fixture |
| FR10 | T-GOLD10, category/outcome fixture |
| FR11 | T-RESET01–12,48 chains |
| FR12 | T-RUN07–08, plan/attempt ledger |
| FR14,15 | mock/Isaac 공통 port, B 추가 diff |
| FR16 | T-CTRL01–08, mutant pair report |
| FR17–20 | T-TRACE01–11, hash/manifest/re-evaluation |
| FR21,22 | clean-install10회, CLI exit/error tests |
| FR24 | acceptance.json의 모든 gate→test/evidence 참조 |

시험 ID 표기에서 하이픈 생략은 같은 ID를 의미한다. 구현 파일명은 tests/unit/test_fault_f3.py처럼 module별로 두고 test 함수 이름/metadata에 규범 ID를 붙인다.

---

<a id="sec-23"></a>

## 23. R3 완료·성능 수락 기준

### 23.1 MUST: 미충족이면 R3로 표시하지 않는 항목

| gate | 합격 기준 | 증거 |
|---|---|---|
| G01 설정 | 모든 invalid fixture 거부, valid preset100%해석, 임의 코드 실행 경로 없음 | schema suite |
| G02 시간·고장 |62개 명명 계약과 parameterized boundary의 기대 결과100%일치, start/end0-step오차 | unit/conformance |
| G03 독립 검사 | oracle120+NA24의 exact label100%일치, 온라인/오프라인 판정·witness 일치 | confusion/witness |
| G04 실제 발현 | 각 cell×6family에서 독립 발현과 E<H 복원을 각각 적어도1회 관측 | 12개 cell-family evidence |
| G05 F6 물리 근거 | 두 셀 각각 conflicting travel 아래31-sample physical hold PASS 사례 존재 | actual q·target·receipt |
| G06 정상 기능 | 고정 정상 장면별20회 연속·8개경계사례·clean-install 각5/5에서 task SUCCESS 및 compliance PASS | commissioning/quickstart |
| G07 무결성 | 모든 계획·시도·종료 사유 보존, silent drop0, checksum/marker 오류0 | ledger/artifact audit |
| G08 초기화 |48chains에서 software 잔여 상태0, 물리 reset tolerance 충족, post-reset 목표 성공 | reset report |
| G09 오류 처리 | boot/stall/writer/NaN/취소를 정확한 오류·부분 결과로 종결 | negative integration suite |
| G10 동결 본평가 | 820 first attempts 모두 RUNNING 진입→fullH COMPLETED·COMPLETE artifact, 인프라오류0·unhandled crash0·hang0 | campaign report |
| G11 장시간 실행 | warmup 제외≥200episodes AND≥2h, unhandled crash/hang/silent loss0 | soak report |
| G12 재사용 | B 추가가 core의 scene-specific 분기 없이 구현, 공통 계약 회귀 통과 | diff/extension report |
| G13 배포 | version tag·LICENSE·설치·2quickstart·asset route·raw결과·재생성 명령 공개 가능 | release checklist |
| G14 재현 | 깨끗한 설치 환경에서 다른 작업 디렉터리로 두 셀 재실행, 실제 명령·환경·결과 기록 | reproducibility log |
| G15 성능 측정 | overhead180회 완결, 실행성능·발현·메모리 등 SHOULD 지표를 실측표로 작성. 임계값 자체는 SHOULD | benchmark/metrics report |

G10은 정상적인 controller task 실패·CONTROLLED_ABORT·검출된 mutant 위반을 소프트웨어 crash로 세는 기준이 아니다. 인프라 오류가 발견되면 원인을 수정하고 새 candidate/version에서 캠페인을 수행한다. 이전 실패 artifact와 수정 기록은 보존한다.

G03의100%는 **명시한 유한 fixture 집합의 계약 준수**다. 무한한 모든 오류에 대한 검출 보증이 아니다. G04의 적어도1회는 최소 기능 coverage이며 전체 발현률은 아래 목표로 별도 평가한다.

G10의 논리적 UNKNOWN/NOT_APPLICABLE은 올바르게 기록한 유한 시간 결과로 남길 수 있다. 완전한 artifact와 task/compliance의 확정 판정은 다른 개념이다. 미실행·부분 기록·인프라 실패를 COMPLETED로 세지 않는다. G15는 측정 수행과 보고를 요구하며, 제3자 재실행·reuse 같은 선택 활동은 NOT_PERFORMED와 사유를 기록할 수 있다. G07의 손상0은 정상 완결로 채택하는 artifact에 적용하고, 의도적으로 손상시킨 부정 fixture는 정확한 거부가 합격이다.

### 23.2 SHOULD: 권장 성능 목표

| 지표 | 목표 | 해석·미달 시 조치 |
|---|---:|---|
| 정상 분포 task 성공 | 셀별≥95% |40reference nominal runs/cell. 실패 원인·initial-state 구간을 공개하고 정상 셀 보정 우선 |
| 조건부 fault 발현 | 셀별·family별≥98% | N_realized/N_activate. 계획 대비 비율·미도달도 함께 보고 |
| CellIO critical-path cost |30Hz구간 p99≤3.33ms |4physics tick 합, 범위는19.5절 |
| paired runtime overhead |median≤10%, p95≤20% |B1/B0 대응값, cold startup제외 |
| headless RTF |median≥0.5 |기준 장비에서60sim-s를RUNNING120wall-s 이내. 하드 실시간 요구 아님 |
| cached startup |p95≤120wall-s |최초 shader/asset 준비 별도 |
| hard_reload reset |p95≤15wall-s |physics view·warmup 포함, 실제 측정 |
| oracle online latency |invariant/admission0step |기한형 규칙은 window close 기준 |
| 메모리 |cache plateau 후 지속 상승 없음 |아래 회귀/구간 차이 검사 |
| mutant 분별 |6종 중≥5종을 실제 장면 paired evidence로 구별 |각 mutant fixture는 필수, uninformative를 숨기지 않음 |
| 외부 연구자 재실행 |제3자1명 이상이 두 quickstart 실행 |실행 장비·명령·문제/수정 기록 |

메모리 목표는 warmup10개 후 episode 종료 RSS/VRAM을 수집하고, 처음20개와 마지막20개의 median 차이 및 Theil–Sen slope를 보고한다. 우선 경보 기준은 RSS 증가>256MiB 또는>5% 중 큰 값, VRAM 증가>256MiB다. 경보가 나면 cache 성숙과 실제 누수를 구분해 원인을 설명한다. allocator가 예약 메모리를 유지한다는 이유만으로 곧바로 누수라고 판정하지 않는다.

목표를 못 맞추면 실제 수치·병목·사용상 영향을 문서에 남긴다. 특히 정상 작업이 자주 실패하거나 발현이 충분하지 않으면 속도 개선보다 장면·계약 수정을 먼저 한다. SoftwareX의 “안정권”을 특정 채택 확률로 환산하지 않는다.

### 23.3 성능 목표를 바꿀 수 있는 시점

commissioning/pilot의 측정 결과를 근거로 **본평가 전에만** 목표·장면·timing·tolerance를 수정할 수 있다. 수정 이유, 이전/새 값, 영향을 받는 claim과 시험을 ADR에 남긴다. 본평가 결과를 본 뒤 threshold를 낮추면 같은 확증 평가를 통과한 것으로 간주하지 않고 새 protocol revision으로 평가한다.

---

<a id="sec-24"></a>

## 24. 개발 작업 분해·일정·중단 판단

### 24.1 권장 구현 순서

기간은 숙련 Python/로보틱스 개발자1명과 검토자1명이 일부 참여하는 경우의 **계획 추정**이다. 실측 개발 기간이 아니다.

| 작업 | 범위 | 예상 | 완료 산출물 |
|---|---|---:|---|
| W01 계약·repo | schema·dataclass·registry·mock plant |3–5일 | R0 import/validation |
| W02 시간·고장 | phase·event·F1–F6·queue·cancellation |5–8일 |62개 fault 계약 통과 |
| W03 oracle·trace | predicate·시간 규칙·144fixture·hash·오프라인 재판정 |6–9일 |golden oracle/trace |
| W04 Isaac 기반 | bootstrap·material·readback·DOF·TCP·motion |5–8일 |adapter integration |
| W05 장면 A | conveyor·friction grasp·FSM·센서·정상/고장 |5–8일 |R1, 정상20연속 |
| W06 장면 B |load/clamp/process/unload·독립 검사|5–8일 |R2, 재사용 diff |
| W07 runner/CLI |campaign·watchdog·reset·report·doctor|4–6일|오류/재개 통과|
| W08 pilot·동결 |경계·발현·timing·storage·baseline|3–5일|protocol.lock|
| W09 평가·배포 |820+별도suite, 분석·문서·깨끗한설치|5–8일+계산대기|R3candidate|
| W10 논문화 |기여/비교/그림/limitations·release검토|4–6일|SoftwareX제출초안|

합계45–71실작업일 정도를 초기 예산으로 잡는다. 팀의 기존 Isaac 코드·버전 전환 경험에 따라 달라진다.2인 병렬화는 core/oracle과 Isaac scene을 분리하되 같은 interface fixture를 먼저 동결한다.

### 24.2 먼저 확인할 위험

| 위험 | 첫 확인 시점 | 처리 |
|---|---|---|
|6.2API·자산 불일치|W04첫날|doctor/import readback; legacy혼합금지|
|friction grasp 불안정|A하위동작제작|TCP·contact·drive·waypoint를 순서대로 수정|
|fixture load 간섭|B제작첫날|load/unload 위치분리와 극값사례|
|60s내 정상B미완료|commissioning|명세된마지막하강구간·경로/속도점검, 필요하면새protocol의H로전체arm동일수정|
|fault는active이나효과없음|pilot|trigger/target의독립근거점검,본평가전수정|
|mutant둘다실패|pilot|reference요구사항과결함위치점검,unscorable보존|
|trace가병목|pilot|중복객체복사·직렬화/압축cost분리,유실로해결금지|
|같은process잔여상태|reset/soak|stage/view완전폐기,필수resetstate확인|
|공개자산배포불가|assets resolve|공식다운로드절차·hash로재현,허가없는재배포금지|

아직 획득하지 않은 실행 결과 때문에 설계 문서에 가짜 성능값을 채우지 않는다. API signature·TCP calibration·asset hash는 개발 단계에서 측정해 생성할 **실행 산출물**이지, 문서가 빠뜨린 임의 설계 선택이 아니다.

### 24.3 설계 변경 관리

ADR 파일은 id, date, problem, alternatives, decision, affected_contracts, tests, old/new protocol hash를 가진다. 다음 변경은 protocol major/minor revision과 관련 평가 재실행이 필요하다.

- clock/phase/고장 종료 의미/queue 복원/판정 의미: major.
- 새로운 scene·fault model·controller policy·oracle predicate: minor, 기존 suite회귀.
- 설명·오타 수정으로 실행 의미 불변: patch.
- gains/TCP/waypoint/초기분포/timeout/tolerance 변경: scene 또는 protocol revision 증가, 해당 comparison 재실행.

원시 artifact를 새 의미로 다시 판정하면 evaluation revision을 추가하고 이전 판정도 보존한다.

---

<a id="sec-25"></a>

## 25. 저장소·개발 산출물·SoftwareX 원고 연결

### 25.1 저장소 기본 구조

~~~text
cellio/
  pyproject.toml
  LICENSE.txt
  README.md
  CITATION.cff
  CHANGELOG.md
  THIRD_PARTY_NOTICES.md
  src/cellio/
    __main__.py
    config/
    core/
      models.py
      ports.py
      clock.py
      events.py
      signals.py
      commands.py
      faults/
      oracle/
    adapters/
      mock/
      isaac62/
    scenes/
      cell_a/
      cell_b/
    controllers/
      reference/
      mutants/
    runtime/
      episode.py
      campaign.py
      worker.py
    artifacts/
    analysis/
    reporting/
    cli/
    plugins/
  schemas/
    config.schema.json
    trace.schema.json
    result.schema.json
  examples/
    cell_a/
    cell_b/
  protocols/
    canonical-v1.yaml
    oracle-v1.yaml
    reset-v1.yaml
    overhead-v1.yaml
    soak-v1.yaml
  tests/
    unit/
    fixtures/
    integration/
    acceptance/
  scripts/
    reproduce_tables.py
    reproduce_figures.py
  docs/
    installation.md
    quickstart_cell_a.md
    quickstart_cell_b.md
    fault_semantics.md
    add_a_cell.md
    limitations.md
    adr/
  assets/
    manifests/
    generated/
  results/
    release_manifest.json
~~~

core는 adapters/scenes/controller의 구체 구현을 import하지 않는다. CLI와 composition root가 구체 factory를 조립한다. dependency cycle은 import 검사에서 실패 처리한다.

### 25.2 릴리스에 포함할 것

- wheel/sdist 또는 source tag, commit hash, Python/package lock.
- 자체 장면 생성 코드와 공개 가능한 소형 asset.
- vendor asset의 정확한 취득 경로·variant·checksum·이용조건.
- 정상/각fault quickstart 설정과 예상 파일 구조.
- fixture gold label·mutant patch·protocol 계획.
- 원시 artifact 또는 공개 저장소의 고정 DOI/버전 링크와 checksum.
- 표·그림 재생성 스크립트, software metadata table에 넣을 정보.
- known limitations와 지원하지 않는 profile 목록.

SoftwareX는 소프트웨어 설명과 공개 코드·재사용 가능성을 중시하는 저널이다. 현재 guide/template의 원고 구성·코드 공개·metadata 요구를 최종 제출 시 재확인한다. 이 명세의 시험 횟수나 성능 목표는 저널이 정한 문턱이 아니다. [S19] 로봇·평가 도구형 소프트웨어의 실제 게재 사례는 이전 조사 자료의 BehaviorMetrics, RehabBot, Metriplane과 연결하되 각 논문의 기여를 CellIO의 최초성 근거로 바꾸지 않는다. [S20–S23]

### 25.3 필수 재현 명령 흐름

~~~powershell
conda activate cellio-isaac62
python -m cellio doctor --profile isaac62 --output artifacts/doctor.json
python -m cellio assets verify --offline
python -m cellio campaign plan --suite canonical-v1 --output artifacts/canonical-v1
python -m cellio campaign run --plan artifacts/canonical-v1/campaign.plan.json
python -m cellio report --campaign artifacts/canonical-v1 --output reports/canonical-v1
python -m cellio acceptance --level r3 --report reports/acceptance.json
~~~

이 흐름은 구현할 interface다. acceptance는 아직 수행하지 않은 oracle/reset/overhead/soak/clean-install을 자동으로 PASS하지 않고 NOT_RUN으로 표시한다. 개별 suite 실행 기록을 모아야 R3 보고서가 완성된다.

### 25.4 논문용 최소 그림·표

| 산출물 | 입증할 내용 | 원자료 |
|---|---|---|
|Figure1 구조/정보경계|C1재사용구조|module/port정의|
|Figure2 두장면과실제작업|적용범위·scene차이|고정camera실제render|
|Figure3 한고장timeline|활성/발현/제어/물리/판정분리|rawtrace|
|Figure4 발현·task/compliance결과|실험coverage와결과분포|820campaign|
|Figure5 oracle/mutant검증|C2독립검사|fixture+pairedreport|
|Figure6 overhead/reset/soak|실용성·실행안정성|성능suite|
|Table1 기존도구비교|실제재사용차이|코드/문서검증|
|Table2 module/지원범위|사용가능기능|release|
|Table3 조건·분모·실패유형|평가계약|plan/ledger|
|Table4 환경·version·asset|재현성|lockfiles|

Fig2는 생성 이미지로 성능 시연을 대체하지 않는다. 실제 Isaac 실행 render를 사용한다. 그래프는 수치를 수작업으로 다시 입력하지 않고 검증된 trace에서 생성한다.

### 25.5 최종 개발 완료 선언 형식

release acceptance.json은 아래를 반드시 포함한다.

~~~json
{
  "software_version": "1.0.0",
  "spec_version": "1.0",
  "status": "NOT_RUN",
  "must_gates": [],
  "should_metrics": [],
  "campaign_ids": [],
  "evidence_manifest": null,
  "known_limitations": [],
  "review_date": null
}
~~~

초기값은 NOT_RUN이다. 구현자가 빈 gate 목록으로 PASS를 반환할 수 없다. G01–G15가 모두 존재하고 증거가 검증됐을 때만 engineering_status=R3_COMPLETE를 생성한다. 논문 투고 승인·채택을 의미하는 status는 소프트웨어가 자동 생성하지 않는다.

---

<a id="sec-26"></a>

## 26. 근거·설계 결정·해석 범위

### 26.1 주요 고정 결정

| ADR | 결정 | 이유 |
|---|---|---|
|ADR001 개정|Windows 11 Conda·Isaac Sim 6.2.0.0·CPU PhysX, WSL2 오프라인 재판정|장비의 실제 설치 경로와 교차 환경 판정 검증|
|ADR002|120Hzphysics/servo/sensor,30HzFSM|물리동작과상위I/O의분리|
|ADR003|한episode한fault|6family의의미와근거를먼저검증|
|ADR004|fresh_process본평가|실험격리,reset기능은별도스트레스검사|
|ADR005|F3FIFO+종료flush|실제전송과복원의명확한경계|
|ADR006|F4최신validintent복원|고장해제뒤숨은queue tail방지|
|ADR007|F6finite-forcepositionhold|텔레포트없는구동제한모델|
|ADR008|독립raworacle·4판정|제어기자기보고와정답분리|
|ADR009|fullH·finalS_H관측전용|결과별조기종료편향과미래witness방지|
|ADR010|JSONLgzip·markerlast|의존성축소와부분결과구별|
|ADR011|장면A/B·단일Franka|기능재사용증거와개발범위통제|
|ADR012|OpenPLC는R4|시뮬레이터단독R3를완결가능하게유지|

### 26.2 출처 목록

확인 기준일은2026-11-18이다. 버전이 박힌 공식 문서를 우선하며, 아래 링크의 내용을 명세의 실제 실행 성과로 해석하지 않는다.

| ID | 출처·연결되는 내용 |
|---|---|
|S01|[NVIDIA Isaac Sim 다운로드](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/download.html):6.2.0릴리스정보|
|S02|[6.2시스템요구사항](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/requirements.html):OS·GPU·testeddriver|
|S03|[6.2Python설치](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/install_python.html):Python3.12경로|
|S04|[Standalone Python](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/python_scripting/manual_standalone_python.html):SimulationApp부트스트랩|
|S05|[SimulationManager API](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/py/source/extensions/isaacsim.core.simulation_manager/docs/api.html):engine·physics초기화·manualstep·Fabric|
|S06|[Experimental Prims API](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/py/source/extensions/isaacsim.core.experimental.prims/docs/api.html):actualstate·DOF·physicsview|
|S07|[cuMotion RMPflow](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/cumotion/tutorial_rmpflow.html):motionadapter·reset|
|S08|[cuMotion Robot Configuration](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/cumotion/tutorial_robot_configuration.html):로봇설정·site|
|S09|[Physics Resources](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/physics/physics_resources.html):물리재현·설정문맥|
|S10|[isaac-plc-digital-twin](https://github.com/Nebras4u/isaac-plc-digital-twin):인접소프트웨어비교대상|
|S11|[industrial-automation-cell-simulator](https://github.com/rgcb01/industrial-automation-cell-simulator):인접구현비교대상|
|S12|[otlab-sim](https://github.com/Furiadelimon/otlab-sim):I/O·통신고장비교대상|
|S13|[Manipulator Assets](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/assets/usd_assets_robots_manipulator.html):Franka자산경로|
|S14|[Motion Quick Start](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/robot_motion_experimental/quick_start/index.html):modernmotion자료형·planningworld|
|S15|[Franka Pick-and-Place](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/examples/manipulation_franka_pick_place.html):공식하위동작예제|
|S16|[Storage API](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/py/source/extensions/isaacsim.storage.native/docs/api.html):assetroot검증|
|S17|[OmniPhysics Joints](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/110.1/dev_guide/rigid_bodies_articulations/joints.html):prismatic·drive|
|S18|[OmniPhysics Rigid Bodies](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/110.1/dev_guide/rigid_bodies_articulations/rigid_bodies.html):surfacevelocity·material·mass|
|S19|[SoftwareX Guide for Authors](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors):저널공식제출규칙|
|S20|[BehaviorMetrics, DOI10.1016/j.softx.2024.101702](https://doi.org/10.1016/j.softx.2024.101702):로봇평가소프트웨어사례|
|S21|[RehabBot, DOI10.1016/j.softx.2026.102800](https://doi.org/10.1016/j.softx.2026.102800):로봇소프트웨어사례|
|S22|[Metriplane, DOI10.1016/j.softx.2026.103104](https://doi.org/10.1016/j.softx.2026.103104):평가도구사례|
|S23|기존 SoftwareX 주제 조사 보고서. 비공개 선행 조사 자료. 공개 검증에는 S01–S22 및 S24–S25를 사용한다.|
|S24|[SimulationApp API](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/py/source/extensions/isaacsim.simulation_app/docs/index.html): 앱 설정·종료 |
|S25|[cuMotion Python API](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/py/source/extensions/isaacsim.robot_motion.cumotion/docs/api.html): RmpFlowController 생성자·WorldInterface device |

### 26.3 실행으로 확정할 산출물

환경 exact patch/hash, 로봇 실제 DOF·mimic·collision binding, TCP calibration, 정상 장면 성공, fault 발현률, 실행 속도·메모리·검출 결과는 현재 미측정이다. 이 문서는 이를 채우는 절차·기준·필드·실험을 정한 것이다.

문서상 구조·값을 그대로 시작값으로 사용한 뒤 commissioning에서 실제 물리 실행을 검증해야 한다. simulation-only 결과는 해당 시뮬레이션 조건의 소프트웨어 검증이며, 보유한 실물 로봇의 성능이나 산업 안전 인증으로 확장하지 않는다.

<a id="sec-27"></a>

## 27. 부록: 독립 oracle fixture 생성 명세

### 27.1 공통 규칙

fixture ID는 ora.<cell_id>.<family>.c01…c10, 추가 NA는 n01/n02다. 각 표의 같은10개 논리를 A/B의 signal·geometry·endpoint에 각각 적용해120개를 만든다. 일반 fixture의 H는12, 물리 목표 fixture는240이다. step이 생략된 구간은 마지막 값을 유지하되, 표에서 “나머지 FALSE”라고 하면 해당 원자는 명시적으로 FALSE다.

이 fixture는 OraclePort의 단위 입력이다. 정상 artifact를 가장하지 않는다. UNKNOWN 시험에서 필요한 raw evidence를 valid=false 또는 missing input으로 만든 경우, production runner는 별도로 INFRA_ERROR를 기록할 수 있다. oracle는 그 불명확성을 PASS/FAIL로 단정하지 않아야 한다.

FixtureBuilder는 각 모델의 필수 필드·ID·정렬·기본값을 채운다. expected label은 아래 표의 상수로 직접 입력한다. production predicate 호출로 expected를 만들지 않는다. 계층별 assertion은 final verdict, first_witness_step 또는 null, obligation_count, reason code다.

### 27.2 O1 순서

A의 before는 실제 belt-stop dwell 충족, after는 pickup 접근 적용이다. B는 실제 hand clear와 clamp close 적용이다. 다른 전제조건은 모두 TRUE로 둔다.

| case | 입력 | 기대 |
|---|---|---|
|c01|before step2 P1, after4 P6, 같은job|PASS|
|c02|before4 P1, after4 P6|PASS|
|c03|job0의2→4, job1의6→8, 각각 matching|PASS|
|c04|before1, after12의최종판정에제시되는작업사건|PASS|
|c05|after4, before5|FAIL at4|
|c06|after4, before는전체구간known absent|FAIL at4|
|c07|같은step에서after P4가before P7보다먼저|FAIL at4|
|c08|before는job0, after는job1|FAIL at4|
|c09|after4, before가발생했을가능성이있는2–3구간의근거UNKNOWN|UNKNOWN|
|c10|요구범위H12이나S3까지만관측, after가아직없음|UNKNOWN|

c04는 phase-order engine 자체를 검증하는 합성 event fixture다. production의 final S_H에서 새 작업 명령을 발행하도록 허용하는 사례가 아니다. 별도 T-CLK-04는 그 발행을 거부해야 한다.

### 27.3 O2 취소·명령 충돌

초기 generation1, operation0, active command IDc0다. A는gripper, B는clamp endpoint를 사용한다. 검사 규칙은 “유효 token의 명령만 적용되고 취소된 token은 적용되지 않는다”는 invariant다.

|case|입력|기대|
|---|---|---|
|c01|cancel4→operation1, old due5는CANCELLED|PASS|
|c02|cancel4와old due4, cancel을먼저처리해old 미적용|PASS|
|c03|cancel4 뒤epoch1 HOLD가같은tick에적용|PASS|
|c04|취소없이유효operation0 명령이4에한번적용|PASS|
|c05|cancel4 뒤old operation0이5에적용|FAIL at5|
|c06|cancel4 P5 뒤old operation0을같은P6에적용|FAIL at4|
|c07|generation2 reset뒤generation1 명령적용|FAIL at7|
|c08|같은command ID를다른payload로두번APPLIED|FAIL at두번째적용|
|c09|적용여부가필요한receipt가UNKNOWN|UNKNOWN|
|c10|H12 scope인데S8에서관측중단,지금까지위반없음|UNKNOWN|

### 27.4 O3 입력 신선도

step20에서 새 일반 작업 명령을 발행하는 admission fixture다. fixture의 H는24로 확장한다. A는gripper.closed를 사용하는 lift, B는clamp.closed를 사용하는 process on이다. CANCEL/HOLD 예외는 별도 T-CTRL/T-CMD에서 검사한다.

|case|입력|기대|
|---|---|---|
|c01|GOOD,source20,value true|PASS|
|c02|GOOD,source8,age12|PASS|
|c03|GOOD,source16,age4,정확한bool type|PASS|
|c04|필요한2개signal이각각age8/12로GOOD|PASS|
|c05|GOOD,source7,age13|FAIL at20|
|c06|BAD,value null|FAIL at20|
|c07|MISSING placeholder|FAIL at20|
|c08|GOOD이지만source21로미래시각|FAIL at20|
|c09|관측품질MISSING과구별되는oracle입력자료자체의누락|UNKNOWN|
|c10|필수signal하나는usable,다른것의원자료가UNKNOWN|UNKNOWN|

### 27.5 O4 응답 기한

기본 trigger4, lower1, upper5, 즉 허용 snapshot5…9, dwell0다. “응답”은 같은 command/job에 연결된 실제 상태 조건이다.

|case|입력|기대|
|---|---|---|
|c01|S5에서최초TRUE|PASS,witness5|
|c02|S9에서최초TRUE|PASS,witness9|
|c03|dwell2로변경,S7·8·9가TRUE|PASS,witness9|
|c04|trigger2/job0→response6,trigger4/job1→response8,각window충족|PASS,obligation2|
|c05|S4만TRUE,5…9는FALSE|FAIL at9|
|c06|S10에서최초TRUE|FAIL at9|
|c07|5…9가모두FALSE|FAIL at9|
|c08|dwell2,S8만TRUE,나머지FALSE|FAIL at9|
|c09|S8에서종료,5…8은FALSE|UNKNOWN|
|c10|S7은UNKNOWN,5·6·8·9는FALSE,dwell0|UNKNOWN|

### 27.6 O5 물리 완료

H240, 기본 goal 상태는S140부터유지한다. A/B의 output 중심과13장조건을 사용한다. B는S0…120에 유효 process history를 합성하고 이후 output 이동을 제시한다. analytic fixture의 pose 변화는 물리적으로 실행한 trajectory라는 주장이 아니다.

|case|입력|기대|
|---|---|---|
|c01|모든goal조건S140…240 TRUE|PASS,첫dwell종료200|
|c02|모든goal조건S180…240 TRUE|PASS,witness240|
|c03|part yaw=.02rad,8corner가box내부,기타정상|PASS|
|c04|중심수평오차정확히.015m,속도정확히.01m/s,기타경계안|PASS|
|c05|다른Part_001만목표에있고필수Part_000은밖|FAIL at240|
|c06|필수part중심수평오차.015001m를계속유지|FAIL at240|
|c07|선속도.010001m/s를계속유지|FAIL at240|
|c08|DONE/destination.present는true이나finger각q=.020m로닫힘|FAIL at240|
|c09|goal은180부터TRUE이나최종관측239|UNKNOWN|
|c10|goal은180부터,210의필수part state만UNKNOWN,다른허용60-step witness없음|UNKNOWN|

### 27.7 O6 복원·초기화

faultA2/E6, 기본복원window6…9로짧게둔다. 이 family의 expectation은 “기한내복원 response”와 “이후old packet/override 없음 invariant”의 결합이다. reset 사례는 별도R16을 사용한다.

|case|입력|기대|
|---|---|---|
|c01|E6 freshsample source6,이후정상|PASS|
|c02|F4의due6 oldqueue폐기,정상router/hold적용,새일반명령없음|PASS;functional reuse UNOBSERVED|
|c03|F6 override6에서제거,정상boundedtarget commit|PASS|
|c04|reset generation2,oldgeneration1 packet7에거부,newgeneration2정상|PASS|
|c05|E6복원뒤old delayedpacket7에적용|FAIL at7|
|c06|E6뒤F6 override가계속target을변조|FAIL at6|
|c07|reset뒤queue 또는capturecache가남음|FAIL atreset검사|
|c08|post-reset body position오차.00011m|FAIL atS0비교|
|c09|E6전에S5에서종료|UNKNOWN|
|c10|E6정상target receipt/actualstate의필수근거UNKNOWN|UNKNOWN|

### 27.8 NOT_APPLICABLE 24개

각 cell×family의 기본 규칙에 명시적인 applicability wrapper를 추가한 별도 fixture를 만든다. n01은 scope=after_event, trigger=test_scope_enter로 설정하고 complete horizon에 해당 event가 없다. n02는 applicability=fixture_enabled이고 전체 구간 known FALSE다. wrapper를 포함한 이 두 시험의 최종 판정은 NOT_APPLICABLE이다. required_coverage=true이면 coverage_missed=true도 assert한다. production O2의 episode invariant와 시작부터 활성인 GOAL은 그대로 두며, 단순히 작업 event가 없다는 이유로 NA로 바꾸지 않는다.

추가 부정 테스트로 불완전 horizon의 “antecedent를아직못봄”은n01이아니며UNKNOWN인지검사한다. UNKNOWN과NOT_APPLICABLE의구분을requirement engine이자동으로collapse하지않는다.

### 27.9 결정론적 장면 경계 사례

A는initial Xoffset±.001, Yoffset±.004, yaw±.03의2³=8개조합, B는Xoffset±.002,Yoffset±.002,yaw±.03의8개조합을사용한다. 별도파일initial_boundaries.json에실제숫자를저장한다. 이는seed0–4 pilot sampling과별개다.

각조합에서healthy작업1회와실제load/pickup clearance를확인한다. corner case가정상실행을막으면분포·기하·waypoint를본평가전에수정하고동결한다. 극값시험결과를본평가의20random-state통계에섞지않는다.

---

**명세 종료.** 이 파일의 요구사항·모델·시험·합격 기준이 CellIO 1.0.0 개발의 기준이다. 실제 구현 완료와 성능은 생성될 acceptance report 및 원시 실행 자료로만 확정한다.


---


---


---


---


---


