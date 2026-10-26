<#
Repository-local entry for the integrated R3/Doubao test build (Python 3.11).
RealDrone authorizes takeoff requests; starting this script never takes off.
CheckOnly performs offline dependency/config checks, without cloud/audio/drone IO.
#>
param(
    [int]$InputDevice = -1,
    [int]$OutputDevice = -1,
    [switch]$RealDrone,
    [switch]$ListDevices,
    [switch]$CheckOnly,
    [switch]$NoAutoClimb,
    [ValidateRange(80,160)][int]$ClimbTargetCm = 120,
    [ValidateSet('facing_pilot','aircraft')][string]$DirectionFrame = 'facing_pilot'
)

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
$config = Join-Path $project 'modules\follow\configs\aircraft_A_ID0_motion_follow.json'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create the Python 3.11 .venv and install requirements.txt first. See README.md.'
}
& $python -X utf8 -B -c "import sys; assert sys.version_info[:2] == (3,11), 'Python 3.11 required'; import cv2, numpy, djitellopy, sounddevice, soxr, websockets; import importlib.util; assert importlib.util.find_spec('pynput'), 'pynput required'; print('Python 3.11 and runtime dependencies OK')"
if ($LASTEXITCODE -ne 0) { exit 1 }
if ($ListDevices) {
    & $python -X utf8 -B -c "import sys; sys.path.insert(0, sys.argv[1]); import sounddevice; from audio_devices import list_audio_devices; list_audio_devices(sounddevice)" (Join-Path $project 'modules\ai')
    exit $LASTEXITCODE
}
if (-not $CheckOnly -and ($InputDevice -lt 0 -or $OutputDevice -lt 0)) {
    throw 'Run -ListDevices first, then pass both -InputDevice and -OutputDevice. Select the actual headset, not a default mapper.'
}

$savedLocation = Get-Location
$savedEnvironment = @{}
foreach ($name in @('PYTHONUTF8','PYTHONIOENCODING','R3_FOLLOW_CONFIG','R3_VOICE_LATERAL_FRAME','VOLC_API_KEY')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$result = 1
try {
    Set-Location -LiteralPath $project
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:R3_FOLLOW_CONFIG = $config
    $env:R3_VOICE_LATERAL_FRAME = $DirectionFrame
    & $python -X utf8 -B 'modules\follow\check_follow_config.py' --config $config
    if ($LASTEXITCODE -ne 0) { throw 'Follow configuration check failed.' }
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $logFile = Join-Path $project ('logs\r3_voice_' + $stamp + '.log')
    $launchArgs = @('modules\ai\main.py', '--volc-dialog', '--input-device', "$InputDevice",
                    '--output-device', "$OutputDevice", '--log-file', $logFile)
    if ($RealDrone) {
        $launchArgs += @('--real-drone', '--show-video', '--min-battery', '30', '--allow-real-takeoff')
        if ($NoAutoClimb) { $launchArgs += '--no-auto-climb' }
        else { $launchArgs += @('--climb-target-cm', "$ClimbTargetCm") }
    }
    if ($CheckOnly) {
        Write-Host 'CHECK ONLY: no audio recording, cloud connection or drone connection.'
        Write-Host ('Follow config: ' + $config)
        Write-Host ('Voice direction frame: ' + $DirectionFrame)
        ConvertTo-Json -InputObject $launchArgs -Compress
        $result = 0
    } else {
        if ($RealDrone) {
            $occupied = @(netstat -ano -p udp | Select-String ':8889\s|:8890\s|:11111\s')
            if ($occupied.Count) { throw 'Exit the previous drone program before starting another one.' }
            Write-Host 'REAL FLIGHT ENABLED. Takeoff only on your request; follow starts separately.' -ForegroundColor Yellow
            Write-Host 'Ctrl+Shift+Alt+A: hover. Ctrl+Shift+Alt+L: land.'
        } else {
            Write-Host 'VOICE TEST: simulated drone. No real drone connection.'
        }
        # Validate explicit endpoint selection without opening recording/playback streams.
        & $python -X utf8 -B -c "import sys; sys.path.insert(0, 'modules/ai'); import sounddevice; from audio_config import load_audio_config; from audio_devices import resolve_endpoints, describe_endpoints; describe_endpoints(resolve_endpoints(sounddevice, load_audio_config(input_device=int(sys.argv[1]), output_device=int(sys.argv[2]))))" "$InputDevice" "$OutputDevice"
        if ($LASTEXITCODE -ne 0) { throw 'Headset selection failed; no cloud or drone session started.' }
        if (-not $env:VOLC_API_KEY) {
            $secret = Read-Host 'VOLC API KEY (hidden; used in this process only)' -AsSecureString
            $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
            try { $env:VOLC_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer) }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer); $secret.Dispose() }
        }
        if (-not $env:VOLC_API_KEY) { throw 'No voice API key provided.' }
        Write-Host ('Follow config: ' + (Split-Path $config -Leaf))
        Write-Host ('Voice direction frame: ' + $DirectionFrame + '; turn default: 90 degrees.')
        & $python -X utf8 -B @launchArgs
        $result = $LASTEXITCODE
        Write-Host ('Log: ' + $logFile)
    }
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
    Set-Location -LiteralPath $savedLocation
}
exit $result
