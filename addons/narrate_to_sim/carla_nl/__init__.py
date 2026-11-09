"""carla_nl — natural-language CARLA scenario agent.

Local Python package that lets researchers describe driving scenarios in
Chinese or English; Claude translates the description into a v1 scenario
JSON, saves it, and (with user confirmation) launches the simulator via
the bundled PowerShell launcher.

Entry point: `python -m carla_nl.agent` (run from the parent directory).
"""

__version__ = "0.1.0"
