# Introduction: Create and Run a Stock CARLA Scenario

Narrate-to-Sim is a standalone Python client using the shared Add Scenario UI
and CARLA's public Python API, without an Unreal rebuild, cloud key or lab hardware.

## 1. Prerequisites and Client Setup

Use **Windows 10/11 x64, full Python 3.12 x64 with pip, Tcl/Tk and the Python
launcher, stock CARLA 0.9.16, and Town04**. Other maps/versions are not supported
by this entry point. Install Python using the
[full Windows installer](https://docs.python.org/3.12/using/windows.html#the-full-installer),
including pip, Tcl/Tk and `py.exe`; do not use the embeddable distribution.
Follow [CARLA setup](../../docs/CARLA_SETUP.md) for the official Windows runtime
and the reviewed `curl.exe` download / `tar.exe` extraction method. Never extract
over an existing runtime or launch a partial extraction.

Clone the [public repository](https://github.com/ioe-wang-ihub/narrate-to-sim),
or download `NarrateToSim_Source_v0.1.0.zip` from the
[v0.1.0 release](https://github.com/ioe-wang-ihub/narrate-to-sim/releases/tag/v0.1.0)
and extract into a new writable folder. GitHub's automatic source archives of
this public repository work too. Keep the full directory layout. In the
examples, replace `C:\NarrateToSim` with its **`addons\narrate_to_sim` directory**
(containing `setup_narrate_to_sim.bat`), and `C:\CARLA_0.9.16` with the folder
containing `CarlaUE4.exe`. In PowerShell:

```powershell
cd C:\NarrateToSim
.\setup_narrate_to_sim.bat
```

Setup needs Internet access, creates this client's `.venv`, and installs pinned
dependencies from `requirements-stock.txt`, including `carla==0.9.16`; it does
not install CARLA. Do not copy another PC's environment. Stop on a setup error.
Without `py.exe`, supply your full Python 3.12 interpreter path instead:

```powershell
.\setup_narrate_to_sim.bat -Python "C:\Path\To\Python312\python.exe"
```

## 2. Launch a Dedicated CARLA Server and Town04

In a separate PowerShell window, start your own fresh simulator:

```powershell
cd C:\CARLA_0.9.16
.\CarlaUE4.exe -windowed -ResX=1280 -ResY=720 -quality-level=Low -carla-rpc-port=2000
```

Wait for the simulator window. **Loading a world destroys its current actors.**
Before creating any ego, load Town04 from another PowerShell window:

```powershell
cd C:\NarrateToSim
.\.venv\Scripts\python.exe -I -c "import carla; c=carla.Client('127.0.0.1',2000); c.set_timeout(120); print(c.load_world('Town04').get_map().name)"
```

Do not do this in another experiment's world. The GUI never reloads the map
or closes a server you started. Keep other simulation controllers stopped.
Use unused ports 2000-2002; if occupied, choose another unused range and update
the RPC port consistently in the server, Python commands, helper and GUI.

## 3. Open the GUI and Choose an Ego

From the client directory, launch the Add Scenario interface:

```powershell
.\start_narrate_to_sim.bat
```

Set **RPC port** to `2000`, click **Refresh CARLA**, and confirm `Connected`,
`CARLA 0.9.16` and `Town04`. The connection is local (`127.0.0.1`).
For a first run, choose **Ego: New vehicle for each run** and
**Blueprint: `vehicle.audi.tt`**. This ego is created at Run and removed at cleanup.

**Optional existing Tesla:** in another terminal in the client directory, run:

```powershell
.\.venv\Scripts\python.exe -I .\examples\create_ego.py --port 2000 --blueprint vehicle.tesla.model3 --role-name my_lab_ego
```

Keep that terminal open; it prints an actor ID and does not continuously control
the vehicle. Refresh the GUI and select that exact
`#ID | vehicle.tesla.model3 | my_lab_ego` in **Ego**. Blueprint follows the actor.
Actor ID identifies a live vehicle; blueprint identifies its model. `role_name`
is only a label. Refresh/reselect after any reload or respawn.

For any borrowed ego, **pause its controller, turn autopilot OFF, and enable
physics first**. No competing controller may command the ego or world clock.
Unknown autopilot registration and disabled physics state cannot be restored.

## 4. Analyze and Save

Set **Name** to `town04_cut_in`, **Map** to `Town04`, and leave **Start** at
**Default**. Enter exactly:

```text
scenario duration=12s
ego: lane=left, speed=30km/h
v1: lane=middle, ahead=4s, speed=30km/h
t=3s: v1 -> left, duration=3s
```

Click **Analyze**, inspect the layout/timeline in **Design**, and check **JSON**:
`dreyevr` is `false`; `vehicles.ego.blueprint_filter` matches your selection.
The Blueprint selector controls the ego model, not a model named in the prompt.
Click **Save Scenario**, then select the saved scenario and **Edit** to reopen.
Choose the intended live ego again, or keep **New vehicle for each run**.
Saved scenarios retain the blueprint, not a portable live actor ID.

## 5. Run, Stop and Replay

Click **Run in CARLA**, review the confirmation and approve this dedicated run.
Observe the ego and lane change; wait for `COMPLETED` and cleanup.
To exercise Stop, change only `scenario duration=12s` to `scenario duration=60s`,
Analyze, Run, then click **Stop** after playback starts. Wait for `CANCELLED`
and cleanup. Restore the original prompt, Analyze and Run again to replay.
**Play in Design only animates the 2D preview; it is not Run in CARLA.**
Edits do not alter an active run: Stop and wait before analyzing/running changes.

**Run logs** opens `outputs/stock_runs/` run records: `request.json`,
`playback.log`, `result.json` and, when captured, `camera.png`.
Check `result.json`: `FAILED` or nonempty `cleanup_errors` is not success.
Saved scenarios: `outputs/scenarios/`; prompt/history/map cache: `runtime/`.
Finish playback before pressing Ctrl+C in the optional Tesla terminal; the helper
removes only its own vehicle. Close CARLA yourself.

## Safety and Troubleshooting

Playback temporarily changes the clock, weather and spectator, teleports and
controls the ego, and creates scenario vehicles/sensors. Normal completion and
cooperative Stop remove owned actors and restore captured world settings,
weather, spectator pose and borrowed ego pose/control/velocities.
The borrowed ego remains with physics enabled; its owner must resume autopilot
if needed. Simulation time, unrelated actors' motion, collisions and other
clients' internal state cannot be rolled back. Hard termination or a server
crash can prevent cleanup. This is not a non-invasive or manual-driving adapter.

For connection errors, check server, port, firewall and map loading. For a synchronous
world, stop the other controller through normal shutdown, then Refresh. For stale
actors, Refresh/reselect. For path/cleanup failures, inspect `playback.log`/`result.json`;
restart only your dedicated session if uncertain. Never delete another client's actors.

## Video and Validation Scope

The [public interface tutorial](https://ioe-wang-ihub.github.io/carla_based_experiment_platform/)
uses lab footage to show shared prompt authoring, CARLA event preview and refinement.
LSL, playlist, device, TOR and custom-cockpit features visible in that context
are not included in this stock client. The video is neither an installation
tutorial nor acceptance evidence for this exact client.

TargetPC-B passed automated authoring, live new/borrowed-ego playback, Stop,
replay and cleanup on **2026-10-08**. Independent manual GUI reproduction is
**NOT_TESTED**; full visible-workflow acceptance remains incomplete. Runtime
code is unchanged for public **v0.1.0 (2026-11-09)**. No new live trial or universal
portability is claimed. See the public [verification summary](../../docs/VERIFICATION.md).
