# Verification Summary

Public release: **v0.1.0, 2026-11-09**. Target: Windows 10/11 x64, full Python
3.12 with Tcl/Tk, stock CARLA 0.9.16 and Town04. Runtime code is unchanged since
the automated TargetPC-B diagnostic below; this public documentation update
does not constitute a new simulator trial.

## Recorded Automated Diagnostic

On **2026-10-08**, TargetPC-B ran Windows 11 x64, Python 3.12.10/Tk 8.6 and
CARLA client/server 0.9.16 with Town04 on a separate physical computer.

| Check | Recorded result |
| --- | --- |
| Existing 12 client tests | PASS |
| Actual GUI Analyze, Design, Save and Reopen | Automated PASS |
| Case 1: new `vehicle.audi.tt` | COMPLETED |
| Case 2: borrowed `vehicle.tesla.model3` | COMPLETED |
| Case 3: Stop with the same borrowed Tesla | CANCELLED |
| Case 4: new Audi replay | COMPLETED |
| Normal cleanup and borrowed/unrelated actor preservation assertions | PASS |
| Captured world settings, weather and spectator restoration assertions | PASS |
| Two injected-error cleanup checks | PASS |
| Independent manual GUI reproduction | NOT_TESTED |

These results summarize recorded evidence, not a fresh execution on the public
release. The required visible operator workflow was not completed; full
functional/instruction acceptance remains incomplete. Automated GUI operation
is not evidence of independent human reproduction.

## Limits and Reporting

There is no claim of universal portability, offline installation, other maps
or CARLA versions, or full lab-hardware validation. Successful restoration
assertions do not imply that every external simulator state can be restored.
See the [Introduction](../addons/narrate_to_sim/INTRODUCTION.md) for selected-ego,
world-clock, autopilot and cleanup limits, and [Contributing](../CONTRIBUTING.md)
for local check commands and sanitized reporting.

Only this summary is published here. Personal records, raw private acceptance
logs and identifying machine/user evidence are not part of the public docs.
A future result should identify the public tag/commit and environment and
distinguish automated checks, live cases and manual reproduction explicitly.
