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

    if (Test-Path $dest) {
        Remove-Item -Recurse -Force $dest
    }
    Copy-Item -Recurse -Force $source $dest
    # 清掉缓存，避免旧字节码生效
    $pyc = Join-Path $dest "__pycache__"
    if (Test-Path $pyc) { Remove-Item -Recurse -Force $pyc }

    Write-Host "已安装到 Blender $($v.Name): $dest"
}

Write-Host ""
Write-Host "完成。启动 Blender 后在 编辑 > 偏好设置 > 插件 中搜索 'Plugin Library' 勾选启用。"
Write-Host "启用后到 3D 视图右侧边栏 (按 N) 的 '插件库' 标签中使用。"
