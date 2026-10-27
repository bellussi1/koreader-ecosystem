from pathlib import Path

import pytest


@pytest.mark.parametrize("workflow", ["ci.yml", "desktop.yml", "secrets.yml"])
def test_only_pull_request_runs_cancel_older_runs(workflow: str) -> None:
    """Pushes, scheduled scans and manual runs must not cancel each other."""
    text = (Path(__file__).parents[2] / ".github" / "workflows" / workflow).read_text(
        encoding="utf-8"
    )
    assert "github.event.pull_request.number || github.run_id" in text
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in text


def test_release_runs_are_never_cancelled_by_another_run() -> None:
    text = (Path(__file__).parents[2] / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "cancel-in-progress: false" in text
