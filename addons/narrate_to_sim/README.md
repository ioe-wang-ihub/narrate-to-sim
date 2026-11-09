Copyright (C) 2026 The Regents of the University of Michigan

Intelligence & Human Augmentation Lab

# Narrate-to-Sim: CARLA Client Add-on

Create traffic scenarios with the original Add Scenario interface, inspect their
design and JSON, then run them with an explicitly selected stock CARLA vehicle.

This is an external Python client, not an Unreal Engine plugin.

**Evaluation target:** Windows 10/11 x64, full Python 3.12 with pip and Tcl/Tk,
stock CARLA 0.9.16, Town04. **Public release:** v0.1.0 (2026-11-09).

## Get Started

1. [Clone or download the public release](../../README.md#get-the-source),
   keeping the `addons/narrate_to_sim` layout. Follow
   [INTRODUCTION.md](INTRODUCTION.md) to install CARLA and Python.
2. Run `setup_narrate_to_sim.bat` once in a newly extracted, writable directory.
3. Start your CARLA server and load Town04 before spawning any vehicles.
4. Run `start_narrate_to_sim.bat`, then **Refresh CARLA**.
5. Select an ego, write a prompt, **Analyze**, then **Run in CARLA**.

The original Review, JSON, Design, History, guided authoring and saved prompt
workflow remain available. **Play** in Design is a 2D preview; **Run in CARLA**
starts simulator playback. The blueprint selector is authoritative for the ego.

## Scope and Safety

Traffic-only scripted playback. No DReyeVR, takeover alerts, AOI, LSL or wheel
integration is enabled. Unsupported experiment/audio commands cause an error.
The parser runs locally; no API key or online language-model service is required.

Playback needs exclusive control of the world clock and selected ego. It is not
a transparent addition to an already running experiment. See the Introduction
for state changes, restoration limits, output locations and troubleshooting.

The original authoring and runner sources retain their filenames; only the
stock-client entry point above is supported in this package.

`source_manifest.json` records source hashes and the authoring-interface origin
hash. Install dependencies on each recipient computer; do not copy the
development `.venv`. Run tests with the commands below from this directory:

```powershell
.\.venv\Scripts\python.exe -I -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -I tools\rehearse_stock_addon.py
```

The second command exercises Analyze, Design, Save and Reopen with no server.
It briefly opens the GUI. It does not verify live CARLA playback.

The runtime is unchanged since TargetPC-B's 2026-10-08 automated diagnostic:
12 tests and four live cases (new Audi, borrowed Tesla, Stop and replay).
Independent manual reproduction is **NOT_TESTED**. This is not a claim of
universal portability; see the [verification summary](../../docs/VERIFICATION.md).

## License

SPDX-License-Identifier: GPL-3.0-or-later

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See [License.txt](License.txt) for details.

Third-party dependencies and their copied license texts retain their own
terms; see [Notices.txt](Notices.txt).

## Project Resources

From the [Intelligence & Human Augmentation Lab](https://github.com/ioe-wang-ihub).
Paper authors: Wei-Hsiang Lo, Jincheng Ye and Manhua Wang, University of Michigan.
For questions or problems, open a [public issue](https://github.com/ioe-wang-ihub/narrate-to-sim/issues).
Cite their AutomotiveUI Adjunct '26 paper (DOI
[10.1145/3828158.3833211](https://doi.org/10.1145/3828158.3833211)) and, for this
client, the public repository URL and tag or exact commit.

See the [contributor guide](../../CONTRIBUTING.md),
[conduct policy](../../CODE_OF_CONDUCT.md),
[changelog](../../CHANGELOG.md), [citation metadata](../../CITATION.cff),
[CARLA setup](../../docs/CARLA_SETUP.md) and
[public interface tutorial](https://ioe-wang-ihub.github.io/carla_based_experiment_platform/).
Keep the release directory layout, license, notices and upstream license copies.
