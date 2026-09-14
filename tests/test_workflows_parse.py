"""Every GitHub Actions workflow must be valid YAML.

An unparseable workflow does not fail loudly -- GitHub declines to run
it, so the build goes *quiet* rather than red, and a deploy gate that
never executes looks exactly like one that passed. `deploy.yml` shipped
in that state: an unquoted `run:` whose awk format string contained
": ", which YAML reads as a nested mapping.

No marker: this is pure file parsing, and belongs in the fast tier.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted(
    (Path(__file__).resolve().parent.parent / ".github" / "workflows").glob(
        "*.yml"
    )
)


def test_there_are_workflows_to_check() -> None:
    """Guard against the glob silently matching nothing."""
    assert WORKFLOWS, "no workflows found; has the directory moved?"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_is_valid_yaml(path: Path) -> None:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        pytest.fail(f"{path.name} is not valid YAML: {exc}")

    assert isinstance(loaded, dict), f"{path.name} is not a mapping"
    # `on:` is YAML 1.1's boolean true, which is why it arrives as a key
    # of `True` rather than the string. Either spelling means the
    # workflow declares its triggers.
    assert "jobs" in loaded, f"{path.name} declares no jobs"
    assert "on" in loaded or True in loaded, f"{path.name} declares no triggers"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_step_has_something_to_run(path: Path) -> None:
    """A step with neither `uses` nor `run` is a typo that GitHub rejects."""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    for job_name, job in loaded["jobs"].items():
        for index, step in enumerate(job.get("steps") or []):
            assert "uses" in step or "run" in step, (
                f"{path.name}: {job_name} step {index} "
                f"({step.get('name', 'unnamed')}) has neither uses nor run"
            )
