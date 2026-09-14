<#
.SYNOPSIS
    Run isolated Blender regression tests for the plugin library manager.

.DESCRIPTION
    The temporary Blender config and scripts directories keep this suite away
    from the user's actual add-ons, extensions, preferences, and library.
#>
[CmdletBinding()]
param(
    [string]$BlenderExe = $env:BLENDER_EXE
)

$ErrorActionPreference = "Stop"
$oldConfig = $env:BLENDER_USER_CONFIG
$oldScripts = $env:BLENDER_USER_SCRIPTS
$oldTemp = $env:TEMP
$oldTmp = $env:TMP
$oldMachineConfig = $env:BL_PLUGIN_MANAGER_MACHINE_CONFIG
$oldLocalState = $env:BL_PLUGIN_MANAGER_LOCAL_STATE
$oldExpectedRoot = $env:BL_PLUGIN_MANAGER_EXPECTED_TEST_ROOT
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path
if ([string]::IsNullOrWhiteSpace($BlenderExe)) {
    $candidates = @(
        "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
        "C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        "C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
    )
    $BlenderExe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($BlenderExe) -or -not (Test-Path -LiteralPath $BlenderExe)) {
    throw "Blender executable not found. Set BLENDER_EXE or pass -BlenderExe."
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("pmlib_regressions_" + [guid]::NewGuid().ToString("N"))
$config = Join-Path $tempRoot "config"
$scripts = Join-Path $tempRoot "scripts"
$scratch = Join-Path $tempRoot "tmp"
$log = Join-Path $tempRoot "regressions.log"
$addonTarget = Join-Path $scripts "addons\bl_plugin_manager"

try {
    New-Item -ItemType Directory -Force -Path $config, $scripts, $scratch, (Split-Path -Parent $addonTarget) | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo "bl_plugin_manager") -Destination $addonTarget -Recurse

    $env:BLENDER_USER_CONFIG = $config
    $env:BLENDER_USER_SCRIPTS = $scripts
    $env:TEMP = $scratch
    $env:TMP = $scratch
    $env:BL_PLUGIN_MANAGER_MACHINE_CONFIG = Join-Path $tempRoot "machine.json"
    $env:BL_PLUGIN_MANAGER_LOCAL_STATE = Join-Path $tempRoot "local-state.json"
    $env:BL_PLUGIN_MANAGER_EXPECTED_TEST_ROOT = $tempRoot
    $script = Join-Path $repo "_test\test_regressions.py"
    $proc = Start-Process -FilePath $BlenderExe -ArgumentList @(
        "--background", "--factory-startup", "--python-exit-code", "1", "--python", $script
    ) -Wait -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError ($log + ".err")

    Get-Content -LiteralPath $log, ($log + ".err") -ErrorAction SilentlyContinue |
        Select-String -Pattern '^\[PASS\]|^\[FAIL\]|===REGRESSION SUMMARY===|^\{|Traceback|^  File |^[A-Za-z]*Error' |
        ForEach-Object { $_.Line }

    if ($proc.ExitCode -ne 0) {
        throw "Blender regression process failed with exit code $($proc.ExitCode)."
    }
    $summaryLines = @(Select-String -LiteralPath $log -Pattern '^\{"total"' | ForEach-Object { $_.Line })
    if ($summaryLines.Count -eq 0) {
        throw "Regression suite did not emit a JSON summary."
    }
    $summary = $summaryLines[-1] | ConvertFrom-Json
    if ($summary.failed -ne 0) {
        throw "Regression suite reported $($summary.failed) failed checks."
    }
    Write-Host "Regression suite passed: $($summary.total) checks."
}
finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    if ($null -eq $oldConfig) {
        Remove-Item Env:BLENDER_USER_CONFIG -ErrorAction SilentlyContinue
    } else {
        $env:BLENDER_USER_CONFIG = $oldConfig
    }
    if ($null -eq $oldScripts) {
        Remove-Item Env:BLENDER_USER_SCRIPTS -ErrorAction SilentlyContinue
    } else {
        $env:BLENDER_USER_SCRIPTS = $oldScripts
    }
    if ($null -eq $oldTemp) { Remove-Item Env:TEMP -ErrorAction SilentlyContinue } else { $env:TEMP = $oldTemp }
    if ($null -eq $oldTmp) { Remove-Item Env:TMP -ErrorAction SilentlyContinue } else { $env:TMP = $oldTmp }
    if ($null -eq $oldMachineConfig) { Remove-Item Env:BL_PLUGIN_MANAGER_MACHINE_CONFIG -ErrorAction SilentlyContinue } else { $env:BL_PLUGIN_MANAGER_MACHINE_CONFIG = $oldMachineConfig }
    if ($null -eq $oldLocalState) { Remove-Item Env:BL_PLUGIN_MANAGER_LOCAL_STATE -ErrorAction SilentlyContinue } else { $env:BL_PLUGIN_MANAGER_LOCAL_STATE = $oldLocalState }
    if ($null -eq $oldExpectedRoot) { Remove-Item Env:BL_PLUGIN_MANAGER_EXPECTED_TEST_ROOT -ErrorAction SilentlyContinue } else { $env:BL_PLUGIN_MANAGER_EXPECTED_TEST_ROOT = $oldExpectedRoot }
}
