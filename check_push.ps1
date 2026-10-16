# ===========================================================================
# check_push.ps1 - Compare local vs remote, then push if needed
# ===========================================================================

$ErrorActionPreference = "Continue"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "=================================================================" -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host "=================================================================" -ForegroundColor Cyan
}

function Write-OK    { param([string]$Msg) Write-Host "[OK]   $Msg" -ForegroundColor Green }
function Write-WARN  { param([string]$Msg) Write-Host "[WARN] $Msg" -ForegroundColor Yellow }
function Write-ERR   { param([string]$Msg) Write-Host "[ERR]  $Msg" -ForegroundColor Red }
function Write-INFO  { param([string]$Msg) Write-Host "[INFO] $Msg" -ForegroundColor White }

Write-Section "Git Push Diagnostic"

# ---------- 1. Check we're in a git repo ----------
if (-not (Test-Path ".git")) {
    Write-ERR "Not a git repository. Abort."
    exit 1
}
Write-OK "In git repo: $((Get-Location).Path)"

# ---------- 2. Show remotes ----------
Write-Section "Remote Configuration"
git remote -v
if ($LASTEXITCODE -ne 0) {
    Write-ERR "No remote configured."
    exit 1
}

# ---------- 3. Current branch ----------
Write-Section "Current Branch"
$branch = git rev-parse --abbrev-ref HEAD
Write-INFO "Branch: $branch"

# ---------- 4. Fetch from remote ----------
Write-Section "Fetching from remote"
git fetch origin 2>&1 | ForEach-Object { Write-Host "  $_" }

# ---------- 5. Compare local vs remote ----------
Write-Section "Local vs Remote ($branch)"

$local  = git rev-parse HEAD
$remote = git rev-parse "origin/$branch" 2>$null

Write-INFO "Local  HEAD: $local"
Write-INFO "Remote HEAD: $remote"

if ($local -eq $remote) {
    Write-OK "Local and remote are IDENTICAL"
} else {
    Write-WARN "Local and remote DIFFER"

    # check if local is ahead
    $ahead  = git rev-list --count "origin/$branch..HEAD"
    $behind = git rev-list --count "HEAD..origin/$branch"

    Write-INFO "Local is ahead by:  $ahead commits"
    Write-INFO "Local is behind by: $behind commits"

    Write-Host ""
    Write-Host "  Commits LOCAL has but remote does NOT:" -ForegroundColor Yellow
    git log "origin/$branch..HEAD" --oneline

    Write-Host ""
    Write-Host "  Commits REMOTE has but local does NOT:" -ForegroundColor Yellow
    git log "HEAD..origin/$branch" --oneline
}

# ---------- 6. Show last 5 commits on both ----------
Write-Section "Last 5 commits (local)"
git log --oneline -5

Write-Section "Last 5 commits (remote)"
git log "origin/$branch" --oneline -5 2>$null

# ---------- 7. Uncommitted changes? ----------
Write-Section "Working Tree Status"
$status = git status --porcelain
if ([string]::IsNullOrWhiteSpace($status)) {
    Write-OK "Working tree is clean"
} else {
    Write-WARN "You have uncommitted changes:"
    git status --short
}

# ---------- 8. Ask user what to do ----------
Write-Section "Actions"

Write-Host "What do you want to do?" -ForegroundColor Cyan
Write-Host "  [1] Commit all changes (if any)" -ForegroundColor White
Write-Host "  [2] Push to origin (normal)" -ForegroundColor White
Write-Host "  [3] Force push to origin" -ForegroundColor White
Write-Host "  [4] Just exit" -ForegroundColor White
Write-Host ""

$choice = Read-Host "Enter your choice (1-4)"

switch ($choice) {
    "1" {
        $msg = Read-Host "Commit message"
        if ([string]::IsNullOrWhiteSpace($msg)) {
            $msg = "update from " + (Get-Date -Format "yyyy-MM-dd HH:mm")
        }
        git add -A
        git commit -m $msg
        if ($LASTEXITCODE -eq 0) {
            Write-OK "Committed"
        } else {
            Write-WARN "Nothing to commit or commit failed"
        }
        Write-Host ""
        Write-Host "Now run this script again to push." -ForegroundColor Yellow
    }
    "2" {
        Write-INFO "Pushing to origin/$branch ..."
        git push origin $branch
        if ($LASTEXITCODE -eq 0) {
            Write-OK "Pushed successfully"
        } else {
            Write-ERR "Push failed. Try option 3 (force push) if you know what you're doing."
        }
    }
    "3" {
        Write-WARN "About to FORCE push. This rewrites remote history."
        $confirm = Read-Host "Type 'yes' to confirm"
        if ($confirm -eq "yes") {
            Write-INFO "Force pushing to origin/$branch ..."
            git push origin $branch --force
            if ($LASTEXITCODE -eq 0) {
                Write-OK "Force pushed successfully"
            } else {
                Write-ERR "Force push failed"
            }
        } else {
            Write-INFO "Cancelled"
        }
    }
    default {
        Write-INFO "Exiting without changes"
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host ""