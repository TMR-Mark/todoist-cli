param(
    [string]$CommandName = "tod",
    [string]$TargetDir = "",
    [string]$ScriptName = "todoist_rich.py",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Get-FirstWritablePathDir {
    $pathDirs = ($env:Path -split ';') | Where-Object { $_ -and (Test-Path $_) }
    foreach ($dir in $pathDirs) {
        try {
            $testFile = Join-Path $dir ".__tod_write_test__"
            "test" | Set-Content -Path $testFile -Encoding Ascii -Force
            Remove-Item -Path $testFile -Force
            return $dir
        } catch {
            continue
        }
    }
    return $null
}

 $repoDir = Split-Path -Parent $PSCommandPath
 $scriptPath = Join-Path $repoDir $ScriptName
if (-not (Test-Path $scriptPath)) {
    throw "Cannot find $ScriptName at: $scriptPath"
}

if ([string]::IsNullOrWhiteSpace($TargetDir)) {
    $condaScripts = Join-Path $env:USERPROFILE "anaconda3\Scripts"
    if (Test-Path $condaScripts) {
        $TargetDir = $condaScripts
    } else {
        $TargetDir = Get-FirstWritablePathDir
    }
}

if ([string]::IsNullOrWhiteSpace($TargetDir) -or -not (Test-Path $TargetDir)) {
    throw "Could not determine a writable install directory on PATH. Pass -TargetDir explicitly."
}

$shimPath = Join-Path $TargetDir ("{0}.cmd" -f $CommandName)
if ((Test-Path $shimPath) -and -not $Force) {
    throw "Shim already exists at $shimPath. Re-run with -Force to overwrite."
}

$shim = @"
@echo off
setlocal
python "$scriptPath" %*
"@

$shim | Set-Content -Path $shimPath -Encoding Ascii -Force

Write-Host "Installed '$CommandName' shim -> $shimPath" -ForegroundColor Green
Write-Host "Runs: python $scriptPath" -ForegroundColor DarkGray
