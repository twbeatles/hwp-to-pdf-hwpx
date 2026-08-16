# Update manifest

latest.json은 GitHub 릴리즈 워크플로우를 통해 자동으로 생성되며,
HWPMATE_UPDATE_PRIVATE_KEY_B64에 보관된 Ed25519 비공개키로 서명된 후 커밋됩니다.

매니페스트 생성은 python scripts/build_update_manifest.py --help를 참조하세요.
비공개키나 서명되지 않은 임의의 매니페스트를 수동으로 커밋하지 마세요.
