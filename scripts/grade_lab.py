#!/usr/bin/env python3
"""
grade_lab.py  --  Auto-grader for C++ lab assignments.

Usage:
    python3 scripts/grade_lab.py --lab 1

The script:
  1. Finds every labN/assignmentM.cpp file.
  2. Compiles each with g++ -std=c++17 -Wall.
  3. Runs the binary against every test case in lab N/tests/assignmentM/.
  4. Compares output using three matching modes:
       EXACT          -- output must match expected file exactly (whitespace trimmed per line)
       __MULTILINE_MIN<n>__ -- output must have at least n non-empty lines
       __CONTAINS__<text>   -- output must contain the given text (case-insensitive)
  5. Scores each assignment: points_earned / points_possible.
  6. Writes RESULT.md and sets GitHub Actions step summary.
"""

import argparse
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from datetime import datetime, timezone

# ── Configuration ─────────────────────────────────────────────────────────────
POINTS_PER_ASSIGNMENT = 20          # Each assignment is worth 20 marks
COMPILE_POINTS        = 8           # Awarded just for compiling cleanly
TEST_POINTS           = 12          # Awarded for passing all test cases
COMPILE_TIMEOUT       = 30          # seconds
RUN_TIMEOUT           = 10          # seconds per test case

ASSIGNMENT_DESCRIPTIONS = {
    1: {
        1: "Print your full name on line 1 and your department on line 2",
        2: "Print a star triangle: *, **, *** on separate lines",
        3: "Print your name, the current year, and your matric number (3 lines)",
        4: "Print a self-introduction of at least 3 lines",
        5: "Fix the buggy code so it prints Hello World correctly",
    }
    # Add more labs here as the course progresses
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def compile_cpp(source: Path, binary: Path) -> tuple[bool, str]:
    """Compile source with g++. Returns (success, message)."""
    result = subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-o", str(binary), str(source)],
        capture_output=True, text=True, timeout=COMPILE_TIMEOUT
    )
    if result.returncode == 0:
        msg = "Compiled successfully"
        if result.stderr.strip():
            msg += f" (with warnings):\n{result.stderr.strip()}"
        return True, msg
    return False, result.stderr.strip()


def run_binary(binary: Path, input_file: Path) -> tuple[bool, str]:
    """Run binary with stdin from input_file. Returns (success, stdout)."""
    try:
        with open(input_file, "r") as f:
            inp = f.read()
        result = subprocess.run(
            [str(binary)],
            input=inp, capture_output=True, text=True, timeout=RUN_TIMEOUT
        )
        return True, result.stdout
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT: program ran longer than 10 seconds (possible infinite loop)"
    except Exception as e:
        return False, f"RUNTIME ERROR: {e}"


def match_output(actual: str, expected_file: Path) -> tuple[bool, str]:
    """
    Compare actual output to expected file content.
    Returns (passed, detail_message).
    """
    expected_raw = expected_file.read_text().strip()

    # ── Special mode: MULTILINE_MIN ──────────────────────────────────────────
    if expected_raw.startswith("__MULTILINE_MIN"):
        try:
            min_lines = int(expected_raw.replace("__MULTILINE_MIN", "").replace("__", ""))
        except ValueError:
            return False, "Grader config error: invalid MULTILINE_MIN value"
        non_empty = [l for l in actual.splitlines() if l.strip()]
        if len(non_empty) >= min_lines:
            return True, f"Output has {len(non_empty)} non-empty lines (minimum required: {min_lines})"
        return False, f"Output has only {len(non_empty)} non-empty line(s); at least {min_lines} required"

    # ── Special mode: CONTAINS ───────────────────────────────────────────────
    if expected_raw.startswith("__CONTAINS__"):
        needle = expected_raw.replace("__CONTAINS__", "").strip()
        if needle.lower() in actual.lower():
            return True, f"Output contains required text: '{needle}'"
        return False, f"Output does not contain required text: '{needle}'"

    # ── Exact match (line by line, trimmed) ──────────────────────────────────
    actual_lines   = [l.rstrip() for l in actual.strip().splitlines()]
    expected_lines = [l.rstrip() for l in expected_raw.splitlines()]

    if actual_lines == expected_lines:
        return True, "Output matches expected exactly"

    # Build a diff-like message
    diff_lines = []
    max_len = max(len(actual_lines), len(expected_lines))
    for i in range(max_len):
        a = actual_lines[i]   if i < len(actual_lines)   else "<missing>"
        e = expected_lines[i] if i < len(expected_lines) else "<extra>"
        mark = "OK" if a == e else "DIFF"
        diff_lines.append(f"  Line {i+1} [{mark}]  expected: {repr(e)}  got: {repr(a)}")
    return False, "Output mismatch:\n" + "\n".join(diff_lines)


def grade_assignment(lab: int, assignment: int, lab_dir: Path) -> dict:
    """Grade one assignment. Returns a result dict."""
    source = lab_dir / f"assignment{assignment}.cpp"
    binary = lab_dir / f"assignment{assignment}.out"
    test_dir = lab_dir / "tests" / f"assignment{assignment}"

    desc = ASSIGNMENT_DESCRIPTIONS.get(lab, {}).get(assignment, f"Assignment {assignment}")

    result = {
        "assignment": assignment,
        "description": desc,
        "found": source.exists(),
        "compiled": False,
        "compile_msg": "",
        "tests": [],
        "points": 0,
        "max_points": POINTS_PER_ASSIGNMENT,
    }

    if not result["found"]:
        result["compile_msg"] = f"File not found: {source.relative_to(lab_dir.parent)}"
        return result

    # Compile
    try:
        ok, msg = compile_cpp(source, binary)
    except subprocess.TimeoutExpired:
        result["compile_msg"] = "TIMEOUT during compilation"
        return result

    result["compiled"]     = ok
    result["compile_msg"]  = msg

    if not ok:
        return result

    result["points"] += COMPILE_POINTS

    # Run tests
    if not test_dir.exists():
        result["tests"].append({"name": "test1", "passed": False, "detail": "No test directory found"})
        return result

    input_files = sorted(test_dir.glob("input*.txt"))
    if not input_files:
        result["tests"].append({"name": "test1", "passed": False, "detail": "No test input files found"})
        return result

    test_points_each = TEST_POINTS / len(input_files)
    all_passed = True

    for inp in input_files:
        test_name    = inp.stem.replace("input", "test")
        expected_file = test_dir / inp.name.replace("input", "expected")

        if not expected_file.exists():
            result["tests"].append({"name": test_name, "passed": False, "detail": "Missing expected output file (grader error)"})
            all_passed = False
            continue

        run_ok, stdout = run_binary(binary, inp)
        if not run_ok:
            result["tests"].append({"name": test_name, "passed": False, "detail": stdout})
            all_passed = False
            continue

        passed, detail = match_output(stdout, expected_file)
        result["tests"].append({"name": test_name, "passed": passed, "detail": detail})
        if passed:
            result["points"] += test_points_each
        else:
            all_passed = False

    # Clean up binary
    if binary.exists():
        binary.unlink()

    return result


def build_result_md(lab: int, results: list[dict], total: int, max_total: int) -> str:
    """Build the RESULT.md content."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pct = round((total / max_total) * 100) if max_total > 0 else 0

    grade_letter = (
        "A" if pct >= 70 else
        "B" if pct >= 60 else
        "C" if pct >= 50 else
        "D" if pct >= 45 else "F"
    )

    lines = [
        f"# Lab {lab} Auto-Grade Report",
        f"",
        f"**Graded:** {now}  ",
        f"**Total Score:** {total} / {max_total} ({pct}%)  ",
        f"**Grade:** {grade_letter}",
        f"",
        f"---",
        f"",
    ]

    for r in results:
        status = "PASS" if r["points"] == r["max_points"] else ("PARTIAL" if r["points"] > 0 else "FAIL")
        emoji  = "✅" if status == "PASS" else ("⚠️" if status == "PARTIAL" else "❌")

        lines += [
            f"## {emoji} Assignment {r['assignment']} ({r['points']:.0f} / {r['max_points']} marks)",
            f"**Task:** {r['description']}",
            f"",
        ]

        if not r["found"]:
            lines += [f"> File not submitted: `lab{lab}/assignment{r['assignment']}.cpp`", ""]
            continue

        compile_icon = "✅" if r["compiled"] else "❌"
        lines += [f"**Compilation:** {compile_icon} {r['compile_msg']}", ""]

        if not r["compiled"]:
            lines += [
                "```",
                r["compile_msg"],
                "```",
                "",
                f"> Fix the compilation error and push again.",
                "",
            ]
            continue

        if r["tests"]:
            lines.append("**Test Results:**")
            lines.append("")
            for t in r["tests"]:
                t_icon = "✅" if t["passed"] else "❌"
                lines.append(f"- {t_icon} `{t['name']}`: {t['detail']}")
            lines.append("")

        lines.append("---")
        lines.append("")

    lines += [
        "## Summary",
        "",
        f"| Assignment | Score | Status |",
        f"|------------|-------|--------|",
    ]
    for r in results:
        status = "Pass" if r["points"] == r["max_points"] else ("Partial" if r["points"] > 0 else "Fail / Not submitted")
        lines.append(f"| Assignment {r['assignment']} | {r['points']:.0f} / {r['max_points']} | {status} |")

    lines += [
        f"| **TOTAL** | **{total} / {max_total}** | **{grade_letter} ({pct}%)** |",
        "",
        "> This report is generated automatically. Push a corrected file to update your score.",
    ]

    return "\n".join(lines)


def write_github_summary(content: str):
    """Write to GitHub Actions step summary if available."""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as f:
            f.write(content)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Grade a C++ lab assignment")
    parser.add_argument("--lab", type=int, required=True, help="Lab number (e.g. 1)")
    args = parser.parse_args()

    lab     = args.lab
    lab_dir = Path(f"lab{lab}")

    if not lab_dir.exists():
        print(f"ERROR: Directory '{lab_dir}' not found. Nothing to grade.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Grading Lab {lab}")
    print(f"{'='*60}\n")

    results   = []
    total     = 0
    max_total = 0

    for i in range(1, 6):   # 5 assignments per lab
        print(f"  Grading assignment{i}.cpp ...")
        r = grade_assignment(lab, i, lab_dir)
        results.append(r)
        total     += r["points"]
        max_total += r["max_points"]
        status = f"{r['points']:.0f}/{r['max_points']}"
        print(f"    {'OK' if r['compiled'] else 'COMPILE FAIL':<14} Score: {status}")

    print(f"\n  TOTAL: {total:.0f} / {max_total}\n")

    # Build report
    md = build_result_md(lab, results, int(total), max_total)

    # Write RESULT.md
    result_path = Path("RESULT.md")
    result_path.write_text(md)
    print(f"  Written: {result_path}")

    # Write GitHub Actions summary
    write_github_summary(md)

    # Exit non-zero if any assignment failed (so Actions marks the run red)
    if total < max_total:
        sys.exit(1)


if __name__ == "__main__":
    main()
