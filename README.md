# Narrate-to-Sim

Create traffic scenarios in the Add Scenario interface, inspect their design
and JSON, then run them with an explicitly selected vehicle in stock CARLA.
This is an external Python client, not an Unreal Engine plugin.

Copyright (C) 2026 The Regents of the University of Michigan
Intelligence & Human Augmentation Lab

**Target:** Windows 10/11 x64, full Python 3.12 with pip and Tcl/Tk,
stock CARLA 0.9.16, Town04. **Public release:** v0.1.0 (2026-11-09).

## Get the Source

Clone the [public repository](https://github.com/ioe-wang-ihub/narrate-to-sim):

```powershell
git clone --branch v0.1.0 https://github.com/ioe-wang-ihub/narrate-to-sim.git
cd narrate-to-sim\addons\narrate_to_sim
```

Or download **NarrateToSim_Source_v0.1.0.zip** from the
[v0.1.0 release](https://github.com/ioe-wang-ihub/narrate-to-sim/releases/tag/v0.1.0)
and extract into a new writable folder. GitHub's automatic source ZIP/tar.gz
archives of this clean public repository are also suitable. Keep the complete
layout, including `addons/narrate_to_sim`, licenses and documentation.

## First Run

1. Install full Python 3.12 x64 with pip, Tcl/Tk and the Python launcher.
2. Follow [CARLA setup](docs/CARLA_SETUP.md) for the stock Windows runtime.
3. In `addons/narrate_to_sim`, run `setup_narrate_to_sim.bat` once.
4. Follow the [Introduction](addons/narrate_to_sim/INTRODUCTION.md) to start a
   dedicated server, load Town04, select an ego, Analyze and Run in CARLA.

Setup creates a local `.venv` and installs the pinned Python dependencies;
it needs Internet access. The parser runs locally without an API key.
The Introduction includes the exact example prompt, Save/Reopen, Stop and replay.
**Play** in Design is a 2D preview; **Run in CARLA** controls the simulator.

## Playback Boundaries

Playback requires exclusive control of the selected ego and world clock.
For a borrowed ego, pause its controller, turn autopilot OFF and enable physics.
Loading Town04 destroys existing actors: use only your dedicated session.
Cleanup cannot rewind simulation time, unrelated actors or collisions; crashes
may prevent restoration. Read the Introduction's full restoration limits.
This client supports traffic-only playback, not lab-device or takeover workflows.

## Documentation and Status

- [Client README](addons/narrate_to_sim/README.md) and [Introduction](addons/narrate_to_sim/INTRODUCTION.md)
- [Public interface tutorial](https://ioe-wang-ihub.github.io/carla_based_experiment_platform/): shared authoring footage, not stock-client acceptance evidence
- [Verification summary](docs/VERIFICATION.md): automated TargetPC-B checks on 2026-10-08; independent manual reproduction **NOT_TESTED**
- [Changelog](CHANGELOG.md), [Contributing](CONTRIBUTING.md), [Code of Conduct](CODE_OF_CONDUCT.md)
- [Questions and issues](https://github.com/ioe-wang-ihub/narrate-to-sim/issues)

The runtime is unchanged since that automated diagnostic. This release does not
establish universal portability or completed human acceptance.

## Authors and Citation

Paper authors: **Wei-Hsiang Lo, Jincheng Ye and Manhua Wang**, University of
Michigan. Please cite their 2026 paper, *Narrate-to-Sim: A Human Factors-Oriented
Driving Simulator with Natural-Language-Supported Scenario Authoring*,
AutomotiveUI Adjunct '26, Association for Computing Machinery,
[doi:10.1145/3828158.3833211](https://doi.org/10.1145/3828158.3833211).
See [CITATION.cff](CITATION.cff) for full metadata and author identifiers.
For the client, also identify this public repository and the tag or exact commit.

## License

GPL-3.0-or-later. See [License.txt](License.txt) and [Notices.txt](Notices.txt).
