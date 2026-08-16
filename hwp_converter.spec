# -*- mode: python ; coding: utf-8 -*-
"""
HWP 변환기 v9.0 - PyInstaller 빌드 설정
루트 래퍼 엔트리포인트(hwptopdf-hwpx_v4.py) 기준 경량화 빌드 설정
실제 애플리케이션 로직은 hwpmate/ 패키지에서 정적으로 import 됩니다.
2026-03-18 안정화/UX 보강(사전 점검, 결과 리포트, 안전한 강제 종료) 이후에도
2026-05-12 산출물 감사/설정 복구/COM 스모크 보조 스크립트 보강 이후에도
2026-06-10 MainWindow 컨트롤러 분리 이후에도
2026-06-11 감사 개선(단일 인스턴스, artifact policy, busy guard) 이후에도
추가 hidden import 또는 data 번들 없이 동일 빌드 구성이 동작함을 확인했습니다.
2026-08-03: 한컴 보안승인 모듈 DLL 을 datas 로 번들 (배포 단일 exe 필수).
2026-08-03: hwp_security_session·계획 잠금·캐시 정책 반영 — security 모듈 hiddenimport 유지.
2026-08-04: hwp_print_settings(PDF 인쇄 리셋·PrintToPDFEx) 정적 import — 추가 datas 불필요.
2026-08-05: SOLID 패키지 분할 (hwp_converter/ · hwp_print_settings/ · windows_integration/
  · ui/dialogs|theme|main_window_ui/ · controllers 패키지 · conversion_worker/).
  공개 경로는 패키지 __init__ re-export. 정적 import 유지 — 핵심 패키지를 hiddenimports 에 명시.
2026-08-05: 감사 잔여 개선 — 소유 PID 한정 UI 억제, PDF 매직·확장 경로, 변환용 폴더 캐시 연령,
  backup_max_files_per_stem UI/설정. 신규 datas 없음. hiddenimports 패키지 목록 유지.
"""

from pathlib import Path

block_cipher = None

_SPEC_DIR = Path(SPECPATH) if "SPECPATH" in dir() else Path(".").resolve()
_SECURITY_DLL = _SPEC_DIR / "hwpmate" / "resources" / "security" / "FilePathCheckerModuleExample.dll"
_SECURITY_README = _SPEC_DIR / "hwpmate" / "resources" / "security" / "README.md"
if not _SECURITY_DLL.is_file():
    raise SystemExit(
        f"배포 빌드 필수 파일 없음: {_SECURITY_DLL}\n"
        "한컴 오토메이션 보안모듈 DLL 을 hwpmate/resources/security/ 에 두세요."
    )

_SECURITY_DATAS = [
    (str(_SECURITY_DLL), "hwpmate/resources/security"),
]
if _SECURITY_README.is_file():
    _SECURITY_DATAS.append((str(_SECURITY_README), "hwpmate/resources/security"))


# 제외할 불필요한 모듈 목록 (경량화)
# 주의: ssl / asyncio / concurrent 는 stdlib·Qt·로깅 경로에서 간접 사용될 수 있어 제외하지 않음
# 주의: PyQt6.QtNetwork 파이썬 래퍼는 제외해도 되지만 Qt6Network.dll 바이너리는 반드시 유지
EXCLUDES = [
    # 테스트/디버깅
    'pytest', 'unittest', 'test', 'tests',
    
    # 사용하지 않는 PyQt6 모듈 (파이썬 바인딩만 제외 — 네이티브 DLL 필터와 별개)
    'PyQt6.QtWebEngine', 'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtMultimedia', 'PyQt6.QtMultimediaWidgets',
    'PyQt6.QtBluetooth', 'PyQt6.QtNfc',
    'PyQt6.QtQuick', 'PyQt6.QtQuick3D', 'PyQt6.QtQml',
    'PyQt6.QtSql',
    'PyQt6.QtOpenGL', 'PyQt6.QtOpenGLWidgets',
    'PyQt6.QtSvg', 'PyQt6.QtSvgWidgets',
    'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets',
    'PyQt6.QtDesigner', 'PyQt6.QtHelp',
    'PyQt6.QtRemoteObjects', 'PyQt6.QtSensors',
    'PyQt6.QtSerialPort', 'PyQt6.QtPositioning',
    'PyQt6.QtTextToSpeech', 'PyQt6.Qt3DCore',
    'PyQt6.Qt3DInput', 'PyQt6.Qt3DLogic',
    'PyQt6.Qt3DRender', 'PyQt6.Qt3DExtras',
    'PyQt6.QtCharts', 'PyQt6.QtStateMachine',
    'PyQt6.QtWebSockets', 'PyQt6.QtSerialBus',
    'PyQt6.QtSpatialAudio',
    
    # 사용하지 않는 기타 모듈
    'PIL', 'numpy', 'pandas', 'matplotlib',
    'scipy', 'sklearn', 'tensorflow', 'torch',
    'tkinter', 'tk', 'tcl',
    'IPython', 'jupyter',
    'lib2to3', 'distutils',
]

a = Analysis(
    ['hwptopdf-hwpx_v4.py'],
    pathex=[],
    binaries=[],
    datas=_SECURITY_DATAS,  # 보안 DLL — onefile 에 포함, 런타임에 LOCALAPPDATA 로 복사
    hiddenimports=[
        # 필수 pywin32 모듈
        # 컨트롤러·artifact_policy·보안 세션은 정적 import 경로
        'win32com.client',
        'win32api',
        'pythoncom',
        'pywintypes',
        # 암호화 및 자동 업데이트 모듈
        'cryptography',
        'hwpmate.services.update_manifest',
        'hwpmate.services.update_installer',
        # 보안 모듈: 변환 경로에서 동적 성격이 있어 onefile 에서 명시 포함
        'hwpmate.services.hwp_security_module',
        'hwpmate.services.hwp_security_session',
        # 2026-08-05 패키지 분할: onefile 수집 누락 방지용 핵심 패키지
        'hwpmate.services.hwp_converter',
        'hwpmate.services.hwp_print_settings',
        'hwpmate.windows_integration',
        'hwpmate.workers.conversion_worker',
        'hwpmate.ui.dialogs',
        'hwpmate.ui.theme',
        'hwpmate.ui.main_window_ui',
        'hwpmate.ui.main_window_controllers.conversion',
        'hwpmate.ui.main_window_controllers.file_selection',
        'hwpmate.ui.main_window_controllers.lifecycle',
        'hwpmate.ui.main_window_controllers.update',
    ],

    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 불필요한 바이너리 제거 (경량화)
# 중요: Qt6Network / opengl32sw / d3dcompiler 는 Qt6Gui·렌더링 런타임 의존성이라 제거하면
# onefile 실행 직후 QtCore.pyd 에서 0xC0000005 접근 위반으로 즉시 종료됨.
# (Analysis에는 수집되지만 과거 필터가 EXE/PKG 단계에서 탈락시켰음)
a.binaries = [
    b for b in a.binaries
    if not any(x in b[0].lower() for x in [
        'qt6webengine', 'qt6multimedia', 'qt6quick',
        'qt6qml', 'qt6sql',
        # qt6network / qt6opengl / qt6svg / opengl32sw / d3dcompiler 유지
        'qt6designer',
        'qt6charts', 'qt6statemachine', 'qt6websockets',
        'qt6serialbus', 'qt6spatialaudio',
    ])
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='HWP변환기_v9.1.0',

    debug=False,
    bootloader_ignore_signals=False,
    strip=False,  # Windows에서는 strip 효과 제한적
    upx=False,  # UPX는 Qt DLL 손상/미설치 환경 크래시 위험이 있어 비활성
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI 모드 (콘솔 창 숨김)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # 아이콘이 있으면 경로 지정: 'icon.ico'
    uac_admin=True,  # 관리자 권한 요청
)
