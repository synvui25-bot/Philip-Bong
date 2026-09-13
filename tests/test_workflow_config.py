from pathlib import Path
import re
import subprocess
import sys


WORKFLOW_PATH = Path(".github/workflows/telegram-listings.yml")


def test_sync_workflow_is_scheduled_manual_and_secret_scoped():
    """A missing trigger, unsafe scope, or unpinned action must block publishing."""
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "cron: '*/5 * * * *'" in workflow
    assert "workflow_dispatch:" in workflow
    assert "bootstrap:" in workflow
    assert "type: boolean" in workflow
    assert "python -m scripts.sync_telegram ${{ inputs.bootstrap && '--bootstrap' || '' }}" in workflow
    assert "\n  push:" not in workflow
    assert "permissions: {}" in workflow
    assert "concurrency:\n  group: telegram-listing-sync\n  cancel-in-progress: false" in workflow
    assert re.search(r"actions/checkout@[0-9a-f]{40}\s+# v4", workflow)
    assert re.search(r"actions/setup-python@[0-9a-f]{40}\s+# v5", workflow)
    assert "pip install --require-hashes -r requirements.txt" in workflow
    assert "TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}" in workflow
    assert "mkdir -p assets/telegram" in workflow
    assert "git add -- data assets/telegram" in workflow
    assert "git add ." not in workflow


def test_pages_deployment_follows_successful_sync_even_when_no_commit_is_needed():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    jobs = dict(re.findall(r"^  ([a-z_-]+):\n(.*?)(?=^  [a-z_-]+:\n|\Z)", workflow.split("\njobs:\n", 1)[1], re.M | re.S))
    assert "deploy" in jobs, "GITHUB_TOKEN commits must have an explicit Pages deployment"
    sync, deploy = jobs["sync"], jobs["deploy"]
    assert "    needs: sync\n" in deploy
    assert not re.search(r"^    if:", deploy, re.M), "deployment must not depend on changed output"
    assert "continue-on-error" not in workflow
    assert "    permissions:\n      contents: write\n      pages: read\n" in sync
    assert "    permissions:\n      pages: write\n      id-token: write\n" in deploy
    assert "name: github-pages" in deploy
    assert "url: ${{ steps.deployment.outputs.page_url }}" in deploy
    assert "TELEGRAM_BOT_TOKEN" not in deploy
    assert workflow.count("TELEGRAM_BOT_TOKEN:") == 1
    assert re.search(r"actions/configure-pages@[0-9a-f]{40}\s+# v5", sync)
    assert re.search(r"actions/upload-pages-artifact@[0-9a-f]{40}\s+# v[34]", sync)
    assert re.search(r"id: deployment\n\s+uses: actions/deploy-pages@[0-9a-f]{40}\s+# v4", deploy)
    assert sync.index("scripts.sync_telegram") < sync.index("git push") < sync.index("actions/upload-pages-artifact@")
    assert sync.index("actions/configure-pages@") < sync.index("actions/upload-pages-artifact@")
    assert "path: _site" in sync
    assert "cp index.html *.webp _site/" in sync
    assert "cp -R assets _site/" in sync
    assert "cp data/telegram-listings.json _site/data/" in sync
    assert "cp -R ." not in sync, "publishing must exclude credentials, tools and sync state"


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

