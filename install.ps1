# 把插件库管理器安装到本机所有 Blender 版本的用户插件目录。
# 无需管理员权限；不会动 Blender 安装目录。
#
# 用法（PowerShell）:
#   powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"

$pkgName = "bl_plugin_manager"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $repoRoot $pkgName

if (-not (Test-Path $source)) {
    Write-Error "未找到源目录: $source"
    exit 1
}

$base = Join-Path $env:APPDATA "Blender Foundation\Blender"
if (-not (Test-Path $base)) {
    Write-Error "未找到 Blender 用户配置目录: $base"
    exit 1
}

$versions = Get-ChildItem -Path $base -Directory | Where-Object { $_.Name -match '^\d+\.\d+$' }
if (-not $versions) {
    Write-Error "在 $base 下没有发现版本目录"
    exit 1
}

foreach ($v in $versions) {
    $addons = Join-Path $v.FullName "scripts\addons"
    New-Item -ItemType Directory -Force -Path $addons | Out-Null
    $dest = Join-Path $addons $pkgName

    $stage = Join-Path $addons ("." + $pkgName + ".stage." + [guid]::NewGuid().ToString("N"))
    $previous = Join-Path $addons ($pkgName + ".previous")
    try {
        Copy-Item -Recurse -Force $source $stage
        $compile = Get-Command python -ErrorAction SilentlyContinue
        if ($compile) {
            & $compile.Source -m compileall -q $stage
            if ($LASTEXITCODE -ne 0) { throw "Python compile check failed" }
        }
        $stagePyc = Join-Path $stage "__pycache__"
        if (Test-Path $stagePyc) { Remove-Item -Recurse -Force $stagePyc }
        if (Test-Path $previous) { Remove-Item -Recurse -Force $previous }
        if (Test-Path $dest) { Move-Item -Force $dest $previous }
        Move-Item -Force $stage $dest
    } catch {
        if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
        if ((-not (Test-Path $dest)) -and (Test-Path $previous)) { Move-Item -Force $previous $dest }
        throw "安装 Blender $($v.Name) 失败，已尝试回滚：$($_.Exception.Message)"
    }

    Write-Host "已安装到 Blender $($v.Name): $dest"
}

Write-Host ""
Write-Host "完成。启动 Blender 后在 编辑 > 偏好设置 > 插件 中搜索 'Plugin Library' 勾选启用。"
Write-Host "启用后到 3D 视图右侧边栏 (按 N) 的 '插件库' 标签中使用。"
