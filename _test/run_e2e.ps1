[CmdletBinding()]
param([string]$BlenderExe = $env:BLENDER_EXE)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($BlenderExe)) {
    $BlenderExe = @(
        "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
        "C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        "C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($BlenderExe) -or -not (Test-Path -LiteralPath $BlenderExe)) {
    throw "Blender executable not found. Set BLENDER_EXE or pass -BlenderExe."
}
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("pmlib_e2e_" + [guid]::NewGuid().ToString("N"))
$config = Join-Path $tempRoot "config"
$scripts = Join-Path $tempRoot "scripts"
$scratch = Join-Path $tempRoot "tmp"
$outLog = Join-Path $tempRoot "e2e.log"
$errLog = Join-Path $tempRoot "e2e.err"
try {
    New-Item -ItemType Directory -Force -Path $config, $scripts, $scratch, (Join-Path $scripts "addons") | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo "bl_plugin_manager") -Destination (Join-Path $scripts "addons\bl_plugin_manager") -Recurse
    $oldConfig = $env:BLENDER_USER_CONFIG; $oldScripts = $env:BLENDER_USER_SCRIPTS
    $oldTemp = $env:TEMP; $oldTmp = $env:TMP
    $oldMachineConfig = $env:BL_PLUGIN_MANAGER_MACHINE_CONFIG
    $oldLocalState = $env:BL_PLUGIN_MANAGER_LOCAL_STATE
    $oldExpectedRoot = $env:BL_PLUGIN_MANAGER_EXPECTED_TEST_ROOT
    $env:BLENDER_USER_CONFIG = $config; $env:BLENDER_USER_SCRIPTS = $scripts
    $env:TEMP = $scratch; $env:TMP = $scratch
    $env:BL_PLUGIN_MANAGER_MACHINE_CONFIG = Join-Path $tempRoot "machine.json"
    $env:BL_PLUGIN_MANAGER_LOCAL_STATE = Join-Path $tempRoot "local-state.json"
    $env:BL_PLUGIN_MANAGER_EXPECTED_TEST_ROOT = $tempRoot
    $proc = Start-Process -FilePath $BlenderExe -ArgumentList @(
        "--background", "--factory-startup", "--python-exit-code", "1",
        "--python", (Join-Path $repo "_test\test_e2e.py")
    ) -Wait -PassThru -NoNewWindow -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    Get-Content -LiteralPath $outLog, $errLog -ErrorAction SilentlyContinue |
        Select-String -Pattern '^\[PASS\]|^\[FAIL\]|===SUMMARY===|^\{|Traceback|^  File |^[A-Za-z]*Error' |
        ForEach-Object { $_.Line }
    if ($proc.ExitCode -ne 0) { throw "Blender E2E process failed with exit code $($proc.ExitCode)." }
    $summaryLines = @(Select-String -LiteralPath $outLog -Pattern '^\{"total"' | ForEach-Object { $_.Line })
    if ($summaryLines.Count -eq 0) { throw "E2E suite did not emit a JSON summary." }
    $summary = $summaryLines[-1] | ConvertFrom-Json
    if ($summary.failed -ne 0) { throw "E2E suite reported $($summary.failed) failed checks." }
    Write-Host "E2E suite passed: $($summary.total) checks."
}
finally {
    if ($null -ne $oldConfig) { $env:BLENDER_USER_CONFIG = $oldConfig } else { Remove-Item Env:BLENDER_USER_CONFIG -ErrorAction SilentlyContinue }
    if ($null -ne $oldScripts) { $env:BLENDER_USER_SCRIPTS = $oldScripts } else { Remove-Item Env:BLENDER_USER_SCRIPTS -ErrorAction SilentlyContinue }
    if ($null -ne $oldTemp) { $env:TEMP = $oldTemp } else { Remove-Item Env:TEMP -ErrorAction SilentlyContinue }
    if ($null -ne $oldTmp) { $env:TMP = $oldTmp } else { Remove-Item Env:TMP -ErrorAction SilentlyContinue }
    if ($null -ne $oldMachineConfig) { $env:BL_PLUGIN_MANAGER_MACHINE_CONFIG = $oldMachineConfig } else { Remove-Item Env:BL_PLUGIN_MANAGER_MACHINE_CONFIG -ErrorAction SilentlyContinue }
    if ($null -ne $oldLocalState) { $env:BL_PLUGIN_MANAGER_LOCAL_STATE = $oldLocalState } else { Remove-Item Env:BL_PLUGIN_MANAGER_LOCAL_STATE -ErrorAction SilentlyContinue }
    if ($null -ne $oldExpectedRoot) { $env:BL_PLUGIN_MANAGER_EXPECTED_TEST_ROOT = $oldExpectedRoot } else { Remove-Item Env:BL_PLUGIN_MANAGER_EXPECTED_TEST_ROOT -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
