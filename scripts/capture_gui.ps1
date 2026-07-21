param(
    [string]$OutputPath = "gui-overview.png",
    [ValidateSet("overview", "share", "network", "transfers", "accounts", "security", "logs")]
    [string]$Page = "overview",
    [ValidateSet("stopped", "running")]
    [string]$State = "stopped",
    [string]$ConfigPath = "",
    [switch]$SeedTransfer
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"
$resolvedConfig = if ($ConfigPath) { Join-Path $projectRoot $ConfigPath } else { Join-Path $projectRoot "config.example.json" }
$arguments = @("-m", "chfs.gui.app", "--config", $resolvedConfig, "--capture-page", $Page, "--capture-state", $State, "--print-window-handle")
$pythonExecutable = (& python -c "import sys; print(sys.executable)").Trim()
$stderrPath = Join-Path $projectRoot "artifacts\gui-capture-stderr.log"
$stdoutPath = Join-Path $projectRoot "artifacts\gui-capture-stdout.log"
$artifactDirectory = Split-Path -Parent $stderrPath
if (-not (Test-Path -LiteralPath $artifactDirectory)) {
    New-Item -ItemType Directory -Path $artifactDirectory | Out-Null
}
$process = Start-Process -FilePath $pythonExecutable -ArgumentList $arguments -WorkingDirectory $projectRoot -RedirectStandardError $stderrPath -RedirectStandardOutput $stdoutPath -PassThru
$seedUploadId = $null
$seedBaseUrl = $null

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

    if ($SeedTransfer) {
        $configDocument = Get-Content -Raw -LiteralPath $resolvedConfig | ConvertFrom-Json
        $seedHost = if ($configDocument.host -in @('0.0.0.0', '::')) { '127.0.0.1' } else { [string]$configDocument.host }
        $seedBaseUrl = "http://${seedHost}:$($configDocument.port)"
        $seedBody = @{
            path = 'capture-transfer-demo.bin'
            size = 4294967296
            resume_key = "capture-$([guid]::NewGuid().ToString('N'))"
            overwrite = $false
        } | ConvertTo-Json -Compress
        $seedResponse = Invoke-RestMethod -Method Post -Uri "$seedBaseUrl/api/v1/uploads" -ContentType 'application/json' -Body $seedBody
        $seedUploadId = $seedResponse.upload_id
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
    if ($seedUploadId -and $seedBaseUrl -and -not $process.HasExited) {
        try {
            Invoke-RestMethod -Method Delete -Uri "$seedBaseUrl/api/v1/uploads/$seedUploadId" | Out-Null
        }
        catch {
            # 截图已经完成时，清理失败不覆盖主要验收结果。
        }
    }
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
}
