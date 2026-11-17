"""Run an explicitly authorized test command and retain bounded evidence."""

from __future__ import annotations

import os
import shlex
import subprocess


def run_test(command: str, timeout: int = 300, limit: int = 4000) -> dict:
    argv = shlex.split(command, posix=True)
    if not argv:
        raise ValueError("test command is empty")
    try:
        safe_env = {
            name: value
            for name, value in os.environ.items()
            if name.upper()
            in {
                "PATH",
                "PATHEXT",
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "HOME",
                "USERPROFILE",
                "PYTHONPATH",
                "PYTHONUTF8",
                "LANG",
                "LC_ALL",
            }
        }
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
            env=safe_env,
        )
        return {
            "name": command,
            "command": command,
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-limit:],
            "stderr": completed.stderr[-limit:],
            "skipped": False,
        }
    except subprocess.TimeoutExpired as error:
        return {
            "name": command,
            "command": command,
            "exit_code": 124,
            "stdout": (error.stdout or "")[-limit:],
            "stderr": (error.stderr or "")[-limit:],
            "skipped": True,
            "reason": f"timeout after {timeout}s",
        }
