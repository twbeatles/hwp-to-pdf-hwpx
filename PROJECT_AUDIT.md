# Project Audit

감사일: 2026-08-10
범위: 기능 구현, 오류 처리, 상태·비동기 흐름, 경로/설정/보안, 테스트 및 문서 정합성

## 1. Executive Summary

전체 위험도는 **Medium**이다. 정적 검증은 양호하다(`python -m pytest -q`: **143 passed**, `python -m pyright .`: **0 errors / 0 warnings**). 또한 단일 인스턴스 잠금, COM 워커 분리, 출력 산출물 검증, 설정 원자 저장, DLL SHA-256 검증 등 주요 안정성 장치가 구현되어 있다.

다만 "덮어쓰기 끔"의 출력 충돌 방어가 사전 점검 단계에만 있어, 점검과 실제 COM 저장 사이에 외부 프로세스가 파일을 만들면 기존 파일을 덮어쓸 수 있다. 실패한 비-PDF 내보내기의 부분 산출물도 정리하지 않는다. 또한 COM 호출 자체가 멈추는 경우, 앱이 띄우지 않은 한글 프로세스에는 강제 종료 경로가 없어서 취소가 무기한 대기가 될 수 있다. 폴더 스캔 캐시도 짧은 유효기간과 표본 검사만 사용하므로, 하위 폴더의 직전 변경을 놓칠 수 있다.

## 2. Project Understanding

README와 CLAUDE.md 기준으로 HwpMate는 Windows 10/11에서 한글(HWP/HWPX) 문서를 PDF, 문서, 이미지 형식으로 일괄 변환하는 PyQt6 GUI 앱이다. 배포 진입점은 `hwptopdf-hwpx_v4.py`이고, 이는 `hwpmate.bootstrap.main`을 거쳐 `hwpmate.app.main`을 호출한다. 실행 전 pywin32와 관리자 권한을 확인하고, `SingleInstanceLock`으로 중복 실행을 막은 뒤 `MainWindow`를 연다.

CodeGraph 호출 관계 분석 결과, 핵심 실행 흐름은 다음과 같다.

```text
MainWindow UI
  -> ConversionController.start_conversion
  -> folder scan cache 확인 / TaskPlanner.build_tasks
  -> TaskPlanner.resolve_output_conflicts
  -> PreflightDialog 승인
  -> ConversionWorker(QThread).run
  -> HWPConverter.initialize / convert_file
  -> ConversionSummary signal
  -> ResultDialog 및 상태 초기화
```

`TaskPlanner`는 동일 확장자 입력을 건너뛰고 출력 경로·보조 산출물 충돌을 계산한다. `ConversionWorker`는 별도 QThread에서 COM apartment를 초기화하고, 파일별 백업·디렉터리 생성·재시도·취소 상태·요약 스냅샷을 처리한다. `HWPConverter`는 SaveAs/PrintToPDFEx 폴백, PDF 매직 검증, 보안 모듈 설치 및 소유 PID 기반 강제 종료를 제공한다.

CodeGraph상 영향 범위가 특히 큰 지점은 `ConversionWorker`(15 callers), `PlannedConversion`(25 callers), `TaskPlanner.build_tasks`/`resolve_output_conflicts`, `ConversionController.start_conversion`이다. 이들은 수정 시 UI, 결과 대화상자 및 단위 테스트까지 함께 회귀 검증해야 한다.

README.md와 CLAUDE.md 파일 자체는 UTF-8 바이트로 저장되어 있다. 콘솔에서 한글이 깨져 보일 수 있으나, 이는 현재 PowerShell 출력 코드페이지의 표현 문제이며 파일 인코딩 손상 근거는 아니다.

## 3. High-Risk Issues

### A-01. 출력 충돌 검사가 저장 직전 재검증되지 않아 덮어쓰기 금지 정책이 우회될 수 있음

* 위치: `hwpmate/services/task_planner.py:187` `TaskPlanner.resolve_output_conflicts`, `hwpmate/ui/main_window_controllers/conversion/controller.py:409-445`, `hwpmate/workers/conversion_worker/worker.py:125-152`
* 문제: `overwrite=False`일 때 기존 산출물 충돌은 계획/사전점검 시 한 번만 검사한다. 워커는 실제 저장 직전에 대상 또는 보조 산출물의 존재를 다시 검사하지 않고 `converter.convert_file()`을 호출한다.
* 영향: 사전점검 승인 후 다른 프로세스·사용자·동기화 프로그램이 같은 출력 파일을 생성하면, 한글 COM의 SaveAs 동작에 따라 사용자가 "덮어쓰기"를 선택하지 않았어도 외부 산출물을 덮어쓸 수 있다. 이미지/HTML 보조 산출물도 같은 경쟁 조건에 놓인다.
* 근거: 충돌 판단은 `resolve_output_conflicts()`의 `existing_conflict`에서만 수행된다. 워커의 파일별 처리에는 출력 폴더 생성 뒤 입력 파일 존재 확인과 `convert_file()` 호출만 있으며 충돌 재확인이 없다.
* 권장 수정 방향: 워커에서 각 작업의 저장 직전에 원자적으로 가능한 예약/재검증을 수행한다. 충돌 발견 시 새 이름을 재계산하거나 해당 작업을 실패/건너뜀 처리한다. 보조 산출물 포맷은 `existing_artifact_conflicts()`를 같은 정책으로 재사용하고, 재조정 결과를 요약/CSV/JSON에 기록한다.
* 우선순위: High

### A-02. 실패한 비-PDF 내보내기가 부분 산출물을 남길 수 있음

* 위치: `hwpmate/services/hwp_converter/converter.py:428-475` `HWPConverter.convert_file`
* 문제: SaveAs가 실패를 반환하거나, 산출물 스냅샷 검증이 실패한 경우 PDF 경로만 `remove_incomplete_output()`으로 불완전 파일을 정리한다. DOCX/HWPX/이미지/HTML 경로는 `Clear()` 후 실패를 반환하지만, 이번 호출이 만들거나 변경한 파일·보조 산출물을 되돌리지 않는다.
* 영향: UI와 결과 보고서에는 실패로 표시되지만 출력 폴더에는 열리지 않거나 불완전한 파일/보조 디렉터리가 남을 수 있다. 이후 실행에서 충돌로 간주되어 이름이 바뀌거나 사용자가 잘못된 결과물을 사용할 수 있다.
* 근거: `_artifact_ok()` 실패 후 PDF에만 폴백 및 불완전 PDF 제거가 있으며, 비-PDF는 475행 부근에서 실패 반환한다. 일반 실패 분기(428-435)도 출력 정리를 수행하지 않는다.
* 권장 수정 방향: 변환 전 산출물 스냅샷을 바탕으로, 이번 시도에서 새로 생성된 파일과 보조 산출물을 best-effort로 제거한다. 기존 파일을 덮어쓴 경우에는 무조건 삭제하지 말고 사전 백업/임시 경로 저장 후 원복 가능한 트랜잭션 정책을 둔다.
* 우선순위: High

### A-03. COM 호출이 무응답이면 취소가 완료되지 않을 수 있음

* 위치: `hwpmate/workers/conversion_worker/worker.py:147-162`, `hwpmate/ui/main_window_controllers/conversion/controller.py:482-555`
* 문제: 취소 플래그는 `convert_file()` 호출 전후와 재시도 대기에서만 확인된다. `Open`, `SaveAs`, `PrintToPDFEx` 같은 동기 COM 호출 중에는 인터럽트할 수 없다. 제한 시간 후 강제 종료는 앱이 새로 띄운 것으로 추적한 PID가 있을 때만 가능하다.
* 영향: 이미 실행 중이던 한글 인스턴스에 연결했거나 PID 스냅샷을 만들지 못한 상태에서 COM이 모달 창/무응답으로 정지하면, 사용자는 취소 후에도 워커 종료를 기다려야 한다. 창 닫기와 후속 작업도 지연될 수 있다.
* 근거: `request_worker_stop()`은 `worker.wait()`를 반복한 뒤 `worker.can_force_terminate()`가 false면 강제 종료하지 않고 대기 상태를 유지한다. CLAUDE.md도 이 제약을 명시하고 있으므로 문서와 구현은 일치하지만, 가용성 위험은 남는다.
* 권장 수정 방향: COM 호출별 진행 단계·경과 시간·사용자 안내를 더 명확히 기록하고, 별도의 watchdog/복구 절차를 설계한다. 안전한 소유성 확인을 전제로만 강제 종료를 제공한다는 현재 보안 원칙은 유지한다.
* 우선순위: Medium

### A-04. 폴더 변환의 캐시 신선도 검사가 하위 변경을 완전하게 보장하지 않음

* 위치: `hwpmate/ui/main_window_controllers/file_selection/controller.py:55-103`, `326` 이후 `validate_folder_scan_cache_freshness`, `hwpmate/ui/main_window_controllers/conversion/controller.py:164-212`
* 문제: 폴더 모드는 UI 스레드 재스캔을 피하기 위해 최대 90초 캐시와 루트 폴더 mtime, 일부 파일 존재 표본으로 입력 목록을 확정한다. 하위 폴더의 새 파일 추가는 루트 mtime에 반영되지 않을 수 있고, 표본에 걸리지 않으면 캐시가 유효하다고 판단한다.
* 영향: 사용자가 "변환 시작" 직전에 하위 폴더에 넣은 HWP/HWPX가 이번 일괄 변환에서 누락될 수 있다. 특히 대규모 트리와 동기화 폴더에서 재현 가능성이 높다.
* 근거: 코드 주석도 디렉터리 mtime과 표본 파일 존재 검사가 best-effort임을 명시한다. `collect_tasks()`는 캐시가 유효하면 직접 파일 순회를 하지 않는다.
* 권장 수정 방향: 변환 시점의 재스캔 옵션(정확성 우선)을 제공하거나, 스캔 결과에 디렉터리별 mtime/파일 수 서명을 저장해 변경된 하위 트리만 증분 재스캔한다. 기본 동작을 유지할 경우 UI에 캐시 기준 시각과 "최신화 후 변환"을 명확히 표시한다.
* 우선순위: Medium

### A-05. README의 로그 기본 위치 설명이 실제 우선순위와 다름

* 위치: `README.md` 설정/로그 표, `hwpmate/logging_config.py:13-20`
* 문제: README는 로그 위치를 `%LOCALAPPDATA%\\HwpMate\\logs` 또는 `~/.hwp_converter/logs`로 설명한다. 실제 `_log_dir_candidates()`는 홈 디렉터리의 `~/.hwp_converter/logs`를 먼저 선택하고, 그것이 불가능할 때만 `%LOCALAPPDATA%`를 선택한다.
* 영향: Windows 사용자가 안내된 위치에서 로그를 찾지 못해 장애 보고와 현장 진단이 지연될 수 있다.
* 근거: 코드의 후보 순서가 문서의 기본 위치 표현과 반대다.
* 권장 수정 방향: README에 실제 우선순위와 임시 폴더 폴백을 명시하거나, Windows 우선 정책이 의도라면 코드 후보 순서를 변경한다.
* 우선순위: Low

## 4. Potential Functional Gaps

* **추정:** 실제 한글 COM을 사용하는 자동화 회귀 테스트가 없다. 현재 `tests/test_hwp_converter.py`는 모의 COM 객체 중심이고, 프로젝트도 별도 수동 `tools/hwp_com_smoke.py` 및 체크리스트를 제공한다. 한글 버전·프린터·보안 모듈·UAC 조합 차이는 CI에서 검증되지 않는다.
* **추정:** 네트워크 드라이브, OneDrive/동기화 폴더, 긴 UNC 경로에서의 출력 충돌·원자 저장·백업 보존을 종단 간 검증할 시나리오가 부족하다. 경로 후보와 긴 경로 경고는 구현되어 있으나 실제 HWP COM 호환성은 환경 의존적이다.
* **추정:** 실패 후 남은 다중 이미지/HTML 보조 산출물을 결과 화면에서 정리하거나 사용자가 열어 볼 수 없도록 식별하는 기능이 필요할 수 있다.
* **추정:** 출력 파일을 외부 변경으로부터 완전히 보호하려면 "새 이름으로 저장"뿐 아니라 작업별 임시 출력 위치와 완료 후 이동하는 정책이 필요할 수 있다. 이는 A-01/A-02 해결 방식에 따라 결정해야 한다.

## 5. Recommended Fix Plan

### 1단계: 즉시 수정

1. 워커의 실제 저장 직전에 `overwrite=False` 충돌을 재검사하고, 충돌 시 새 이름 재계산 또는 안전한 실패 처리를 추가한다.
2. 내보내기 실패·산출물 검증 실패 시 이번 시도가 만든 비-PDF 주/보조 산출물을 정리한다. 기존 파일 덮어쓰기 경로는 임시 파일/원복 정책을 먼저 정한다.
3. 위 두 경로의 결과를 `ConversionTask`와 CSV/JSON 감사 필드에 명확히 남긴다.

### 2단계: 안정성 개선

1. 폴더 변환에서 "최신 스캔 후 실행" 선택지 또는 변경 하위 트리 증분 재스캔을 추가한다.
2. COM 대기 상태에 단계별 timeout telemetry, 상태 표시, 수동 복구 안내를 추가한다.
3. README의 로그 위치 우선순위와 실제 폴백 위치를 동기화한다.

### 3단계: 구조 개선

1. 출력 정책을 `TaskPlanner`와 `ConversionWorker`가 공유하는 단일 예약/커밋 서비스로 분리해 TOCTOU 판단을 한곳에서 관리한다.
2. 다중 산출물 형식을 위한 임시 디렉터리 생성 → 검증 → 원자적 publish/rollback 흐름을 도입한다.
3. 실제 한글이 설치된 Windows 테스트 환경에서 smoke test를 정기 실행하는 배포 검증 단계를 마련한다.

## 6. Test Recommendations

1. `overwrite=False` 계획 완료 뒤 대상 파일 또는 PNG/HTML 보조 산출물을 생성하고, 워커가 저장 직전 재검사하여 덮어쓰지 않는지 테스트한다.
2. 모의 COM이 SaveAs 후 빈 DOCX, 부분 PNG, `.files` 보조 디렉터리를 만든 뒤 실패하도록 하여, 새 산출물이 정리되고 기존 산출물은 보존되는지 테스트한다.
3. 폴더 미리보기 완료 후 하위 폴더에 파일을 추가·삭제·교체하는 테스트를 추가해 캐시 정책(재스캔 요구 또는 증분 반영)을 명시적으로 고정한다.
4. `Open` 또는 `SaveAs`가 장시간 반환하지 않는 모의 COM으로 취소 UI 상태, timeout 안내, 소유 PID 없음/있음 분기를 검증한다.
5. Windows 통합 테스트에서 UNC, 240자 경고 경계, 260자 차단 경계, OneDrive 또는 잠긴 출력 파일을 포함한다.
6. 실제 한글 설치 Windows runner에서 PDF·DOCX·PNG·HTML 각각 1건 이상을 변환하고, PDF 매직, 주/보조 산출물, 백업, CSV/JSON 감사 필드를 확인하는 nightly smoke test를 실행한다.
