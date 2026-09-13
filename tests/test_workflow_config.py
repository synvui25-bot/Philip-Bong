from pathlib import Path
import re
import subprocess
import sys


WORKFLOW_PATH = Path(".github/workflows/telegram-listings.yml")


def test_sync_workflow_is_scheduled_manual_and_secret_scoped():
    """A missing trigger, unsafe scope, or unpinned action must block publishing."""
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "cron: '*/15 * * * *'" in workflow
    assert "workflow_dispatch:" in workflow
    assert "bootstrap:" in workflow
    assert "type: boolean" in workflow
    assert "python -m scripts.sync_telegram ${{ inputs.bootstrap && '--bootstrap' || '' }}" in workflow
    assert "\n  push:" not in workflow
    assert "permissions:\n  contents: write" in workflow
    assert "concurrency:\n  group: telegram-listing-sync\n  cancel-in-progress: false" in workflow
    assert re.search(r"actions/checkout@[0-9a-f]{40}\s+# v4", workflow)
    assert re.search(r"actions/setup-python@[0-9a-f]{40}\s+# v5", workflow)
    assert "pip install --require-hashes -r requirements.txt" in workflow
    assert "TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}" in workflow
    assert "mkdir -p assets/telegram" in workflow
    assert "git add -- data assets/telegram" in workflow
    assert "git add ." not in workflow


def test_token_is_absent_from_publishable_files():
    for path in [Path("index.html"), *Path("data").glob("*.json")]:
        assert "TELEGRAM_BOT_TOKEN" not in path.read_text(encoding="utf-8")


def test_requirements_pin_pillow_with_hashes():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert "pillow==12.3.0 \\" in requirements.lower()
    assert "--hash=sha256:" in requirements


def test_module_entry_point_supports_help():
    """The workflow entry point must resolve package imports outside scripts/."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.sync_telegram", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Synchronize Telegram channel listings" in result.stdout


def test_media_staging_handles_a_missing_directory_and_stages_last_deletion(tmp_path):
    """Recreating media before git add must retain the deletion of its final asset."""
    repository = tmp_path / "repository"
    media = repository / "assets" / "telegram"
    media.mkdir(parents=True)
    (repository / "data").mkdir()
    (repository / "data" / "telegram-listings.json").write_text("[]\n", encoding="utf-8")
    image = media / "1-0.webp"
    image.write_bytes(b"image")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repository, capture_output=True, text=True, check=True
        )

    git("init")
    git("config", "user.name", "workflow-test")
    git("config", "user.email", "workflow-test@example.invalid")
    git("add", "--", "data", "assets/telegram")
    git("commit", "-m", "initial media")

    image.unlink()
    media.rmdir()
    assert not media.exists()

    media.mkdir(parents=True, exist_ok=True)
    git("add", "--", "data", "assets/telegram")

    assert "D\tassets/telegram/1-0.webp" in git("diff", "--cached", "--name-status").stdout
