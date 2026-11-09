# Stock CARLA 0.9.16 on Windows

Use Windows 10/11 x64 and the Windows **CARLA_0.9.16.zip** linked from the
[official CARLA release](https://github.com/carla-simulator/carla/releases/tag/0.9.16).
The [Windows runtime archive](https://downloads.carlasim.com/Windows/CARLA_0.9.16.zip)
is not GitHub's CARLA "Source code (zip)". Use stock 0.9.16, not a nightly,
UE5 or custom cockpit build. The client targets Town04.

The `curl.exe` download and `tar.exe` extraction method below succeeded in the
2026-10-08 TargetPC-B diagnostic. This standalone guide does not require an
earlier checkout, acceptance record or session variables.

## Download and Check

Reserve space for the 7.8 GB archive and its extracted runtime. In a new
PowerShell window, run this complete block. Choose a **new** writable absolute
directory outside the client source; neither download nor extraction overlays
an existing installation. Keep this same PowerShell window for extraction.

```powershell
$ErrorActionPreference = 'Stop'
$InstallDir = [IO.Path]::GetFullPath((Read-Host 'New absolute directory for CARLA download and runtime').Trim().Trim('"'))
if (Test-Path -LiteralPath $InstallDir) { throw 'Choose a new directory.' }
$Curl = (Get-Command curl.exe -CommandType Application -ErrorAction Stop).Source
$Tar = (Get-Command tar.exe -CommandType Application -ErrorAction Stop).Source
& $Curl --version
if ($LASTEXITCODE -ne 0) { throw 'curl.exe is unavailable.' }
& $Tar --version
if ($LASTEXITCODE -ne 0) { throw 'tar.exe is unavailable.' }
New-Item -ItemType Directory -Path $InstallDir | Out-Null
$Archive = Join-Path $InstallDir 'CARLA_0.9.16.zip'
$DownloadUrl = 'https://downloads.carlasim.com/Windows/CARLA_0.9.16.zip'
& $Curl --silent --show-error --location --fail --output $Archive --write-out '%{http_code} %{url_effective} %{size_download}' $DownloadUrl
if ($LASTEXITCODE -ne 0) { throw 'Download failed; do not extract the partial file.' }
$ArchiveInfo = Get-Item -LiteralPath $Archive
$ArchiveHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
if ($ArchiveInfo.Length -ne 7812252810 -or $ArchiveHash -ne '0972F26FA3F1C741FBFCB4602B4CCF520415B8F8CF3D06CB83C9502FE8118BD2') {
    throw 'Archive differs from the reviewed download. Stop for review; do not bypass this check.'
}
Write-Host "Verified local reference: $($ArchiveInfo.Length) bytes; SHA256 $ArchiveHash"
```

The transfer summary should show HTTP 200 and **7,812,252,810 bytes**. The
SHA-256 above is a **local reference from the reviewed download, not an official
publisher checksum, signature or proof of authenticity**. Compare upstream
verification information if supplied; an upstream file change needs review.

Use `curl.exe`, not PowerShell's `curl` alias. A prior `Invoke-WebRequest`
attempt failed with `Stream was too long` on this large archive. The download
is quiet until it finishes; hashing also takes time. Do not start another copy
or extract a growing file. If either tool is absent, stop and resolve that
prerequisite. After a failure, preserve the error and retry in a new directory.

## Extract and Locate the Runtime

In the **same PowerShell window**, after all download checks pass:

```powershell
$ExtractDir = Join-Path $InstallDir 'CARLA_0.9.16'
if (Test-Path -LiteralPath $ExtractDir) { throw 'Extraction requires a new directory.' }
New-Item -ItemType Directory -Path $ExtractDir | Out-Null
& $Tar -xf $Archive -C $ExtractDir
if ($LASTEXITCODE -ne 0) { throw 'Extraction failed; do not launch this runtime.' }
$CarlaRoot = (Resolve-Path -LiteralPath $ExtractDir).Path
foreach ($RelativeExe in @('CarlaUE4.exe', 'CarlaUE4\Binaries\Win64\CarlaUE4-Win64-Shipping.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $CarlaRoot $RelativeExe) -PathType Leaf)) {
        throw "Missing runtime executable: $RelativeExe. Do not launch a partial extraction."
    }
}
Write-Host "CARLA runtime directory: $CarlaRoot"
```

The reviewed archive places `CarlaUE4.exe` directly in that runtime directory.
Never extract over an old or partial runtime, modify engine binaries, or launch
from inside the ZIP. Install drivers/prerequisites only from authorized sources
with the machine owner's approval.

## Continue with the Client

Install full Python 3.12 x64 with pip, Tcl/Tk and the Python launcher, then follow
the [Introduction](../addons/narrate_to_sim/INTRODUCTION.md) for client setup,
server launch and Town04 loading. Use the printed runtime directory in place
of `C:\CARLA_0.9.16` there; no variable carries into a new PowerShell window.

Use a dedicated server and an unused RPC/streaming port range (the example
uses 2000-2002). If occupied, leave its owner alone and choose an unused range;
use the same RPC port in server, Python commands, ego helper and GUI.
Load Town04 **before** creating an ego: loading a world destroys its actors.
Do not run another controller or autopilot on the selected ego or world clock.
