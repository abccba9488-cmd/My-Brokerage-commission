$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "============================================"
Write-Host "  籌碼流向分析系統 - 執行每日報表"
Write-Host "============================================"
Write-Host ""

& ".\.venv\Scripts\python.exe" "run_daily.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[錯誤] 執行失敗，請檢查上面的錯誤訊息。" -ForegroundColor Red
    Read-Host "按 Enter 關閉"
    exit 1
}

$reportDate = Get-Date -Format "yyyy-MM-dd"
$mdPath = Join-Path $PSScriptRoot "reports\$reportDate.md"
$csvPath = Join-Path $PSScriptRoot "reports\$reportDate.csv"

Write-Host ""
if (Test-Path $mdPath) {
    Write-Host "執行完成，正在開啟報表..."
    Invoke-Item $mdPath
    if (Test-Path $csvPath) { Invoke-Item $csvPath }
} else {
    Write-Host "找不到今天的報表，開啟 reports 資料夾讓你自己找。"
    Invoke-Item (Join-Path $PSScriptRoot "reports")
}

Write-Host ""
Read-Host "按 Enter 關閉"
