[CmdletBinding()]
param(
    [string]$OutputDirectory = [Environment]::GetFolderPath("Desktop")
)

$ErrorActionPreference = "Stop"

# Always run from the project root, regardless of the caller's location.
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvDirectory = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvDirectory "Scripts\python.exe"
$buildDirectory = Join-Path $projectRoot "build"
$entryScript = Join-Path $PSScriptRoot "chfs_gui_entry.py"
$iconPath = Join-Path $projectRoot "src\chfs\gui\chfs.ico"

Set-Location $projectRoot

if (-not (Test-Path -LiteralPath $venvPython)) {
    $candidatePaths = @(
        (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.14-64\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.13-64\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.12-64\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.11-64\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
    )
    foreach ($commandName in @("python3.13.exe", "python3.12.exe", "python3.11.exe", "python.exe")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            $candidatePaths += $command.Source
        }
    }

    $pythonSource = $null
    $pythonVersion = $null
    foreach ($candidate in ($candidatePaths | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        $candidateVersion = & $candidate -c 'import sys; print(".".join(map(str, sys.version_info[:2])))'
        if ($LASTEXITCODE -ne 0) {
            continue
        }
        $versionParts = $candidateVersion.Trim().Split(".")
        if (([int]$versionParts[0] -lt 3) -or (([int]$versionParts[0] -eq 3) -and ([int]$versionParts[1] -lt 11))) {
            continue
        }
        & $candidate -c "import tkinter; t = tkinter.Tcl(); t.eval('info patchlevel')" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $pythonSource = $candidate
            $pythonVersion = $candidateVersion.Trim()
            break
        }
    }

    if ($null -eq $pythonSource) {
        throw "No usable Python with Tcl/Tk was found. Repair the managed runtime with: py install --force 3.14"
    }

    Write-Host "Creating the build environment with Python $pythonVersion..." -ForegroundColor Cyan
    & $pythonSource -m venv $venvDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the virtual environment."
    }
}

# A GUI build is invalid when Tcl/Tk is missing. PyInstaller may otherwise
# finish successfully while silently excluding tkinter from the executable.
& $venvPython -c "import tkinter; t = tkinter.Tcl(); print(t.eval('info patchlevel'))"
if ($LASTEXITCODE -ne 0) {
    throw "This Python installation has a broken Tcl/Tk runtime. Run: py install --force 3.14; then delete .venv and build again."
}

Write-Host "Installing or updating build dependencies..." -ForegroundColor Cyan
& $venvPython -m pip install --disable-pip-version-check -e $projectRoot pyinstaller
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install build dependencies. Check the network connection."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $buildDirectory | Out-Null

Write-Host "Building the single-file Windows executable..." -ForegroundColor Cyan
& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --noupx `
    --name "CHFS" `
    --icon $iconPath `
    --paths (Join-Path $projectRoot "src") `
    --collect-data "chfs" `
    --distpath $OutputDirectory `
    --workpath (Join-Path $buildDirectory "pyinstaller") `
    --specpath $buildDirectory `
    $entryScript

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the executable."
}

$exePath = Join-Path $OutputDirectory "CHFS.exe"

# Explorer 会按完整路径缓存 EXE 图标。重复覆盖桌面的 CHFS.exe 时，即使新图标
# 已正确写入 PE 资源，也可能继续显示旧的 Python 图标；通知 Shell 立即重载。
$iconRefresh = Join-Path $env:SystemRoot "System32\ie4uinit.exe"
if (Test-Path -LiteralPath $iconRefresh) {
    & $iconRefresh -show
}
Write-Host "Build completed: $exePath" -ForegroundColor Green
