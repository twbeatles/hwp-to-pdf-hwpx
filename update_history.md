# HwpMate 업데이트 이력

이 문서는 현재 배포 기준인 `hwptopdf-hwpx_v4.py`와 관련 설정, 문서, 빌드 구성의 변경 사항을 기록합니다.

## 현재 상태

- 앱 버전: `v9.1.0`
- 주 엔트리포인트: `hwptopdf-hwpx_v4.py`
- 빌드 설정: `hwp_converter.spec`
- 정적 검사 기준: `pyrightconfig.json`
- 배포 산출물: `dist/HWP변환기_v9.1.0.exe` (릴리즈: `HwpMate-v9.1.0.exe`)
- 보안 모듈 번들: `hwpmate/resources/security/FilePathCheckerModuleExample.dll`

## 2026-08-16 자동 업데이트 및 종합 감사 개선 반영 (v9.1.0)


- **Ed25519 서명 기반 자동 업데이트 파이프라인:**
  - GitHub Releases `updates/latest.json` 매니페스트 및 Ed25519 비대칭 서명/SHA-256 해시/HTTPS 무결성 검증.
  - 새 버전 다운로드 시 프로그레스바 및 실시간 취소 지원, 업데이트 후 `--smoke` 검증 실패 시 자동 롤백 복구 지원.
  - UI 상단 헤더에 `🔄 업데이트` 버튼 및 팝업 안내 다이얼로그 추가.
- **헤드리스 CLI 일괄 변환 모드 추가:**
  - GUI 없이 `--input`, `--format`, `--output`, `--recursive`, `--overwrite`, `--no-backup`, `--retry`, `--pdf-export-mode`를 통한 배치 스크립트 변환 지원.
  - `--smoke` 플래그를 통한 모듈/의존성 무결성 진단 지원.
- **대량 변환 한글 COM 프로세스 자동 재순환:**
  - `CONVERTER_RECYCLE_BATCH_COUNT=200`: 연속 변환 200건마다 한글 프로세스를 안전하게 정리 및 재초기화하여 장시간 대량 변환 시 메모리/GDI 핸들 누수 방지.
- **감사 및 문서 정합성 일치:**
  - 배포 실행 파일명을 `HwpMate-v9.0.exe`로 표준화하고 README 및 CI/CD 워크플로우 동기화.
- **검증:** `pytest` 168 passed (100%), `pyright .` 0 errors.

## 2026-08-05 재감사 잔여 항목 반영


- **워커 suppress 전역 금지:** `_suppress_hwp_ui_flash` 는 소유 PID 가 있을 때만 HWND 조작 (`or None` 제거).
- **변환 직전 폴더 캐시:** `FOLDER_SCAN_CACHE_CONVERT_MAX_AGE_SECONDS=90` — 오래되면 자동 재스캔.
- **긴 경로:** `com_path_candidates` / `\\?\` 확장 경로 Open·SaveAs·Print 재시도, Preflight 260자 차단.
- **백업 보관 개수 UI/설정:** `backup_max_files_per_stem` (1~100).
- **암호·접근 실패 힌트:** Open/변환 예외에 안내 문구.
- **SaveAs 무산출물 시 Print 폴백** 보강.
- 검증: `pytest` 143 passed, `pyright .` 0 errors.

## 2026-08-05 PROJECT_AUDIT 권장안 일괄 반영

- **PID 미추적 시 전역 HWP 조작 금지:** `HwpSecuritySession.allows_window_control` — 소유 PID 없으면 숨김/전면화/자동 클릭 생략.
- **SaveAs PDF 매직 검증:** `is_valid_pdf_file` 적용, 실패 시 PrintToPDFEx 경로 1회 폴백.
- **재변환 재진입 가드:** `retry_failed_tasks` 에 `is_planning` + `_confirm_preflight_and_start_worker` 공통화.
- **취소/강제종료 UX:** COM 대기 안내 문구, 슬라이스 wait, 강제종료 대기 연장.
- **폴더 캐시:** 디렉터리 mtime 단독 하드 실패 제거(샘플 파일 존재 우선).
- **보안 버튼:** 「모두 허용」 정확 일치만 클릭.
- **긴 경로:** Preflight 경고 (`WINDOWS_PATH_WARN_LENGTH`).
- **PDF 내보내기 UI:** 변환 옵션 콤보 (`pdf_export_mode` 설정 연동).
- **백업 상한:** 동일 stem 백업 `BACKUP_MAX_FILES_PER_STEM` 초과 시 오래된 파일 prune.
- **암호 문서 힌트:** 예외 메시지 키워드 시 안내 문구.
- 검증: `pytest` 140 passed, `pyright .` 0 errors.

## 2026-08-05 SOLID 패키지 분할 리팩토링

- 대형 단일 모듈을 **기능별 패키지**로 분리 (동작 불변, 공개 import 경로 호환 re-export).
- `services/hwp_converter/`: COM 타입, 프로세스 스냅샷, 산출물 스냅샷, ProgID, 변환기.
- `services/hwp_print_settings/`: 상수, 모드, 프린터 탐지, 인쇄 리셋, PDF 내보내기.
- `windows_integration/`: 관리자·DnD 정책, HWND 조회, 한글 창 제어, NativeDropFilter.
- `ui/dialogs/`, `ui/theme/`, `ui/main_window_ui/`: 다이얼로그·테마 QSS·UI 빌더 분리.
- `ui/main_window_controllers/{conversion,file_selection,lifecycle}/`: 컨트롤러 패키지.
- `workers/conversion_worker/`: Protocol, 백업, 요약, 워커 루프.
- `hwp_converter.spec`: 패키지 분할 이력·핵심 패키지 `hiddenimports` 보강.
- `.gitignore`: 로컬 설정/로그/스모크 출력·일회성 분할 스크립트 패턴 보강.
- 문서 동기화: `Claude.md`, `gemini.md`, `PROJECT_STRUCTURE_ANALYSIS.md`, `PROJECT_AUDIT.md`, `HWP_COM_SMOKE_TEST_CHECKLIST.md`.
- 검증: `pytest` 132 passed, `pyright .` 0 errors.

## 2026-08-04 PROJECT_AUDIT 인쇄 경로 전면 개선

- **물리 Print Execute 제거:** `CreateAction("Print").Execute` 폴백 삭제 (물리 프린터 오출력 방지).
- **PDF 기본 전략:** `pdf_export_mode=saveas_first` (SaveAs → 실패 시 PrintToPDFEx/RunToPDF). 옵션 `print_to_pdf_ex_first`.
- **감사 필드:** `export_method` (CSV/JSON), `CONFIG_VERSION=3`.
- **PDF 검증:** `%PDF` 매직·최소 크기, 불완전 산출물 정리.
- **취소 협력:** `convert_file(..., cancel_check=)` + 워커 `cancel_requested`.
- **가상 프린터 탐지:** `win32print` Enum 우선, 후보 상한.
- **폴더 캐시:** 디렉터리 mtime·파일 수로 신규 변경 감지.
- `DOCUMENT_LOAD_DELAY` 0.5s. Claude.md/README/스모크 체크리스트 동기화.

## 2026-08-04 PDF·이미지 인쇄 설정 best-effort 초기화

- 변환 전 `PrintMethod=0`(자동/1쪽씩) 등 세션 인쇄 기본값을 best-effort로 맞춥니다 (`hwp_print_settings.py`).
- 문서 편집 용지(A4 등 원본)는 강제 변경하지 않습니다.
- 원본 HWP/HWPX 디스크 파일은 저장하지 않습니다 (`Clear`만).

## 2026-08-04 한글 COM UI 깜빡임 완화

- 보안 대화상자 판별에서 메인 창 제목(`… - 한글`)에 걸리던 과도한 힌트(`한글`/`hwp` 등)를 제거했습니다.
- 연결·Open·전면화 폴링 시 메인 편집 창을 `XHwpWindows.Visible=False` + `SW_HIDE` 로 best-effort 숨깁니다.
- 보안·허용 대화상자는 숨기지 않고 전면화/자동 클릭 경로를 유지합니다.
- 기동 순간 1프레임 플래시·한컴 내부 스플래시는 완전 차단하지 않습니다 (COM 모델 한계).

## 2026-08-04 v9.0 릴리즈

- 앱 버전·PyInstaller 산출물 이름을 `9.0` / `HWP변환기_v9.0.exe`로 올렸습니다.
- README를 사용 방법·프로그램 설명 중심으로 개편했습니다.
- PROJECT_AUDIT 권장안(계획 중 종료 가드, Preflight 부하 완화, 전면화 지연, 캐시 샘플 분산, COM Open=0 판정, 워커 스냅샷 등)을 반영했습니다.
- 단위 테스트·pyright 기준을 유지한 채 GitHub 릴리즈 `v9.0`을 발행합니다.

## 2026-08-04 PROJECT_AUDIT 권장안 구현

### 1단계 (안전·재진입)
- 계획 중(`is_planning`) 종료: `close_requested` / `close_after_plan` 으로 창 파괴 방지 후 계획 정리·재종료
- `wait_for_active_scan` 이 `close_requested` 시 즉시 False
- Preflight 대량 부하 완화: 상세 목록 상한, 읽기 심층 검사 샘플 상한

### 2단계 (안정성)
- `engine_status_received` 전 한글 창 전면화 폴링 no-op (연결 후 1회 폴)
- 폴더 캐시 신선도 샘플을 앞·뒤·중간 분산
- 폴더 밖 캐시 경로 시 `relative_to` flat 폴백 + 경고
- COM Open/SaveAs 실패 판정에 `0` 포함 (`is_com_failure_result`)
- busy 중 테마 버튼 잠금

### 3단계 (구조·테스트·문서)
- `ConversionTask.snapshot` + 워커 summary 복사본 emit
- 회귀 테스트 보강 (close/planning, Open=0, Preflight 생략, 캐시 샘플 등)
- Claude.md Spec Kit 포인터 정리, PROJECT_AUDIT 동기화

### 보류 (의도적)
- 전체 폴더 계획 수립 백그라운드 워커화 (대량 시 Preflight 완화로 우선 대응)
- DLL Authenticode 서명 검증 (SHA-256 유지)

## 2026-08-03 문서·spec·gitignore 정합 및 배포

- README 프로젝트 구조·기능 목록을 보안 모듈·계획 잠금·캐시 정책과 동기화
- gemini.md 보안/계획 잠금 지침 보강, PROJECT_STRUCTURE_ANALYSIS 스냅샷 갱신
- `hwp_converter.spec`에 `hwp_security_session` hiddenimport 및 이력 주석
- `.gitignore`: PyInstaller warn, `*.dll.tmp`, `.claude/`, 루트 exe, 캐시 보강
- HWP_COM_SMOKE 체크리스트에 planning·캐시 신선도 항목 추가

## 2026-08-03 재감사 권장안 구현

### 1단계
- `is_planning` 계획 잠금: 스캔 대기·작업 수집 중 시작/입력/드롭 재진입 차단
- `engine_status_received` 전·모듈 미실패 시 「모두 허용」 자동 클릭 금지

### 2단계
- 폴더 캐시 만료(`FOLDER_SCAN_CACHE_MAX_AGE_SECONDS`)·샘플 존재 검증
- 설정 복원 폴더 자동 미리보기 스캔; 콜드 변환 시 비동기 스캔 후 대기 (UI 동기 재스캔 제거)
- 프로세스 추적/스냅샷 실패 상태바 문구 보강
- Claude.md / PROJECT_STRUCTURE_ANALYSIS 동기화

### 3단계
- 미사용 `error_occurred` 시그널 제거 (요약은 `task_completed` 단일 경로)
- 계획 잠금·연결 구간 자동 클릭·캐시 신선도 회귀 테스트

## 2026-08-03 감사 권장안 구현 (신뢰성·보안 UX)

### 1단계
- 폴더 스캔 `wait` 후 Qt 시그널 드레인 + 캐시 필수(동기 재스캔 race 제거)
- 전면화/자동 클릭을 소유 PID 우선, 보안 대화상자 위주로 제한
- 보안 모듈 등록 성공 시 「모두 허용」 자동 클릭 생략·폴링 간격 완화
- pyright 타입 정리 (`QTimer`, nativeEventFilter, 테스트 어노테이션)

### 2단계
- 보안 모듈 DLL SHA-256 무결성 검증 후 레지스트리 등록
- 자동 클릭 쿨다운·세션 상한, 버튼 매칭 강화
- Toolhelp 스냅샷 연속 실패 시 결과/상태 경고
- 시작 토스트에 허용 창 힌트 합침, 스모크 체크리스트 보강

### 3단계
- `HwpSecuritySession` 으로 폴링 정책 분리
- 설정/UI: `auto_accept_security_dialog` (「모두 허용」 자동 시도)
- 회귀 테스트: 세션 정책, 폴링 PID, 캐시 필수, DLL 무결성

## 2026-08-03 토스트 고대비 UI

### 변경
- `ToastWidget` 배경을 짙은 슬레이트(`rgba(15,23,42,0.97)`)로 통일하고 본문·아이콘을 **흰색 굵은 글씨**로 표시.
- 성공/경고/오류/정보 아이콘별 **왼쪽 강조 테두리 색**과 그림자 효과로 시인성 강화.
- 메시지 길이에 맞게 토스트 폭·높이 가변 (최대 약 440×140).
- README에 토스트 시각 설계 기준을 명시.

## 2026-08-03 보안승인 모듈 자동 설치 (팝업 근원 해결)

### 원인
- 파일 Open/Save 시 뜨는 허용 팝업은 UI 클릭 문제가 아니라 **한컴 FilePathCheckDLL 보안모듈(DLL+레지스트리)** 미설치가 근본 원인.
- `RegisterModule` 예외만 없으면 “성공”으로 로깅했으나 레지스트리에 DLL 경로가 없으면 팝업이 계속 뜸.
- 자동 「모두 허용」 폴링을 **연결 성공 직후 중지**해, 실제 팝업이 뜨는 Open 구간에서 동작하지 않음.

### 수정
- `FilePathCheckerModuleExample.dll` 번들 + `%LOCALAPPDATA%\HwpMate\security` 복사
- `HKCU\SOFTWARE\HNC\HwpAutomation\Modules` 자동 등록 후 `RegisterModule`
- 폴링을 변환 종료까지 유지, 전면화는 보안/대화상자 위주로 축소
- PyInstaller `datas` 에 보안 DLL 포함

## 2026-08-03 CMD 플래시 제거·보안 모듈 폴백

### 근원
- `_snapshot_hwp_pids` 가 `tasklist` 서브프로세스를 호출해 변환/전면화 폴링 중 **콘솔 창이 반복 플래시**됨.
- `taskkill` 도 `CREATE_NO_WINDOW` 없이 호출됨.

### 수정
- PID 스냅샷을 **Toolhelp32** 로 교체 (외부 콘솔 프로세스 제거).
- `taskkill` 에 `CREATE_NO_WINDOW` 적용.
- 보안 모듈 `RegisterModule` 별칭 폴백 (`FilePathCheckerModuleExample` 등).
- 「모두 허용」 버튼 **best-effort** 자동 클릭 (모듈 실패 시 보조, 주 경로는 보안 모듈).
- UI Tip/결과 경고 문구 정리.

## 2026-08-03 한글 허용 창 전면화·인쇄 설정 안내

### UX
- 변환 시작 후 UI 스레드에서 한글/허용·보안 창을 주기적으로 전면화해 작업 표시줄 뒤로 가려지지 않게 합니다.
- 연결 상태 문구·토스트·상태바에 “허용 창 확인” 안내를 표시하고, 연결 성공 시 “연결 중” 고착을 해제합니다.
- 사전 점검·사용법·프로그램 정보에 **인쇄/용지 설정에 따라 변환 결과가 달라질 수 있음**을 안내합니다.

### 구현 위치
- `windows_integration.bring_hwp_windows_to_foreground`
- `ConversionController` 전면화 폴링 타이머
- `constants.PRINT_SETTINGS_NOTICE` / `HWP_PERMISSION_HINT`

## 2026-08-03 onefile 실행 크래시 수정

### 원인
- Windows 이벤트 로그: `QtCore.pyd` 접근 위반(`0xC0000005` / `0xC000041D`)
- `NativeDropFilter.nativeEventFilter`가 sip에 `None` result를 반환해 frozen/onefile 실행 직후 종료
- `hwp_converter.spec` 바이너리 필터가 `Qt6Network.dll`, `opengl32sw.dll`을 패키지에서 제거 (Qt6Gui 런타임 의존)

### 수정
- `nativeEventFilter` 반환을 항상 `(bool, int)`로 고정, `wintypes.MSG.from_address` 사용
- 네이티브 드롭 필터 설치를 `QTimer.singleShot(0, ...)`로 지연
- spec에서 `Qt6Network`/`opengl32sw` 유지, 위험한 stdlib exclude·UPX 비활성
- `pyinstaller --noconfirm --clean hwp_converter.spec` 재빌드 후 10초 이상 생존 확인

## 2026-07-15 기능 감사 후속 개선

### 변환 안정성
- 취소 요청 후 COM 오류가 난 진행 중 파일은 `실패` 대신 `취소됨`으로 집계합니다.
- 진행률은 작업 **완료 개수** 기준으로 갱신합니다.
- `ConversionWorker`가 COM apartment를 소유하고, `HWPConverter`는 `manage_com_apartment=False`로 중복 CoInit/Uninit을 피합니다.
- 강제 종료 직전 살아있는 한글 프로세스 PID만 `taskkill` 하도록 PID 재사용을 완화합니다.

### 폴더 스캔/계획
- 폴더 미리보기는 전체 지원 확장자를 캐시하고, 변환 시작 시 UI 스레드 전체 재스캔 없이 캐시를 재사용합니다.
- 포맷 변경 시 캐시가 있으면 재스캔 없이 변환 가능 수만 다시 계산합니다.
- 폴더 스캔 중 변환 시작 시 스캔을 취소하지 않고 완료를 대기합니다 (`FOLDER_SCAN_WAIT_MS`).
- 스캔 취소 대기 기본값을 200ms → 2000ms로 상향했습니다.

### UX/문서
- 사전 점검에 프로세스 추적/강제 종료 제한 안내를 추가했습니다.
- 결과 다이얼로그에서 **실패 항목 재변환**을 지원합니다.
- 사용법/프로그램 정보/README의 성공 판정(보조 산출물 포함)과 설정·로그·락 경로를 동기화했습니다.

## 2026-06-11 감사 개선 구현

### 중복 실행과 변환 중 상태 보호
- `QLockFile` 기반 단일 인스턴스 잠금을 추가해 두 번째 실행이 기존 COM/출력 파일 상태를 건드리지 않도록 했습니다.
- 변환 중 `Ctrl+Enter`, 파일/폴더 메뉴 액션, 파일 목록 변경, 네이티브 드롭을 명령 진입점에서 차단합니다.
- worker 종료 시 마지막 결과에 실패/취소가 있으면 한글 상태 표시가 곧바로 정상 대기로 덮이지 않도록 했습니다.

### 산출물/저장 안정성
- 이미지/HTML 보조 산출물 정책을 `services/artifact_policy.py`로 분리하고, 성공 판정과 출력 충돌 회피가 같은 후보 규칙을 공유하도록 했습니다.
- 같은 stem 접두 매칭은 delimiter 경계 기준으로 좁히고, 보조 디렉터리 재귀 snapshot에는 상한을 둡니다.
- `Open()`이 `False`를 반환하는 경로에서도 best-effort `Clear(option=1)`를 수행합니다.
- 설정 저장은 성공 여부를 bool로 반환하며, 결과 TXT/CSV/JSON 저장은 임시 파일 작성 후 교체하는 원자 저장 방식으로 바꿨습니다.

### 검증 보강
- 단일 인스턴스, busy guard, 보조 산출물 충돌, 원자 저장, 설정 저장 실패, Windows 경로 검증, HWP Open 실패 정리 회귀 테스트를 추가했습니다.

## 2026-06-10 MainWindow SOLID 리팩토링

### 구조 분리
- `MainWindow`를 기존 import 경로와 underscore 메서드 호환 래퍼로 유지하면서 실제 런타임 책임을 `ui/main_window_controllers/` 패키지로 분리했습니다.
- `MainWindowState`로 변환/스캔/종료/선택 포맷 상태를 모으고, 테마/표시, 파일 선택/스캔, 변환, 네이티브 드롭, 메뉴/트레이/종료 처리를 컨트롤러별로 나눴습니다.
- `main_window_ui.py`는 `MainWindowCallbacks`를 받아 시그널을 연결하도록 바꿔 레이아웃 빌더와 `MainWindow` 내부 메서드 결합을 줄였습니다.

### 동작 보존과 검증
- 폴더 모드 단일 폴더 드롭, 파일 모드 비동기 스캔, 포맷 변경 시 미리보기 재스캔, 동일 형식 건너뜀 결과, 종료 중 워커 취소/강제 종료 흐름을 유지했습니다.
- 컨트롤러 경계 테스트를 추가해 스캔 상태 정리, 파일 테이블 반영, 출력 경로 검증, skipped-only 결과, 네이티브 드롭 분기를 확인합니다.

## 2026-05-12 기능 구현 리스크 보강

### 출력/산출물 안전성
- `기존 파일 덮어쓰기`가 켜져 있어도 같은 실행 배치 내부의 중복 출력 경로는 자동 이름 변경으로 분리합니다.
- 기존 출력 파일이 있을 때는 단순 존재/0바이트 검사 대신 저장 전후 산출물의 크기와 수정 시각 변화를 확인해 새로 생성 또는 갱신된 경우만 성공으로 집계합니다.
- 이미지/HTML 계열은 기본 출력 파일 외에도 같은 stem 기반 보조 산출물을 수집해 결과에 기록합니다.

### 결과 리포트와 COM 상태
- `ConversionTask`와 CSV/JSON 결과에 `created_files`, `output_size`, `output_mtime`, `save_format`, `progid_used` 감사 필드를 추가했습니다.
- 보안 모듈 등록 실패와 앱 소유 한글 PID 추적 실패를 경고로 남겨 COM 환경 문제를 결과에서 확인할 수 있게 했습니다.
- 변환 워커의 예상 밖 예외도 구조화된 `ConversionSummary`로 남겨 결과 저장 흐름이 끊기지 않도록 보강했습니다.

### 설정/검증/문서
- 설정 JSON의 타입과 범위를 로드 시 정규화해 `retry_count="abc"`, `same_location="false"` 같은 값으로 앱 시작이 깨지지 않도록 했습니다.
- 실제 HWP COM 검증 보조 스크립트 `tools/hwp_com_smoke.py`를 추가했습니다.
- `.gitignore`에 COM 스모크 JSON, 결과 CSV/JSON, 실패 TXT 같은 로컬 검증 산출물을 추가했습니다.
- README, 구조 분석 문서, COM 수동 체크리스트, 협업 가이드를 현재 구현 기준으로 동기화했습니다.

## 2026-04-27 v8.7 안정성/검증 보강

### 런타임 및 배포 기준
- 공식 Python 지원 범위를 `3.10+`로 정리하고 `pyrightconfig.json`, README, 빌드 산출물 이름을 함께 갱신했습니다.
- PyInstaller 실행 파일 이름을 `HWP변환기_v8.7.exe`로 올렸습니다.
- 실제 한글 COM 수동 검증용 `HWP_COM_SMOKE_TEST_CHECKLIST.md`를 추가했습니다.

### 입력/백업/출력 안정성
- 폴더 스캔 시 하위 `backup/` 폴더를 기본 제외해 이전 백업 파일이 재변환되는 문제를 막았습니다. 사용자가 `backup` 폴더 자체를 직접 선택한 경우에는 해당 폴더의 파일을 스캔할 수 있습니다.
- 폴더 모드 입력이 실제 디렉터리인지 명시적으로 검증합니다.
- 출력 충돌 처리의 타임스탬프 폴백을 마이크로초 단위와 반복 확인 방식으로 보강했습니다.
- 출력 폴더 쓰기 권한 검사를 고정 파일명 대신 임시 파일 생성 방식으로 변경했습니다.
- 설정 저장은 임시 파일 작성 후 교체하는 방식으로 원자성을 높이고, 손상 JSON은 타임스탬프 백업명으로 보존합니다.

### 변환 결과 신뢰도
- `Open()` 또는 `SaveAs()`가 명시적으로 `False`를 반환하면 실패로 처리합니다.
- `SaveAs` 2인자/3인자 폴백 후에도 기본 출력 파일이 존재하고 0바이트가 아닐 때만 성공으로 집계합니다.
- 한글 COM 초기화 실패 시 전체 실행 대상이 실패로 집계된 결과 리포트를 생성합니다.
- 동일 형식 파일만 선택된 경우에도 변환 없이 `건너뜀` 결과 다이얼로그와 CSV/JSON 저장 흐름을 제공합니다.

### UI/결과 리포트
- 원본 백업 on/off 옵션과 파일별 실패 재시도 횟수(0~3회, 기본 1회) 옵션을 추가했습니다.
- 사전 점검 다이얼로그에 입력 존재/읽기 가능 여부, 출력 경로, 충돌 조정, 백업 설정, 한글 COM ProgID 탐지 결과를 표시합니다.
- 결과 CSV/JSON에 `retry_count`, `backup_file`, `backup_error` 필드를 추가했습니다.
- `same_location=True` 상태에서는 변환 완료 후에도 출력 폴더 입력/버튼이 비활성 상태를 유지합니다.

## 2026-03-18 구조 리팩토링

### 코드 분할
- 루트 엔트리포인트 `hwptopdf-hwpx_v4.py`를 얇은 래퍼로 전환하고 실제 구현을 `hwpmate/` 패키지로 분리했습니다.
- 설정, 경로 유틸, 변환 엔진, 워커, Windows 통합, UI 컴포넌트, 메인 윈도우를 전용 모듈로 분리했습니다.
- `hwptopdf-hwpx v3.py`는 `legacy/hwptopdf-hwpx v3.py`로 이동했습니다.

### 테스트 및 품질
- `tests/` 아래에 설정 저장소, 경로 유틸, 파일 선택 스토어, 태스크 플래너 테스트를 추가했습니다.
- `pyrightconfig.json`의 검사 대상을 `hwpmate/`와 `tests/`까지 확장했습니다.
- `pyinstaller hwp_converter.spec` 빌드 검증을 통과해 래퍼 엔트리포인트와 패키지 구조의 배포 정합성을 확인했습니다.

## 2026-03-18 안정화/UX 보강

### 변환 계획과 결과 집계
- 동일 형식 변환(`HWP->HWP`, `HWPX->HWPX`)을 실행 대상에서 제외하고 `건너뜀`으로 별도 집계하도록 변경했습니다.
- 변환 시작 전 사전 점검 다이얼로그를 추가해 실행 대상 수, 건너뜀 수, 출력 충돌 조정 수, 저장 위치 정책, 주요 경고를 확인할 수 있게 했습니다.
- 결과 다이얼로그를 `성공/실패/건너뜀/취소됨` 기준으로 재구성하고, 전체 결과를 `CSV`/`JSON`으로 저장할 수 있게 했습니다.

### 안정성
- 강제 종료 범위를 시스템 전체 한글 프로세스가 아니라 앱이 직접 띄운 한글 프로세스로 제한했습니다.
- 변환 취소 시 남은 작업을 `취소됨`으로 마킹하고 실패와 구분해 표시하도록 바꿨습니다.
- 앱 종료 중 워커가 남아 있으면 즉시 닫지 않고 취소/강제 종료/자동 닫기 흐름으로 정리되도록 보강했습니다.
- 설정 저장을 변환 시작 시점뿐 아니라 앱 종료 시점에도 현재 UI 상태 기준으로 반영하도록 수정했습니다.
- 백업 파일명을 마이크로초 기준으로 바꾸고 충돌 시 일련번호를 붙여 덮어쓰지 않도록 했습니다.

### UI/검증
- 폴더 모드의 네이티브 드롭은 폴더 1개만 허용하고, 파일 모드에서는 기존처럼 스캔 후 목록에 추가되도록 분기했습니다.
- 폴더 미리보기 스캔이 현재 선택 포맷 기준의 실제 변환 가능 파일 수를 보여주도록 조정했습니다.
- 도움말/소개 텍스트를 현재 지원 포맷과 결과 리포트 동작 기준으로 최신화했습니다.
- `hwp_converter.spec`를 다시 점검한 결과 추가 hidden import/data bundle 변경 없이 현재 빌드 구성을 유지할 수 있음을 확인했습니다.
- README, 구조 분석 문서, AI 협업 가이드를 현재 동작 기준으로 다시 동기화했습니다.
- 워커, 결과 리포트, 메인 윈도우 종료 흐름, 긴 경로 드롭 처리 테스트를 추가했습니다.

## 2026-03-15 리포지토리 유지보수

### 정적 분석 및 타입 안정화
- `pyright`/`Pylance` 기준 오류를 전수 정리했습니다.
- COM 자동화 객체와 Qt 이벤트 오버라이드에 타입 가드를 추가했습니다.
- `None` 가능성이 있는 UI API 접근에 명시적 검사를 넣었습니다.

### 인코딩 및 편집 기준 정리
- 리포지토리 전체를 UTF-8 기준으로 점검했고, 실제 파일 손상은 없음을 확인했습니다.
- `.editorconfig`를 추가해 `utf-8`, `LF`, 최종 개행 규칙을 고정했습니다.
- 문서와 설정 파일의 표현을 현재 코드 기준으로 정리했습니다.

### 개발 환경 보강
- `pyrightconfig.json`을 추가해 타입 검사 기준을 리포지토리에 고정했습니다.
- README, 히스토리 문서, AI 협업 가이드를 최신 구조에 맞춰 갱신했습니다.
- `.gitignore`에 변환 백업 산출물용 `backup/` 패턴을 추가했습니다.

## v8.6

### 기능 확장
- 문서 형식을 `PDF`, `HWP`, `HWPX`, `DOCX`, `ODT`, `HTML`, `RTF`, `TXT`까지 확장했습니다.
- 이미지 형식을 `PNG`, `JPG`, `BMP`, `GIF`까지 지원합니다.
- 변환 전 원본 백업 기능을 추가했습니다.

### UI/UX
- 문서/이미지 형식을 탭 기반으로 분리했습니다.
- 카드형 형식 선택 UI를 도입했습니다.
- 토스트 알림, 상태바, 시스템 트레이, 다크/라이트 테마를 정비했습니다.
- 예상 소요 시간, 변환 진행 상태, 폴더 미리보기 스캔을 보강했습니다.

### 안정성
- SaveAs 2-인자/3-인자 폴백을 유지해 한글 버전별 COM 차이를 흡수합니다.
- Windows 관리자 권한 환경에서도 드래그 앤 드롭이 가능하도록 `WM_DROPFILES` 기반 네이티브 필터를 사용합니다.
- 파일 경로 유효성, 출력 폴더 쓰기 권한, 중복 파일 관리를 강화했습니다.

## v8.5

- 대량 파일 추가 시 UI 멈춤을 줄이기 위해 비동기 스캔 흐름을 정리했습니다.
- 폴더 선택 시 HWP/HWPX 파일 수를 미리 확인하는 흐름을 추가했습니다.
- 실패한 파일 목록을 텍스트로 내보내는 기능을 추가했습니다.
- 출력 폴더 권한 검사와 경로 정규화 로직을 보강했습니다.

## v8.4.x

- 관리자 권한 환경에서 동작하는 네이티브 드래그 앤 드롭을 도입했습니다.
- 64비트 핸들 처리와 드롭 메시지 필터를 안정화했습니다.
- 테마, 상태바, 카드형 선택 UI 등 전반적인 코드 품질과 시각 완성도를 높였습니다.

## 이전 세대 참고

- `legacy/hwptopdf-hwpx v3.py`는 tkinter 기반 레거시 구현으로 남아 있습니다.
- 유지보수와 배포, 문서 기준은 `v4` 계열을 우선합니다.
