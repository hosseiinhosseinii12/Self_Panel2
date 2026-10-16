# ===========================================================================
# revert_to_4.ps1 - Revert project to 4 commits ago
# ===========================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  REVERT PROJECT - Back to 4 Commits Ago" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""

# ---------- Show last 6 commits ----------
Write-Host "[INFO] Last 6 commits:" -ForegroundColor Yellow
git log --oneline -6
Write-Host ""

# ---------- Confirm ----------
Write-Host "This will revert to:" -ForegroundColor Yellow
$target = git log --oneline -5 | Select-Object -Last 1
Write-Host "  $target" -ForegroundColor White
Write-Host ""
Write-Host "WARNING: This discards:" -ForegroundColor Red
Write-Host "  - Modern admin UI" -ForegroundColor Red
Write-Host "  - All extended features (autoreply, scheduler, rewards, ...)" -ForegroundColor Red
Write-Host "  - Session Vault improvements" -ForegroundColor Red
Write-Host ""
$answer = Read-Host "Type 'yes' to continue, anything else to cancel"

if ($answer -ne "yes") {
    Write-Host "Cancelled." -ForegroundColor Yellow
    exit 0
}

# ---------- Hard reset to 4 commits back ----------
Write-Host ""
Write-Host "[1/3] Resetting to 4 commits back..." -ForegroundColor Yellow
git reset --hard HEAD~4

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] git reset failed" -ForegroundColor Red
    exit 1
}

# ---------- Force push ----------
Write-Host ""
Write-Host "[2/3] Force-pushing to origin..." -ForegroundColor Yellow
git push origin main --force

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] git push failed" -ForegroundColor Red
    exit 1
}

# ---------- Done ----------
Write-Host ""
Write-Host "[3/3] Showing new HEAD..." -ForegroundColor Yellow
git log --oneline -3
Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host "  DONE - Reverted to 4 commits ago" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Railway will rebuild automatically." -ForegroundColor Cyan
Write-Host ""