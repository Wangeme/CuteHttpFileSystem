param(
    [string]$OutputPath = "gui-overview.png",
    [ValidateSet("overview", "share", "network", "accounts", "security", "logs")]
    [string]$Page = "overview"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$arguments = @("-m", "chfs.gui.app", "--config", (Join-Path $projectRoot "config.example.json"), "--capture-page", $Page, "--print-window-handle")
$pythonExecutable = (& python -c "import sys; print(sys.executable)").Trim()
$stderrPath = Join-Path $projectRoot "artifacts\gui-capture-stderr.log"
$stdoutPath = Join-Path $projectRoot "artifacts\gui-capture-stdout.log"
$artifactDirectory = Split-Path -Parent $stderrPath
if (-not (Test-Path -LiteralPath $artifactDirectory)) {
    New-Item -ItemType Directory -Path $artifactDirectory | Out-Null
}
$process = Start-Process -FilePath $pythonExecutable -ArgumentList $arguments -WorkingDirectory $projectRoot -RedirectStandardError $stderrPath -RedirectStandardOutput $stdoutPath -PassThru

try {
    $deadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 200
        $process.Refresh()
        $rawGeometry = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -Raw -LiteralPath $stdoutPath } else { $null }
        $geometryText = if ($null -eq $rawGeometry) { "" } else { $rawGeometry.ToString().Trim() }
    } while (-not $geometryText -and -not $process.HasExited -and (Get-Date) -lt $deadline)

    if ($process.HasExited -or -not $geometryText) {
        $details = if (Test-Path -LiteralPath $stderrPath) { Get-Content -Raw -LiteralPath $stderrPath } else { "无错误输出" }
        throw "CHFS GUI 窗口未能启动：$details"
    }

    # 等待桌面窗口管理器完成首次合成，避免复杂控件（如 Treeview）只截到半绘制状态。
    Start-Sleep -Milliseconds 700

    Add-Type -AssemblyName System.Drawing
    $geometry = $geometryText | ConvertFrom-Json
    $width = [int]$geometry.width
    $height = [int]$geometry.height
    if ($width -le 0 -or $height -le 0) { throw "GUI 客户区尺寸无效。" }
    $bitmap = New-Object System.Drawing.Bitmap($width, $height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen([int]$geometry.x, [int]$geometry.y, 0, 0, $bitmap.Size)
        $target = Join-Path $projectRoot $OutputPath
        $targetDirectory = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $targetDirectory)) {
            New-Item -ItemType Directory -Path $targetDirectory | Out-Null
        }
        $bitmap.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
        Write-Output $target
    }
    finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}
finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
}
