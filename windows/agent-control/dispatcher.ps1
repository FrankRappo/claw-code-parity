param(
    [Parameter(Mandatory)][string]$StateDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Add-Type -Path (Join-Path $PSScriptRoot 'ClawAgentConsole.cs')

$dispatcherPath = Join-Path $StateDir 'dispatcher.json'
$heartbeatPath = Join-Path $StateDir 'heartbeat.json'
$screenPath = Join-Path $StateDir 'screen.txt'
$snapshotPath = Join-Path $StateDir 'screen.json'
$turnStatePath = Join-Path $StateDir 'turn-state.json'
$activeTaskPath = Join-Path $StateDir 'active-task.json'
$interruptSignalPath = Join-Path $StateDir 'interrupt.signal'
$stopPath = Join-Path $StateDir 'dispatcher.stop'
$config = Get-ClawControlConfig -StateDir $StateDir
$hostState = Read-ClawJson -Path (Join-Path $StateDir 'host.json')
if (-not $hostState -or -not (Test-ClawProcess -ProcessId $hostState.pid)) {
    throw 'The visible Claw host process is not running.'
}
$hostProcessId = [uint32]$hostState.pid
$startedAt = (Get-Date).ToUniversalTime().ToString('o')

function Save-DispatcherState {
    param([string]$State, [AllowNull()][string]$ErrorMessage = $null)
    Write-ClawAtomicJson -Path $dispatcherPath -Value ([ordered]@{
        pid = $PID
        host_pid = $hostProcessId
        state = $State
        started_at = $startedAt
        updated_at = (Get-Date).ToUniversalTime().ToString('o')
        error = $ErrorMessage
    })
}

function Save-Snapshot {
    param($Snapshot)
    Write-ClawAtomicText -Path $screenPath -Content $Snapshot.Tail
    Write-ClawAtomicJson -Path $snapshotPath -Value ([ordered]@{
        captured_at = (Get-Date).ToUniversalTime().ToString('o')
        current_line = $Snapshot.CurrentLine
        cursor_x = $Snapshot.CursorX
        cursor_y = $Snapshot.CursorY
        width = $Snapshot.Width
    })
}

function Get-Snapshot {
    $snapshot = [ClawAgentControl.NativeConsole]::Capture($hostProcessId, 80)
    Save-Snapshot -Snapshot $snapshot
    return $snapshot
}

function Test-ClawIdle {
    param($Snapshot)
    $line = $Snapshot.CurrentLine.TrimEnd()
    if ($line -match $config.idle_prompt_regex) { return $true }
    # Claw's Windows rustyline renderer uses a VT-managed prompt. On some
    # ConHost versions the glyph is not exposed by ReadConsoleOutputCharacter,
    # but the cursor is reliably parked at column 2 for the empty `> ` prompt.
    return [string]::IsNullOrWhiteSpace($line) -and $Snapshot.CursorX -eq [int]$config.idle_cursor_x
}

function Get-SessionStamp {
    $path = [string]$config.resume_session_path
    if ([string]::IsNullOrWhiteSpace($path) -or -not (Test-Path -LiteralPath $path)) { return $null }
    $item = Get-Item -LiteralPath $path
    return [pscustomobject]@{
        path = $item.FullName
        last_write_utc_ticks = $item.LastWriteTimeUtc.Ticks
        length = $item.Length
    }
}

function Test-ControlledIdle {
    param($Snapshot)
    $turn = Read-ClawJson -Path $turnStatePath
    if ($turn -and $turn.state -eq 'active') {
        $stamp = Get-SessionStamp
        if ($stamp -and ($stamp.last_write_utc_ticks -ne [long]$turn.start_last_write_utc_ticks -or $stamp.length -ne [long]$turn.start_length)) {
            $turn.state = 'completed_or_stopped'
            $turn | Add-Member -NotePropertyName observed_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
            $turn | Add-Member -NotePropertyName end_last_write_utc_ticks -NotePropertyValue $stamp.last_write_utc_ticks -Force
            $turn | Add-Member -NotePropertyName end_length -NotePropertyValue $stamp.length -Force
            Write-ClawAtomicJson -Path $turnStatePath -Value $turn
            return $true
        }
        return $false
    }
    return Test-ClawIdle -Snapshot $Snapshot
}

function Start-ControlledTurn {
    param([string]$RequestId)
    $stamp = Get-SessionStamp
    if (-not $stamp) {
        Remove-Item -LiteralPath $turnStatePath -Force -ErrorAction SilentlyContinue
        return
    }
    Write-ClawAtomicJson -Path $turnStatePath -Value ([ordered]@{
        state = 'active'
        request_id = $RequestId
        session_path = $stamp.path
        submitted_at = (Get-Date).ToUniversalTime().ToString('o')
        start_last_write_utc_ticks = $stamp.last_write_utc_ticks
        start_length = $stamp.length
    })
}

function Request-ClawTurnInterrupt {
    Write-ClawAtomicText -Path $interruptSignalPath -Content ((Get-Date).ToUniversalTime().ToString('o'))
}

function Start-AutoContinueTask {
    param($Request)
    if ($config.auto_continue -ne $true) { return }
    $markerPath = if ($Request.PSObject.Properties.Name -contains 'completion_marker_path') {
        [string]$Request.completion_marker_path
    } else { '' }
    if ([string]::IsNullOrWhiteSpace($markerPath)) {
        throw 'Auto-continue instruction has no completion marker path.'
    }
    Write-ClawAtomicJson -Path $activeTaskPath -Value ([ordered]@{
        state = 'active'
        request_id = [string]$Request.id
        completion_marker_path = $markerPath
        continuations = 0
        last_submitted_at = (Get-Date).ToUniversalTime().ToString('o')
    })
}

function Invoke-AutoContinue {
    param($Snapshot)
    if ($config.auto_continue -ne $true) { return }
    $task = Read-ClawJson -Path $activeTaskPath
    if (-not $task -or $task.state -ne 'active') { return }

    $markerPath = [string]$task.completion_marker_path
    if (Test-Path -LiteralPath $markerPath) {
        $marker = (Get-Content -LiteralPath $markerPath -Raw -Encoding utf8).Trim()
        if ($marker -match '^COMPLETE\s+' -or $marker -match '^BLOCKED\s+') {
            $task.state = if ($marker -match '^COMPLETE\s+') { 'completed' } else { 'blocked' }
            $task | Add-Member -NotePropertyName marker -NotePropertyValue $marker -Force
            $task | Add-Member -NotePropertyName finished_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
            Write-ClawAtomicJson -Path $activeTaskPath -Value $task
        }
        return
    }
    if (-not (Test-ClawIdle -Snapshot $Snapshot)) { return }

    $lastSubmitted = [datetime]::Parse([string]$task.last_submitted_at).ToUniversalTime()
    if (((Get-Date).ToUniversalTime() - $lastSubmitted).TotalSeconds -lt [int]$config.auto_continue_delay_seconds) { return }
    if ([int]$task.continuations -ge [int]$config.auto_continue_max_turns) {
        $task.state = 'exhausted'
        $task | Add-Member -NotePropertyName finished_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
        Write-ClawAtomicJson -Path $activeTaskPath -Value $task
        return
    }

    $typed = ConvertTo-ClawSingleLine -Text (
        "Продолжай текущую задачу с места остановки. Один инструмент не означает завершение. " +
        "Не повторяй чтение и планирование; выполни следующий полезный инструмент. " +
        "Не пиши финальный ответ и не создавай completion marker, пока все изменения и проверки не закончены."
    )
    [ClawAgentControl.NativeConsole]::SendText($hostProcessId, $typed, $true)
    $task.continuations = [int]$task.continuations + 1
    $task.last_submitted_at = (Get-Date).ToUniversalTime().ToString('o')
    Write-ClawAtomicJson -Path $activeTaskPath -Value $task
}

function Wait-ClawIdle {
    param([int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (-not (Test-ClawProcess -ProcessId $hostProcessId)) { throw 'Claw host exited while waiting for its prompt.' }
        $snapshot = Get-Snapshot
        if (Test-ControlledIdle -Snapshot $snapshot) { return $snapshot }
        Start-Sleep -Milliseconds 150
    } while ((Get-Date) -lt $deadline)
    throw "Claw did not return to its input prompt within $TimeoutSeconds seconds. The queued message was not typed."
}

function Select-NextRequest {
    param([bool]$IsIdle)
    $requests = @(Get-ChildItem -LiteralPath (Join-Path $StateDir 'queue') -Filter '*.json' -File -ErrorAction SilentlyContinue | Sort-Object Name)
    if ($requests.Count -eq 0) { return $null }
    foreach ($file in $requests) {
        try {
            $request = Read-ClawJson -Path $file.FullName
            if ($request.mode -eq 'now' -or $IsIdle) {
                return [pscustomobject]@{ File = $file; Request = $request }
            }
        } catch {}
    }
    return $null
}

function Submit-Request {
    param($Selection, $InitialSnapshot)
    $source = $Selection.File.FullName
    $request = $Selection.Request
    $processing = Join-Path $StateDir "processing\$($Selection.File.Name)"
    Move-Item -LiteralPath $source -Destination $processing

    try {
        $snapshot = $InitialSnapshot
        $isIdle = Test-ControlledIdle -Snapshot $snapshot
        if ($request.mode -eq 'now' -and -not $isIdle) {
            Request-ClawTurnInterrupt
            $snapshot = Wait-ClawIdle -TimeoutSeconds ([int]$config.interrupt_timeout_seconds)
            $isIdle = $true
        }
        if (-not $isIdle) {
            $snapshot = Wait-ClawIdle -TimeoutSeconds ([int]$config.idle_timeout_seconds)
        }

        $typed = $null
        switch ($request.kind) {
            'instruction' {
                $operatorMessage = if (
                    $request.PSObject.Properties.Name -contains 'message' -and
                    -not [string]::IsNullOrWhiteSpace([string]$request.message)
                ) {
                    [string]$request.message
                } else {
                    throw 'Instruction queue record does not contain inline message text.'
                }
                $typed = ConvertTo-ClawSingleLine -Text (
                    'Это текущая задача и текущая сессия; не начинай заново. ' +
                    'У тебя есть все инструменты и agent mode включён. ' +
                    'Не отвечай обещанием или планом: сначала выполни полезное действие над задачей инструментом. ' +
                    $operatorMessage
                )
            }
            'raw' { $typed = ConvertTo-ClawSingleLine -Text ([string]$request.message) }
            'interrupt' { $typed = $null }
            default { throw "Unsupported request kind: $($request.kind)" }
        }

        if ($request.kind -eq 'interrupt') {
            if (Test-ControlledIdle -Snapshot $snapshot) {
                # The turn is already stopped. Do not signal an empty prompt.
            } else {
                Request-ClawTurnInterrupt
                Wait-ClawIdle -TimeoutSeconds ([int]$config.interrupt_timeout_seconds) | Out-Null
            }
        } elseif (-not [string]::IsNullOrWhiteSpace($typed)) {
            if ($request.kind -eq 'instruction') { Start-ControlledTurn -RequestId ([string]$request.id) }
            [ClawAgentControl.NativeConsole]::SendText($hostProcessId, $typed, $true)
            if ($request.kind -eq 'instruction') {
                $receiptPath = if (
                    $request.PSObject.Properties.Name -contains 'delivery_receipt_path' -and
                    -not [string]::IsNullOrWhiteSpace([string]$request.delivery_receipt_path)
                ) {
                    [string]$request.delivery_receipt_path
                } else {
                    Join-Path $StateDir "acks\$($request.id).txt"
                }
                # This is a controller-side delivery receipt, not model work.
                # Requiring the model to write an ACK as its first tool call made
                # one-tool agents treat that administrative action as task completion.
                Write-ClawAtomicText -Path $receiptPath -Content "DISPATCHED $($request.id)"
                Start-AutoContinueTask -Request $request
            }
        }

        $request.status = 'submitted_to_console'
        $request | Add-Member -NotePropertyName submitted_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
        $request | Add-Member -NotePropertyName typed_trigger -NotePropertyValue $typed -Force
        Write-ClawAtomicJson -Path $processing -Value $request
        Move-Item -LiteralPath $processing -Destination (Join-Path $StateDir "delivered\$($Selection.File.Name)") -Force
    }
    catch {
        $attempts = [int]$request.attempts + 1
        $request.attempts = $attempts
        $request.status = if ($attempts -ge 3) { 'failed' } else { 'queued' }
        $request | Add-Member -NotePropertyName last_error -NotePropertyValue $_.Exception.Message -Force
        $request | Add-Member -NotePropertyName last_attempt_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) -Force
        Write-ClawAtomicJson -Path $processing -Value $request
        $destination = if ($attempts -ge 3) { Join-Path $StateDir "failed\$($Selection.File.Name)" } else { Join-Path $StateDir "queue\$($Selection.File.Name)" }
        Move-Item -LiteralPath $processing -Destination $destination -Force
        Start-Sleep -Milliseconds 500
    }
}

Save-DispatcherState -State 'running'
try {
    while (-not (Test-Path -LiteralPath $stopPath) -and (Test-ClawProcess -ProcessId $hostProcessId)) {
        try {
            $snapshot = Get-Snapshot
            $selection = Select-NextRequest -IsIdle (Test-ControlledIdle -Snapshot $snapshot)
            if ($selection) { Submit-Request -Selection $selection -InitialSnapshot $snapshot }
            else { Invoke-AutoContinue -Snapshot $snapshot }
            Write-ClawAtomicJson -Path $heartbeatPath -Value ([ordered]@{
                pid = $PID
                host_pid = $hostProcessId
                at = (Get-Date).ToUniversalTime().ToString('o')
                queue_count = @(Get-ChildItem -LiteralPath (Join-Path $StateDir 'queue') -Filter '*.json' -File -ErrorAction SilentlyContinue).Count
            })
        }
        catch {
            Write-ClawAtomicJson -Path $heartbeatPath -Value ([ordered]@{
                pid = $PID
                host_pid = $hostProcessId
                at = (Get-Date).ToUniversalTime().ToString('o')
                last_error = $_.Exception.Message
            })
        }
        Start-Sleep -Milliseconds 200
    }
    Save-DispatcherState -State 'stopped'
}
catch {
    Save-DispatcherState -State 'failed' -ErrorMessage $_.Exception.Message
    throw
}
