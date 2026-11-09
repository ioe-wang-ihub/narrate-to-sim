# Contributing

Use the public [issue tracker](https://github.com/ioe-wang-ihub/narrate-to-sim/issues)
and pull requests for focused fixes, documentation improvements and questions.
Follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Set Up

Keep the `addons/narrate_to_sim` layout and follow the
[Introduction](addons/narrate_to_sim/INTRODUCTION.md). Use full Python 3.12 x64
with pip and Tcl/Tk. Run setup in your own writable checkout; do not share a
virtual environment. Live checks need a dedicated stock CARLA 0.9.16/Town04
session and exclusive control of the ego and world clock.

From `addons/narrate_to_sim`, the existing local checks are:

```powershell
.\.venv\Scripts\python.exe -I -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -I tools\rehearse_stock_addon.py
```

The second command briefly opens the GUI to exercise Analyze, Design, Save and
Reopen without a server. Neither command alone verifies live CARLA playback.
Describe checks actually run, failures and checks not run. See the
[verification summary](docs/VERIFICATION.md) for the limits of existing evidence.

For intentional changes to manifest-covered client files, review the changes,
then refresh the manifest from `addons/narrate_to_sim` and rerun the checks:

```powershell
.\.venv\Scripts\python.exe -I tools\update_source_manifest.py
```

Before committing, return to the repository root in a **full Git checkout**,
stage only intended files with `git add` (including the reviewed manifest),
then run the public-source audit:

```powershell
.\addons\narrate_to_sim\.venv\Scripts\python.exe -I tools\check_public_source.py
```

This audit requires Git metadata and the staged inventory. Extracted release
ZIPs and GitHub automatic source archives are not Git checkouts; use a clone
for this contribution check. Do not refresh hashes to hide unintended changes.

## Propose a Change

- Describe the problem, expected behavior, actual behavior and minimal steps.
- Give the public tag/commit, OS, Python and CARLA versions, and selected map/ego.
- Keep changes focused; preserve original authoring and runner filenames.
- Add focused tests for behavior changes and update affected instructions.
- Preserve source-author credit, copyright, licensing and upstream notices.

## Public Reports and Privacy

Issues, pull requests and their attachments are public. Share a concise,
sanitized summary, not personal records or raw experiment evidence. Remove
usernames, private paths/URLs, credentials, participant data and identifying
screenshots from any example. Do not upload `.venv/`, `runtime/`, `outputs/`,
simulator installations or private acceptance records.

Report automated diagnostics separately from manual reproduction. Do not mark
untested machines or workflows as passing. For sensitive concerns, follow the
contact guidance in the Code of Conduct instead of posting details publicly.
