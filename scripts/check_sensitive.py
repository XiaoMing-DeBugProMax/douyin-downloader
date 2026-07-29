from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_RULES = (
    (
        "concrete_ttwid",
        re.compile(r"\bttwid=[A-Za-z0-9%_.-]{20,}"),
    ),
    (
        "concrete_verify_fp",
        re.compile(r"\bs_v_web_id=[A-Za-z0-9_-]{20,}"),
    ),
    (
        "concrete_launch_token",
        re.compile(
            r"""["']?launch_token["']?\s*[:=]\s*["'][A-Za-z0-9_-]{20,}["']"""
        ),
    ),
    (
        "queried_media_url",
        re.compile(r"https://[A-Za-z0-9.-]+\.douyinvod\.com/[^\s\"']+\?"),
    ),
)
_REPORT_SHARE_URL = re.compile(r"https://v\.douyin\.com/[A-Za-z0-9_-]{6,}/")


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int
    rule: str

    def safe_summary(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}"


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    normalized_path = path.replace("\\", "/")
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in _RULES:
            if pattern.search(line):
                findings.append(Finding(normalized_path, line_number, rule))
        if normalized_path.startswith("docs/test-reports/") and _REPORT_SHARE_URL.search(line):
            findings.append(Finding(normalized_path, line_number, "share_url_in_report"))
    return findings


def _repository_paths(project_root: Path) -> list[str]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable was not found")
    result = subprocess.run(  # noqa: S603 - resolved Git executable with fixed arguments
        [git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    return [
        item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    ]


def scan_repository(project_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for relative_path in _repository_paths(project_root):
        path = project_root / relative_path
        try:
            content = path.read_bytes()
        except OSError:
            findings.append(Finding(relative_path, 0, "tracked_file_unreadable"))
            continue
        if b"\0" in content:
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(relative_path, text))
    return findings


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    findings = scan_repository(project_root)
    if findings:
        for finding in findings:
            print(finding.safe_summary())
        print(f"FAIL sensitive_boundary findings={len(findings)}")
        return 1
    print("PASS sensitive_boundary findings=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
