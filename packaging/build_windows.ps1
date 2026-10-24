# Build "Motic Live" for Windows and a shareable zip in dist\.
# Run from PowerShell on a Windows PC with Python 3.10+ installed:
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" } else { $py = "py" }
if (-not (Test-Path .venv-win\Scripts\python.exe)) { & $py -m venv .venv-win }
.\.venv-win\Scripts\python -m pip install --quiet --upgrade pip
.\.venv-win\Scripts\python -m pip install --quiet -e . pyinstaller

.\.venv-win\Scripts\pyinstaller --noconfirm --clean --windowed `
  --name "Motic Live" `
  --collect-all libusb_package `
  --distpath dist --workpath build\pyinstaller --specpath build `
  packaging\motic_live_app.py

Copy-Item packaging\README-windows.txt "dist\Motic Live\READ ME FIRST.txt"
$zip = "dist\Motic-Live-windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "dist\Motic Live" -DestinationPath $zip
Write-Host "Built $zip"
