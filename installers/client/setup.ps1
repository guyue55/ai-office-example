# 不修改全局PATH、执行策略或AI工具设置。使用项目专属uv和Python。
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Tool = Join-Path $Root '.office/tools/uv.exe'
if (-not (Test-Path $Tool)) {
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { $Target = 'aarch64-pc-windows-msvc' }
    elseif ($env:PROCESSOR_ARCHITECTURE -eq 'AMD64') { $Target = 'x86_64-pc-windows-msvc' }
    else { throw '仅支持Windows x64/ARM64' }
    $Dir = Split-Path $Tool
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $Archive = Join-Path $Dir 'uv.zip'
    $Digest = Join-Path $Dir 'uv.sha256'
    $Base = "https://github.com/astral-sh/uv/releases/download/0.9.18/uv-$Target.zip"
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 300 -Uri $Base -OutFile $Archive
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 300 -Uri "$Base.sha256" -OutFile $Digest
    $Expected = ((Get-Content $Digest -Raw).Trim() -split '\s+')[0]
    if ($Expected -notmatch '^[0-9a-fA-F]{64}$' -or (Get-FileHash $Archive -Algorithm SHA256).Hash -ne $Expected) { throw 'uv摘要校验失败' }
    Expand-Archive -Path $Archive -DestinationPath $Dir -Force
    Remove-Item $Archive, $Digest
}
& $Tool run --no-project --python 3.13 --no-config (Join-Path $Root 'setup_client.py') --uv $Tool
if ($LASTEXITCODE -ne 0) { throw '安装未通过，请查看上方错误，勿继续登录' }
