param([string]$ApiUrl = "", [string]$StateDir = "")
$ErrorActionPreference = "Stop"
$GridGpuSourceDir = $PSScriptRoot
$GridGpuEnvironment = Join-Path $GridGpuSourceDir ".venv-agent"
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -c 'import sys; assert sys.version_info >= (3, 11), "GridGPU requires Python 3.11 or newer"'
    if ($LASTEXITCODE -ne 0) { throw "Python 3.11 or newer is required" }
    & py -3 -m venv --system-site-packages $GridGpuEnvironment
} else {
    & python -c 'import sys; assert sys.version_info >= (3, 11), "GridGPU requires Python 3.11 or newer"'
    if ($LASTEXITCODE -ne 0) { throw "Python 3.11 or newer is required" }
    & python -m venv --system-site-packages $GridGpuEnvironment
}
if ($LASTEXITCODE -ne 0) { throw "Could not create the GridGPU environment" }
$GridGpuPython = Join-Path $GridGpuEnvironment "Scripts\python.exe"
& $GridGpuPython -m pip install $GridGpuSourceDir
if ($LASTEXITCODE -ne 0) { throw "Could not install GridGPU" }
$GridGpuArguments = @("-m", "gridgpu", "join")
if ($ApiUrl) { $GridGpuArguments += @("--api-url", $ApiUrl) }
if ($StateDir) { $GridGpuArguments += @("--state-dir", $StateDir) }
& $GridGpuPython @GridGpuArguments
exit $LASTEXITCODE
