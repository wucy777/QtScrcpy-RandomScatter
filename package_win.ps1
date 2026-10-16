# Assemble a runnable QtScrcpy package.
#
# Why this exists instead of calling upstream ci/win/publish_for_win.bat:
#   1. That .bat is UTF-8 but cmd.exe reads it as the ANSI codepage (CP936 on
#      the runner here), so its Chinese comments turn into garbage and the
#      script dies with "'制要发布的包' is not recognized as an internal or
#      external command".
#   2. It calls Unix `cp`, which is not on PATH in a plain cmd step.
#   3. `cp`/`xcopy` failures do not stop it, so it can silently produce an
#      incomplete package.
# Doing the same work in PowerShell is encoding-safe and fails loudly.
#
# Usage:
#   pwsh -File package_win.ps1 -RepoRoot <checkout> -Arch x64 -OutDir <dir>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][ValidateSet('x64', 'x86')][string]$Arch,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$QtDir = $env:ENV_QT_PATH
)

$ErrorActionPreference = 'Stop'

function Step($msg) { Write-Host "==> $msg" }

$repoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$srcDir = Join-Path $repoRoot 'src'          # the QtScrcpy source tree
$coreDir = Join-Path $srcDir 'QtScrcpy\QtScrcpyCore'
$outDir = $OutDir
if (-not [System.IO.Path]::IsPathRooted($outDir)) {
    $outDir = Join-Path (Get-Location).Path $outDir
}

$qtArchDir = if ($Arch -eq 'x86') { 'msvc2019' } else { 'msvc2019_64' }
$qtBin = Join-Path $QtDir "$qtArchDir\bin"
$releaseDir = Join-Path $srcDir "output\$Arch\RelWithDebInfo"

Write-Host "repo        : $repoRoot"
Write-Host "source      : $srcDir"
Write-Host "core        : $coreDir"
Write-Host "qt bin      : $qtBin"
Write-Host "release dir : $releaseDir"
Write-Host "out dir     : $outDir"

# ------------------------------------------------------------------ checks
Step 'checking inputs'
if (-not (Test-Path -LiteralPath $releaseDir)) {
    throw "build output not found: $releaseDir"
}
$exe = Join-Path $releaseDir 'QtScrcpy.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    throw "QtScrcpy.exe not found in $releaseDir"
}
if (-not (Test-Path -LiteralPath $qtBin)) {
    throw "Qt bin dir not found: $qtBin"
}

# ------------------------------------------------------------------ copy
Step 'creating the package directory'
if (Test-Path -LiteralPath $outDir) { Remove-Item -LiteralPath $outDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Step 'copying the build output'
Copy-Item -Path (Join-Path $releaseDir '*') -Destination $outDir -Recurse -Force

Step 'copying adb / scrcpy-server'
$adbDir = Join-Path $coreDir 'src\third_party\adb\win'
foreach ($f in @('adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll')) {
    $p = Join-Path $adbDir $f
    if (Test-Path -LiteralPath $p) {
        Copy-Item -LiteralPath $p -Destination $outDir -Force
    } else {
        Write-Host "   [warn] missing $f"
    }
}
$server = Join-Path $coreDir 'src\third_party\scrcpy-server'
if (Test-Path -LiteralPath $server) {
    Copy-Item -LiteralPath $server -Destination (Join-Path $outDir 'scrcpy-server') -Force
} else {
    throw "scrcpy-server not found: $server"
}

Step 'copying keymap / config'
foreach ($pair in @(@('keymap', 'keymap'), @('config', 'config'))) {
    $from = Join-Path $srcDir $pair[0]
    $to = Join-Path $outDir $pair[1]
    if (Test-Path -LiteralPath $from) {
        New-Item -ItemType Directory -Force -Path $to | Out-Null
        Copy-Item -Path (Join-Path $from '*') -Destination $to -Recurse -Force
    }
}

# The tuned keymap goes FLAT into keymap/, not into a keymap-tuned/ subfolder.
# QtScrcpy's script dropdown uses QDir::entryInfoList(..., QDir::Files) with no
# recursion (ui/dialog.cpp:1379), so anything in a subdirectory is invisible
# and cannot be selected. Keeping it flat also means initializeKeyMapDirectory()
# copies it into the user's AppData keymap folder on first run.
Step 'installing the tuned keymap'
$tunedSrc = Join-Path $repoRoot 'keymap'
$keymapDst = Join-Path $outDir 'keymap'
if (Test-Path -LiteralPath $tunedSrc) {
    New-Item -ItemType Directory -Force -Path $keymapDst | Out-Null
    $tuned = Get-ChildItem -LiteralPath $tunedSrc -Filter *.json -File
    if (-not $tuned) { throw "no .json files in $tunedSrc" }
    foreach ($f in $tuned) {
        Copy-Item -LiteralPath $f.FullName -Destination $keymapDst -Force
        Write-Host "   $($f.Name)"
    }
} else {
    Write-Host '   [warn] no keymap/ folder in this repo; shipping upstream defaults only'
}

# ------------------------------------------------------------------ deploy Qt
Step 'running windeployqt'
$env:Path = "$qtBin;$env:Path"
$deploy = Join-Path $qtBin 'windeployqt.exe'
if (-not (Test-Path -LiteralPath $deploy)) { throw "windeployqt.exe not found in $qtBin" }
& $deploy (Join-Path $outDir 'QtScrcpy.exe')
if ($LASTEXITCODE -ne 0) { throw "windeployqt failed with exit code $LASTEXITCODE" }

Step 'trimming redundant Qt plugins'
foreach ($d in @('iconengines', 'translations')) {
    $p = Join-Path $outDir $d
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force }
}
$imgDir = Join-Path $outDir 'imageformats'
if (Test-Path -LiteralPath $imgDir) {
    # screenshot support needs qjpeg; drop the rest
    foreach ($f in @('qgif.dll', 'qicns.dll', 'qico.dll', 'qtga.dll',
                     'qtiff.dll', 'qwbmp.dll', 'qwebp.dll')) {
        $p = Join-Path $imgDir $f
        if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }
    }
}
foreach ($f in @('vc_redist.x86.exe', 'vc_redist.x64.exe')) {
    $p = Join-Path $outDir $f
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }
}

# ------------------------------------------------------------------ runtime
Step 'copying the MSVC runtime DLLs'
$sysDir = if ($Arch -eq 'x86') { "$env:WINDIR\SysWOW64" } else { "$env:WINDIR\System32" }
$runtime = if ($Arch -eq 'x86') {
    @('msvcp140.dll', 'vcruntime140.dll')
} else {
    @('msvcp140.dll', 'msvcp140_1.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
}
foreach ($f in $runtime) {
    $p = Join-Path $sysDir $f
    if (Test-Path -LiteralPath $p) {
        Copy-Item -LiteralPath $p -Destination (Join-Path $outDir $f) -Force
    } else {
        Write-Host "   [warn] runtime not found: $p"
    }
}

# ------------------------------------------------------------------ verify
Step 'verifying the package'
$required = @('QtScrcpy.exe', 'adb.exe', 'scrcpy-server', 'keymap', 'config',
              'Qt5Core.dll', 'Qt5Gui.dll', 'Qt5Widgets.dll')
$missing = @()
foreach ($r in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $outDir $r))) { $missing += $r }
}
if ($missing.Count -gt 0) {
    throw "package is incomplete, missing: $($missing -join ', ')"
}

$size = (Get-ChildItem -LiteralPath $outDir -Recurse -File |
         Measure-Object -Property Length -Sum).Sum
Write-Host ("   package OK: {0} files, {1:N1} MB" -f
            (Get-ChildItem -LiteralPath $outDir -Recurse -File).Count, ($size / 1MB))
Write-Host "PACKAGE OK -> $outDir"
