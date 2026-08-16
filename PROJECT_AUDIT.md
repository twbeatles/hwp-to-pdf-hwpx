# Project Audit

## 1. Executive Summary

HwpMate(HWP 변환기)는 한컴오피스 한글 COM 자동화(Automation)를 기반으로 HWP/HWPX 문서를 다양한 포맷(PDF, DOCX, 이미지 등)으로 일괄 변환하는 PyQt6 기반 데스크톱 애플리케이션입니다.

최근 진행된 **Ed25519 보안 서명 기반 GitHub Releases 자동 업데이트 시스템** 도입을 포함하여, 전체 코드베이스의 아키텍처, 안정성, 예외 처리, 동시성, COM 프로세스 수명 주기, OS 호환성을 종합적으로 감사했습니다.

### 📊 전체 평가 및 위험도 요약
* **전체 위험도**: **Low ~ Medium (안정적)**
* **핵심 강점**:
  - **견고한 COM 프로세스 제어**: _snapshot_hwp_pids()와 Toolhelp API를 통해 앱이 직접 생성한 한글 프로세스(owned_pids)만 선별 추적하여, 사용자의 기존 한글 창을 간섭하지 않고 안전하게 강제 종료/전면화/숨김을 수행합니다.
  - **강력한 무결성 검증 업데이트 시스템**: Ed25519 비대칭 암호화 서명, SHA-256 해시, 크기 검증, HTTPS 강제, 그리고 교체 후 --smoke 검증 실패 시 자동 롤백 복구 메커니즘이 구현되어 있습니다.
  - **SOLID 기반 모듈화**: MainWindow의 비대화를 방지하고 컨트롤러(conversion, ile_selection, lifecycle, ppearance, update)와 서비스 계층으로 분리되어 있습니다.
* **주요 개선 필요 영역**:
  - 대용량 업데이트 다운로드 시 취소 플래그 미반영 및 소켓 읽기 무한 대기 방지.
  - 대량(수백~수천 건) 일괄 변환 시 한글 COM 인스턴스 장기 유지로 인한 잠재적 메모리/핸들 누수 완화(배치 리셋).
  - README.md의 릴리즈 아티팩트 명명 규칙 일치화 및 신규 업데이트 기능 설명 보강.

---

## 2. Project Understanding

### 2.1 아키텍처 및 데이터 흐름

`mermaid
flowchart TD
    subgraph UI_Layer["UI 계층 (PyQt6)"]
        MW["MainWindow (Composition Root)"]
        Builder["main_window_ui.builder"]
        MW --> Builder
        MW --> Controllers["Controllers (Conversion, FileSelection, Lifecycle, Appearance, Update)"]
        MW --> Toast["ToastManager (알림 토스트)"]
    end

    subgraph Service_Layer["서비스 & 도메인 계층"]
        TaskPlanner["TaskPlanner (작업 계획 및 충돌 해결)"]
        FileStore["FileSelectionStore (선택 파일 관리)"]
        SecModule["HwpSecurityModule & Session (보안 DLL 등록 & 팝업 억제)"]
        UpManifest["update_manifest (Ed25519 서명 & 메타데이터 검증)"]
        UpInstaller["update_installer (스트리밍 다운로드 & 원자적 교체 & 롤백)"]
    end

    subgraph Worker_Layer["백그라운드 워커 계층 (QThread)"]
        ConvWorker["ConversionWorker (COM STA 스레드 격리)"]
        ScanWorker["FileScanWorker (비동기 디렉터리 탐색)"]
        UpWorker["UpdateCheck & Download Worker"]
    end

    subgraph COM_Engine["외부 연동 (Windows COM)"]
        HWP_COM["한컴오피스 한글 (HwpObject / HwpCtrl)"]
    end

    Controllers --> TaskPlanner
    Controllers --> ConvWorker
    Controllers --> UpWorker
    ConvWorker --> HWP_COM
    UpWorker --> UpManifest
    UpWorker --> UpInstaller
`

### 2.2 핵심 실행 흐름
1. **부트스트랩 (hwpmate/app.py, ootstrap.py)**:
   - --smoke 인자 확인 시 GUI 없이 의존성 및 무결성을 검증하고 JSON을 출력하여 즉시 종료.
   - --apply-update 인자 확인 시 업데이트 헬퍼 모드로 부모 PID 종료 대기 후 바이너리 원자적 교체 실행.
   - 일반 실행 시 단일 인스턴스 뮤텍스(SingleInstanceLock) 및 관리자 권한(is_admin()) 검증 후 MainWindow 기동.
2. **시작 후 생명주기**:
   - showEvent에서 이전 업데이트 결과(last-update-result.json)를 소비하여 성공/롤백 토스트 알림.
   - 3초 후 백그라운드 스레드로 GitHub Releases 매니페스트(updates/latest.json) 확인.
3. **변환 파이프라인**:
   - 사용자 파일/폴더 선택 ➔ TaskPlanner가 대상 목록 및 출력 경로 충돌 조정 ➔ PreflightDialog로 사전 검증 ➔ ConversionWorker가 별도 COM STA 스레드에서 한글 COM 인스턴스를 제어하며 순차 변환.

---

## 3. High-Risk Issues

### [Issue 1] 업데이트 다운로드 스레드에서 사용자 취소 미반영 및 읽기 타임아웃 처리
* **위치**: hwpmate/ui/main_window_controllers/update.py (UpdateDownloadWorker.run), hwpmate/services/update_installer.py (stream_update_artifact)
* **문제**:
  1. UpdateDownloadWorker에 self._is_cancelled = False가 정의되어 있으나, un() 메서드의 다운로드 청크 반복 루프에서 이 플래그를 확인하거나 취소하는 인터럽트 로직이 누락되어 있습니다. 사용자가 대화상자를 닫아도 백그라운드에서 다운로드가 끝까지 진행됩니다.
  2. stream_update_artifact에서 urlopen(request, timeout=...)의 타임아웃은 최초 연결/첫 바이트에 적용되며, 루프 내부의 esponse.read(1024 * 1024) 도중 네트워크 단절 시 소켓 읽기가 장시간 블로킹될 수 있습니다.
* **영향**:
  - 사용자가 업데이트 대화상자를 닫거나 취소해도 백그라운드 네트워크 트래픽과 디스크 I/O가 지속됨.
  - 네트워크 단절 시 다운로드 스레드가 멈추어 UI가 락업되거나 상태가 갱신되지 않음.
* **근거**:
  - UpdateDownloadWorker의 self._is_cancelled 변수가 선언만 되고 prepare_staged_update 호출 시 전달되지 않음.
* **권장 수정 방향**:
  - prepare_staged_update 및 stream_update_artifact에 cancel_check: Callable[[], bool] 콜백 인자를 추가하여 청크 읽기 루프마다 취소 여부를 검사하고 즉시 중단하도록 개선.
* **우선순위**: **Medium**

---

### [Issue 2] 대량 파일 변환 시 한글 COM 프로세스 장기 유지로 인한 자원 누수 가능성
* **위치**: hwpmate/workers/conversion_worker/worker.py (ConversionWorker.run)
* **문제**:
  - 단일 변환 작업에서 수백~수천 개의 파일을 연속 변환할 때, 단 하나의 한글 COM 인스턴스(HwpObject)를 계속 재사용합니다.
  - 한글 프로그램의 COM 엔진 특성상, 대량의 문서 열기/닫기(Open/Clear)가 반복되면 프로세스 내부 GDI 핸들 또는 메모리가 점진적으로 증가하여 일정 건수 이후 크래시나 속도 저하가 발생할 수 있습니다.
* **영향**:
  - 1,000건 이상의 대규모 배치 변환 작업 후반부에 한글 COM 오류 또는 응답 없음 발생 위험.
* **근거**:
  - ConversionWorker.run()의 or idx, task in enumerate(self.tasks): 루프에서 한글 COM 프로세스를 재생성하거나 재시작하는 배치 주기(예: 매 200~300건마다 재시작)가 없음.
* **권장 수정 방향**:
  - (추정/개선) N건(예: 200건)마다 converter.cleanup() 후 converter.initialize()를 호출하는 자동 재순환(Recycle) 옵션을 추가하거나 예외 발생 시 인스턴스 재초기화 로직 보강.
* **우선순위**: **Medium**

---

### [Issue 3] 릴리즈 워크플로우 아티팩트 명명과 README.md 상의 파일명 불일치
* **위치**: .github/workflows/release.yml, hwp_converter.spec, README.md
* **문제**:
  - hwp_converter.spec에서는 출력 이름을 
ame='HWP변환기_v9.0'으로 생성합니다.
  - .github/workflows/release.yml에서는 $artifact = Join-Path C:\twbeatles-repos\HwpMate "dist/HwpMate-v.exe"로 이름을 변경하여 GitHub Release에 업로드합니다.
  - README.md 가이드에는 HWP변환기_v9.0.exe로 표기되어 있어 사용자가 다운로드한 파일명(HwpMate-v9.0.exe)과 불일치가 발생합니다.
* **영향**:
  - 사용자가 다운로드한 실행 파일명과 안내 문서의 파일명이 달라 혼선 발생 가능.
* **근거**:
  - elease.yml 57행: dist/HwpMate-v.exe
  - README.md 62행: HWP변환기_v9.0.exe
* **권장 수정 방향**:
  - 프로젝트 공식 배포 파일명을 HwpMate-v{version}.exe로 통일하고 README.md의 예시 파일명을 수정.
* **우선순위**: **Low**

---

## 4. Potential Functional Gaps

> 아래 항목은 기능의 완성도와 사용자 편의성을 높이기 위한 보완 지점이며, 확실하지 않은 내용은 **[추정]**으로 명시합니다.

1. **[추정] 업데이트 확인 실패 시 상세 재시도 정책**:
   - 현재 오프라인 상태이거나 방화벽 환경에서 앱 기동 시 조용히 1회 확인하고 종료됩니다. 수동 확인 버튼을 누르지 않는 한 다음 실행 시점까지 재확인이 이루어지지 않습니다. (주기적 폴링 타이머 또는 네트워크 복구 감지 기능 없음).
2. **[추정] CLI 일괄 변환 모드 부재**:
   - 현재 GUI 실행 및 --smoke, --apply-update CLI는 지원하지만, 터미널 환경에서 스크립트나 배치 파일로 직접 변환을 수행하는 CLI 인자(예: HwpMate.exe --input "C:\docs" --format PDF --output "C:\out")는 제공되지 않습니다.
3. **[추정] 한글 암호화 문서 감지 및 알림**:
   - 암호가 걸려 있는 HWP 문서의 경우 orceopen:true 옵션으로도 열리지 않고 대기 상태에 빠질 수 있습니다. 사전 점검(Preflight) 또는 변환 시 암호 걸린 문서를 감지하여 명확한 오류 메시지("문서 암호 설정됨")를 남기는 처리가 보강되면 유용합니다.
4. **[추정] 릴리즈 노트 표시 UI의 서치/마크다운 렌더링**:
   - 현재 업데이트 대화상자에서 릴리즈 노트 클릭 시 기본 웹 브라우저를 통해 GitHub Release 페이지로 이동합니다. 대화상자 내에 텍스트 요약을 직접 표시하는 인라인 뷰어가 있으면 더욱 편리합니다.

---

## 5. Recommended Fix Plan

### 1단계: 즉시 개선 (Stability & Polish)
1. **업데이트 다운로드 취소 및 스트리밍 안전성 보강**:
   - UpdateDownloadWorker 및 prepare_staged_update에 cancel_check 콜백을 연동하여 대화상자 취소 시 즉시 다운로드 루프를 탈출하고 임시 파일을 정리하도록 개선.
2. **문서 및 파일명 정합성 일치화**:
   - README.md에 HwpMate-v{version}.exe 명칭 반영 및 신규 자동 업데이트 기능 안내 섹션 추가.

### 2단계: 대규모 변환 안정성 개선 (Scalability)
1. **대량 변환 COM 인스턴스 자동 재순환**:
   - 연속 변환 200건 초과 시 한글 COM 인스턴스를 재초기화하는 안전 가드 옵션 추가.
2. **암호 걸린 문서 타임아웃 가드 보강**:
   - Open 시도 후 응답이 없을 때 취소 핸들러와 연동하여 신속히 실패로 전환.

### 3단계: 구조 및 편의 기능 개선 (Feature Enhancements)
1. **헤드리스 CLI 변환 인자 지원**:
   - --input, --format, --output 플래그를 통한 자동화 스크립트 연동 지원.
2. **인라인 릴리즈 노트 뷰어**:
   - 업데이트 대화상자 내에 릴리즈 요약 마크다운 렌더링 추가.

---

## 6. Test Recommendations

현재 168개의 단위/통합 테스트가 통과하고 있으나, 다음 시나리오에 대한 테스트 보강을 권장합니다:

1. **UpdateDownloadWorker 취소 테스트**:
   - 대용량 파일 다운로드 중 취소 시그널/플래그가 설정되었을 때 스테이징 파일이 즉시 삭제되고 download_failed 또는 정상 중단되는지 검증.
2. **네트워크 단절 및 만료된 URL 에러 핸들링 테스트**:
   - 404, 500, 타임아웃, DNS 오류 등 다양한 HTTP 예외 상황에서 UpdateController가 충돌 없이 안전하게 오류 토스트를 띄우는지 검증.
3. **TaskPlanner 대규모 경로(1,000건 이상) 충돌 해결 성능 테스트**:
   - 동일 폴더 내 중복 파일명이 다수 존재할 때 esolve_output_conflicts의 처리 속도 및 메모리 사용량 벤치마크.
4. **한글 COM 비정상 종료 시 복구 테스트**:
   - 변환 도중 한글 프로세스가 예기치 않게 종료(Crash)되었을 때 워커가 무한 대기하지 않고 다음 재시도 또는 실패로 안전하게 넘어가는지 Mock 기반 검증.
