"""forge.verify.junit_xml — minimal JUnit XML writer. Consumes the
per-event result list `forge test run` builds by
looping the existing single-run verification path (`_build_runtime_context`/
`_run_one_loaded_flow`) once per event — this module only renders that
already-computed data as `<testsuite><testcase>` XML, stdlib
`xml.etree.ElementTree` only, no new dependency and no new checking logic.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional


def write_junit_xml(
    path: "Path | str",
    *,
    suite_name: str,
    results: List[Dict[str, Any]],
) -> None:
    """Write a JUnit XML report to *path*.

    Each entry in *results* is a dict with:
      - ``name``    (str): testcase name, e.g. ``"event_0"``.
      - ``success`` (bool): whether this event passed.
      - ``message`` (str, optional): failure detail (e.g. the checker's
        message, or a simulator-log tail) — used as the `<failure>`
        element's text when ``success`` is False.
      - ``time``    (float, optional): elapsed seconds, if known.
    """
    n_failures = sum(1 for r in results if not r.get("success"))
    testsuite = ET.Element("testsuite", {
        "name": suite_name,
        "tests": str(len(results)),
        "failures": str(n_failures),
    })
    for r in results:
        attrs: Dict[str, str] = {"name": str(r["name"]), "classname": suite_name}
        if r.get("time") is not None:
            attrs["time"] = f"{r['time']:.3f}"
        testcase = ET.SubElement(testsuite, "testcase", attrs)
        if not r.get("success"):
            message = r.get("message") or "event failed"
            failure = ET.SubElement(testcase, "failure", {"message": message})
            failure.text = message

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(testsuite).write(str(path), encoding="utf-8", xml_declaration=True)


def render_markdown(path: "Path | str") -> str:
    """Render a JUnit XML file (as written by :func:`write_junit_xml`) as
    a Markdown summary — pure presentation over already-written data, no
    re-running of any test (`forge report`'s verification-results section
    reuses a prior `forge test run --junit-xml`'s output rather than
    recomputing it)."""
    testsuite = ET.parse(path).getroot()
    testcases = testsuite.findall("testcase")
    n_tests = testsuite.get("tests", str(len(testcases)))
    n_failures = testsuite.get("failures", "0")

    lines = [
        "# Verification results",
        "",
        f"- **suite**: {testsuite.get('name', '(unnamed)')}",
        f"- **tests**: {n_tests}",
        f"- **failures**: {n_failures}",
        "",
        "| test | result | detail |",
        "|---|---|---|",
    ]
    for tc in testcases:
        failure = tc.find("failure")
        if failure is not None:
            detail = (failure.get("message") or failure.text or "").replace("\n", " ").strip()
            lines.append(f"| {tc.get('name')} | FAIL | {detail} |")
        else:
            lines.append(f"| {tc.get('name')} | PASS | |")

    return "\n".join(lines) + "\n"
