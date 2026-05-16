# ComfyDir PWA smoke
# - launcher.py を pythonw.exe で起動 (tray + uvicorn thread)
# - 127.0.0.1:8765 が bind されるまで最大 15 秒待つ
# - bind できたら kill して exit 0
# - safe-commit skill のゲート 5 (実機エントリポイント検証) から呼び出される想定
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$pyw  = Join-Path $root ".venv\Scripts\pythonw.exe"
$launcher = Join-Path $root "launcher.py"

if (-not (Test-Path $pyw))      { throw "pythonw not found: $pyw" }
if (-not (Test-Path $launcher)) { throw "launcher.py not found: $launcher" }

# 既存サーバが動いていたら smoke できないので、事前に検出して中断
try {
    $pre = Test-NetConnection -ComputerName 127.0.0.1 -Port 8765 -WarningAction SilentlyContinue -InformationLevel Quiet
    if ($pre) { throw "127.0.0.1:8765 は既に bind 済みです。他の ComfyDir プロセスを終了してから再実行してください。" }
} catch [System.Net.Sockets.SocketException] {
    # 期待: 接続失敗 → スルー
}

Write-Host "starting launcher.py ..."
$proc = Start-Process -FilePath $pyw -ArgumentList "`"$launcher`"" -PassThru -WindowStyle Hidden

$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $r = Test-NetConnection -ComputerName 127.0.0.1 -Port 8765 -WarningAction SilentlyContinue -InformationLevel Quiet
        if ($r) { $ok = $true; break }
    } catch { }
}

if (-not $ok) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    throw "launcher did not bind 127.0.0.1:8765 within 15s"
}

# /manifest.json が 200 を返すか確認 (Phase 1 のルート)
try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8765/manifest.json" -TimeoutSec 5
    if ($resp.StatusCode -ne 200) {
        throw ("/manifest.json returned status {0}" -f $resp.StatusCode)
    }
} catch {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    throw "manifest.json check failed: $_"
}

Write-Host ("smoke OK (pid={0}, manifest.json=200)" -f $proc.Id)
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
exit 0
