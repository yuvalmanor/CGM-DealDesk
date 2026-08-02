"""Contract tests over the Task Scheduler definition (Phase 7).

Windows, not Python, executes this task, so the XML *is* the deliverable — the
only way its guarantees survive an edit is to assert on them here. Each test
pins one of the phase's promises: fires twice a day, is a plain script
invocation (no agent loop, so idling is free), and a missed run is picked up
rather than skipped.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TASK_XML = SCRIPTS / "dealdesk-daily.xml"
INSTALL_PS1 = SCRIPTS / "install-task.ps1"
RUN_PS1 = SCRIPTS / "run-daily.ps1"

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture(scope="module")
def task() -> ET.Element:
    return ET.parse(TASK_XML).getroot()


def _setting(task: ET.Element, name: str) -> str:
    el = task.find(f"t:Settings/t:{name}", NS)
    assert el is not None, f"<{name}> is missing from <Settings>"
    return (el.text or "").strip()


def test_fires_twice_a_day(task: ET.Element) -> None:
    # Two independent once-a-day triggers (default 07:00 and 20:00). Each must be a
    # plain daily schedule; a third would be an unintended extra run.
    triggers = task.findall("t:Triggers/t:CalendarTrigger", NS)
    assert len(triggers) == 2, "exactly two daily triggers — the twice-daily schedule"
    for trigger in triggers:
        assert trigger.findtext("t:ScheduleByDay/t:DaysInterval", namespaces=NS) == "1"
        assert trigger.findtext("t:Enabled", namespaces=NS) == "true"
    # The two triggers must fire at different times, or they'd be one run duplicated.
    boundaries = [t.findtext("t:StartBoundary", namespaces=NS) for t in triggers]
    assert len(set(boundaries)) == 2, "the two triggers must have distinct start times"
    assert _setting(task, "Enabled") == "true"


def test_missed_run_executes_at_next_opportunity(task: ET.Element) -> None:
    # The acceptance criterion: machine off/asleep at the scheduled time must not
    # cost a day's triage.
    assert _setting(task, "StartWhenAvailable") == "true"


def test_a_missed_run_is_not_skipped_on_battery(task: ET.Element) -> None:
    # Defaults are true; left alone they would silently re-skip the catch-up run
    # on a laptop, defeating StartWhenAvailable.
    assert _setting(task, "DisallowStartIfOnBatteries") == "false"
    assert _setting(task, "StopIfGoingOnBatteries") == "false"


def test_does_not_wake_the_machine(task: ET.Element) -> None:
    # ADR-0001 accepts "won't fire while asleep" for a non-urgent daily batch and
    # relies on the catch-up run instead.
    assert _setting(task, "WakeToRun") == "false"


def test_does_not_wait_for_an_idle_machine(task: ET.Element) -> None:
    assert _setting(task, "RunOnlyIfIdle") == "false"
    assert task.findtext("t:Settings/t:IdleSettings/t:StopOnIdleEnd", namespaces=NS) == "false"


def test_overlapping_runs_are_ignored(task: ET.Element) -> None:
    # A catch-up run landing on top of a scheduled one must not process the queue
    # twice concurrently.
    assert _setting(task, "MultipleInstancesPolicy") == "IgnoreNew"


def test_action_is_a_plain_script_invocation(task: ET.Element) -> None:
    # "Firing costs nothing while idle" holds because the action is one
    # PowerShell process that exits — not a resident agent.
    actions = task.findall("t:Actions/t:Exec", NS)
    assert len(actions) == 1
    assert actions[0].findtext("t:Command", namespaces=NS) == "powershell.exe"
    args = actions[0].findtext("t:Arguments", namespaces=NS) or ""
    assert "-NoProfile" in args and "-NonInteractive" in args
    assert '-File "{{SCRIPT_PATH}}"' in args


def test_runs_as_the_operator_without_a_stored_password(task: ET.Element) -> None:
    principal = task.find("t:Principals/t:Principal", NS)
    assert principal is not None
    assert principal.findtext("t:LogonType", namespaces=NS) == "InteractiveToken"
    assert principal.findtext("t:RunLevel", namespaces=NS) == "LeastPrivilege"


def test_wrapper_runs_the_live_pipeline() -> None:
    # The scheduled task passes no arguments, so the wrapper's own invocation
    # decides what fires: a live `run`, never a dry run. Comment-based help
    # mentions --dry-run as a manual smoke test, so only the code body counts.
    body = re.sub(r"<#.*?#>", "", RUN_PS1.read_text(encoding="utf-8"), flags=re.DOTALL)
    body = re.sub(r"(?m)^\s*#.*$", "", body)
    assert re.search(r"-m\s+dealdesk\s+run\b", body), "wrapper must invoke `dealdesk run`"
    assert "--dry-run" not in body, "a live run must not default to --dry-run"


def test_installer_substitutes_every_placeholder() -> None:
    # A placeholder added to the XML but not taught to the installer registers a
    # task with a literal "{{...}}" path in it, which fails only at fire time.
    in_xml = set(re.findall(r"{{(\w+)}}", TASK_XML.read_text(encoding="utf-8")))
    substituted = set(re.findall(r"Replace\('{{(\w+)}}'", INSTALL_PS1.read_text(encoding="utf-8")))
    assert in_xml == substituted
