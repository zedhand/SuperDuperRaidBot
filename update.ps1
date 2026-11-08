# Raid Bot updater. Run it by double-clicking Update.bat.
# Downloads the latest release from GitHub, checks its checksum, backs up the current code, and replaces
# ONLY the program files. Your settings (.env), data (raidbot.db), backups and icons are never touched.
param(
    [string]$ApiUrl = "https://api.github.com/repos/zedhand/SuperDuperRaidBot/releases/latest",
    [switch]$Yes,       # don't wait for Enter (used by tests)
    [switch]$SkipDeps,  # don't reinstall Python packages (used by tests)
    [switch]$Force,     # reinstall even if already up to date
    [switch]$AllowUnverified  # install even when GitHub gives no checksum to verify the download against
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Say($text) { Write-Host $text }

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    if (-not (Test-Path (Join-Path $here "bot.py"))) {
        throw "This doesn't look like a bot folder (no bot.py next to this script)."
    }

    $local = ""
    $m = Select-String -Path (Join-Path $here "bot.py") -Pattern '^VERSION = "(.+)"' | Select-Object -First 1
    if ($m) { $local = $m.Matches[0].Groups[1].Value }
    Say ("Installed version: " + $(if ($local) { $local } else { "unknown" }))

    Say "Checking for updates..."
    $headers = @{ "User-Agent" = "SuperDuperRaidBot-updater" }
    $rel = Invoke-RestMethod -Uri $ApiUrl -Headers $headers
    $remote = ([string]$rel.tag_name).TrimStart("v")
    Say "Latest release:   $remote"

    $newer = $true
    try { $newer = ([version]$remote -gt [version]$local) } catch { }
    if (-not $newer -and -not $Force) {
        Say "You're already up to date."
        exit 0
    }

    $asset = $rel.assets | Where-Object { $_.name -like "*.zip" } | Select-Object -First 1
    if (-not $asset) { throw "The latest release has no zip file attached." }

    if (-not $Yes) {
        Say ""
        Say "Close the bot window first. It can't be updated while it is running."
        Read-Host "Press Enter to update (or close this window to cancel)" | Out-Null
    }

    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("raidbot-update-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory $tmp | Out-Null
    $zip = Join-Path $tmp "release.zip"

    Say "Downloading $($asset.name)..."
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing -Headers $headers

    if (-not ($asset.digest -and ([string]$asset.digest) -like "sha256:*")) {
        # No checksum from GitHub: the download can't be verified, so don't install it silently.
        if ($AllowUnverified) { Say "WARNING: no checksum available; installing without verifying the download." }
        elseif (-not $Yes) {
            Say "GitHub did not give a checksum for this download, so it can't be verified."
            $answer = Read-Host "Type INSTALL to continue anyway, or just press Enter to cancel"
            if ($answer -ne "INSTALL") { throw "Cancelled: the download could not be verified. Nothing was changed." }
        }
        else { throw "GitHub did not give a checksum for this download, so it can't be verified. Nothing was changed." }
    }
    if ($asset.digest -and ([string]$asset.digest) -like "sha256:*") {
        $want = ([string]$asset.digest).Substring(7).ToLower()
        # .NET directly: Get-FileHash can be missing when Windows PowerShell is started from PowerShell 7
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $stream = [System.IO.File]::OpenRead($zip)
        try { $got = ([BitConverter]::ToString($sha.ComputeHash($stream)) -replace "-", "").ToLower() }
        finally { $stream.Dispose(); $sha.Dispose() }
        if ($want -ne $got) { throw "The download is corrupted (checksum mismatch). Nothing was changed." }
        Say "Checksum OK."
    }

    $extract = Join-Path $tmp "x"
    Add-Type -AssemblyName System.IO.Compression.FileSystem   # not Expand-Archive: same module-path problem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $extract)
    $src = Get-ChildItem $extract -Directory | Select-Object -First 1
    if (-not $src -or -not (Test-Path (Join-Path $src.FullName "bot.py"))) {
        throw "The download doesn't look like a bot release. Nothing was changed."
    }

    # Back up the current program files first.
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $old = $(if ($local) { $local } else { "old" })
    $backup = Join-Path $here ("backups\code-" + $old + "-" + $stamp)
    New-Item -ItemType Directory $backup -Force | Out-Null
    $code = @("bot.py", "checks.py", "db.py", "embeds.py", "wow_data.py", "setup.py", "start.bat",
              "requirements.txt", "README.md", "LICENSE", "CHANGELOG.md", "SECURITY.md", "Shortcuts.bat")
    foreach ($f in $code) {
        $p = Join-Path $here $f
        if (Test-Path $p) { Copy-Item $p $backup }
    }

    # Install the new program files.
    foreach ($f in $code) {
        $p = Join-Path $src.FullName $f
        if (Test-Path $p) { Copy-Item $p (Join-Path $here $f) -Force }
    }
    $iconsReadme = Join-Path $src.FullName "icons\README.txt"
    if (Test-Path $iconsReadme) {
        New-Item -ItemType Directory (Join-Path $here "icons") -Force | Out-Null
        Copy-Item $iconsReadme (Join-Path $here "icons\README.txt") -Force
    }
    foreach ($f in @("Update.bat", "update.ps1")) {  # the updater may update itself; ignore if locked
        try {
            $p = Join-Path $src.FullName $f
            if (Test-Path $p) { Copy-Item $p (Join-Path $here $f) -Force }
        } catch { }
    }

    $venvPython = Join-Path $here ".venv\Scripts\python.exe"
    if (-not $SkipDeps -and (Test-Path $venvPython)) {
        Say "Updating components..."
        & $venvPython -m pip install -q -r (Join-Path $here "requirements.txt")
    }

    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Say ""
    Say "Updated $local -> $remote."
    Say "Your settings (.env) and data (raidbot.db) were not touched."
    Say "The previous version is saved in: $backup"
    Say "Start the bot with start.bat."
}
catch {
    Write-Host ""
    Write-Host ("Update failed: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "If a backups\code-... folder was created, your previous program files are inside it."
    exit 1
}
