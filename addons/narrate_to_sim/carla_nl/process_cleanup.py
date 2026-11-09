"""Stop owned subprocesses, including Windows virtualenv child interpreters."""

import os
import subprocess


def terminate_process_tree(proc, timeout=4):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # Kill the tree while its root still exists. Terminating the root first
        # loses its descendants (notably Windows venv Python and the HUD).
        result = subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 and proc.poll() is None:
            raise RuntimeError(
                "Could not stop process tree {}: {}".format(
                    proc.pid, result.stdout.decode(errors="replace").strip()
                )
            )
        proc.wait(timeout=timeout)
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)
