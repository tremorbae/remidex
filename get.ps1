# Gets RemiDex and installs it on this Windows PC. Paste this in PowerShell:
#
#   irm https://raw.githubusercontent.com/tremorbae/Remidex/main/get.ps1 | iex
#
# It offers to install what RemiDex needs (Python) if it's missing, downloads the latest RemiDex into
# %USERPROFILE%\RemiDex (replacing an older copy there) and runs its installer, which asks before installing anything.
# Run it again to update, or to sign in again: your sign-in and switches stay.

function Get-RemiDex {
    $ErrorActionPreference = "Stop"
    $Repo = "tremorbae/Remidex"
    $ZipUrl = if ($env:REMIDEX_ZIP_URL) { $env:REMIDEX_ZIP_URL } else { "https://github.com/$Repo/archive/refs/heads/main.zip" }
    $Dest = Join-Path $HOME "RemiDex"

    function Refresh-Path {  # (what an install just added, without opening a new window)
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    }
    function Find-Python {  # py, or a python that really runs (not the Microsoft Store's stand-in)
        if (Get-Command py -ErrorAction SilentlyContinue) { return "py" }
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($python -and $python.Source -notlike "*WindowsApps*") { return "python" }
        return $null
    }
    function Offer($What, $WingetId, $Where) {  # installs it with winget, if you say yes
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Write-Host "RemiDex needs $What. Install it from $Where, then paste the RemiDex line again."
            return $false
        }
        $answer = Read-Host "RemiDex needs $What. Install it now? [y/N]"
        if ($answer -notmatch '^[Yy]') {
            Write-Host "Install $What from $Where, then paste the RemiDex line again."
            return $false
        }
        winget install -e --id $WingetId --accept-package-agreements --accept-source-agreements
        Refresh-Path
        return $true
    }

    $python = Find-Python
    if (-not $python) {
        if (-not (Offer "Python" "Python.Python.3.12" "python.org (tick 'Add python.exe to PATH')")) { return }
        $python = Find-Python
        if (-not $python) { Write-Host "Python is installed: close this window, open PowerShell again and paste the RemiDex line again."; return }
    }
    if ((Test-Path $Dest) -and -not (Test-Path (Join-Path $Dest "install_windows.py"))) {
        Write-Host "There's already a folder at $Dest that isn't RemiDex. Rename or move it, then paste the RemiDex line again."
        return
    }

    Write-Host "Downloading RemiDex..."
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("remidex-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $ProgressPreference = "SilentlyContinue"  # (the progress bar makes downloads very slow in Windows PowerShell)
        Invoke-WebRequest -Uri $ZipUrl -OutFile (Join-Path $tmp "remidex.zip") -UseBasicParsing
        Expand-Archive -Path (Join-Path $tmp "remidex.zip") -DestinationPath (Join-Path $tmp "unzipped")
        if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
        Move-Item (Get-ChildItem (Join-Path $tmp "unzipped") | Select-Object -First 1).FullName $Dest
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
    Push-Location $Dest
    try {
        if ($python -eq "py") { & py -3 install_windows.py @args } else { & python install_windows.py @args }
    } finally {
        Pop-Location
    }
}

Get-RemiDex @args
