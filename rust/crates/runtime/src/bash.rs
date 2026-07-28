use std::env;
use std::fmt::Write as _;
use std::io;
use std::process::{Command, ExitStatus, Stdio};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tokio::io::{AsyncRead, AsyncReadExt};
use tokio::process::Command as TokioCommand;
use tokio::runtime::Builder;
use tokio::time::timeout;

use crate::sandbox::{
    build_linux_sandbox_command, resolve_sandbox_status_for_request, FilesystemIsolationMode,
    SandboxConfig, SandboxStatus,
};
use crate::ConfigLoader;

const MAX_CAPTURED_STREAM_BYTES: usize = 256 * 1024;
const OUTPUT_READ_BUFFER_BYTES: usize = 16 * 1024;
const DEFAULT_TIMEOUT_ENV: &str = "CLAW_BASH_DEFAULT_TIMEOUT_MS";

/// Input schema for the built-in bash execution tool.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BashCommandInput {
    pub command: String,
    pub timeout: Option<u64>,
    pub description: Option<String>,
    #[serde(rename = "run_in_background")]
    pub run_in_background: Option<bool>,
    #[serde(rename = "dangerouslyDisableSandbox")]
    pub dangerously_disable_sandbox: Option<bool>,
    #[serde(rename = "namespaceRestrictions")]
    pub namespace_restrictions: Option<bool>,
    #[serde(rename = "isolateNetwork")]
    pub isolate_network: Option<bool>,
    #[serde(rename = "filesystemMode")]
    pub filesystem_mode: Option<FilesystemIsolationMode>,
    #[serde(rename = "allowedMounts")]
    pub allowed_mounts: Option<Vec<String>>,
}

/// Output returned from a bash tool invocation.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BashCommandOutput {
    pub stdout: String,
    pub stderr: String,
    #[serde(rename = "rawOutputPath")]
    pub raw_output_path: Option<String>,
    pub interrupted: bool,
    #[serde(rename = "isImage")]
    pub is_image: Option<bool>,
    #[serde(rename = "backgroundTaskId")]
    pub background_task_id: Option<String>,
    #[serde(rename = "backgroundedByUser")]
    pub backgrounded_by_user: Option<bool>,
    #[serde(rename = "assistantAutoBackgrounded")]
    pub assistant_auto_backgrounded: Option<bool>,
    #[serde(rename = "dangerouslyDisableSandbox")]
    pub dangerously_disable_sandbox: Option<bool>,
    #[serde(rename = "returnCodeInterpretation")]
    pub return_code_interpretation: Option<String>,
    #[serde(rename = "noOutputExpected")]
    pub no_output_expected: Option<bool>,
    #[serde(rename = "structuredContent")]
    pub structured_content: Option<Vec<serde_json::Value>>,
    #[serde(rename = "persistedOutputPath")]
    pub persisted_output_path: Option<String>,
    #[serde(rename = "persistedOutputSize")]
    pub persisted_output_size: Option<u64>,
    #[serde(rename = "sandboxStatus")]
    pub sandbox_status: Option<SandboxStatus>,
}

/// Executes a shell command with the requested sandbox settings.
pub fn execute_bash(input: BashCommandInput) -> io::Result<BashCommandOutput> {
    let cwd = env::current_dir()?;
    let sandbox_status = sandbox_status_for_input(&input, &cwd);

    if input.run_in_background.unwrap_or(false) {
        let mut child = prepare_command(&input.command, &cwd, &sandbox_status, false);
        let child = child
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()?;

        return Ok(BashCommandOutput {
            stdout: String::new(),
            stderr: String::new(),
            raw_output_path: None,
            interrupted: false,
            is_image: None,
            background_task_id: Some(child.id().to_string()),
            backgrounded_by_user: Some(false),
            assistant_auto_backgrounded: Some(false),
            dangerously_disable_sandbox: input.dangerously_disable_sandbox,
            return_code_interpretation: None,
            no_output_expected: Some(true),
            structured_content: None,
            persisted_output_path: None,
            persisted_output_size: None,
            sandbox_status: Some(sandbox_status),
        });
    }

    let runtime = Builder::new_current_thread().enable_all().build()?;
    runtime.block_on(execute_bash_async(input, sandbox_status, cwd))
}

async fn execute_bash_async(
    input: BashCommandInput,
    sandbox_status: SandboxStatus,
    cwd: std::path::PathBuf,
) -> io::Result<BashCommandOutput> {
    let mut command = prepare_tokio_command(&input.command, &cwd, &sandbox_status, true);
    let timeout_ms =
        effective_timeout_ms(input.timeout, env::var(DEFAULT_TIMEOUT_ENV).ok().as_deref());

    let output_result = if let Some(timeout_ms) = timeout_ms {
        match Box::pin(timeout(
            Duration::from_millis(timeout_ms),
            collect_bounded_output(&mut command),
        ))
        .await
        {
            Ok(result) => (result?, false),
            Err(_) => {
                return Ok(BashCommandOutput {
                    stdout: String::new(),
                    stderr: format!("Command exceeded timeout of {timeout_ms} ms"),
                    raw_output_path: None,
                    interrupted: true,
                    is_image: None,
                    background_task_id: None,
                    backgrounded_by_user: None,
                    assistant_auto_backgrounded: None,
                    dangerously_disable_sandbox: input.dangerously_disable_sandbox,
                    return_code_interpretation: Some(String::from("timeout")),
                    no_output_expected: Some(true),
                    structured_content: None,
                    persisted_output_path: None,
                    persisted_output_size: None,
                    sandbox_status: Some(sandbox_status),
                });
            }
        }
    } else {
        (Box::pin(collect_bounded_output(&mut command)).await?, false)
    };

    let (output, interrupted) = output_result;
    let stdout = render_captured_stream(&output.stdout, "stdout");
    let stderr = render_captured_stream(&output.stderr, "stderr");
    let no_output_expected = Some(stdout.trim().is_empty() && stderr.trim().is_empty());
    let return_code_interpretation = output.status.code().and_then(|code| {
        if code == 0 {
            None
        } else {
            Some(format!("exit_code:{code}"))
        }
    });

    Ok(BashCommandOutput {
        stdout,
        stderr,
        raw_output_path: None,
        interrupted,
        is_image: None,
        background_task_id: None,
        backgrounded_by_user: None,
        assistant_auto_backgrounded: None,
        dangerously_disable_sandbox: input.dangerously_disable_sandbox,
        return_code_interpretation,
        no_output_expected,
        structured_content: None,
        persisted_output_path: None,
        persisted_output_size: None,
        sandbox_status: Some(sandbox_status),
    })
}

fn effective_timeout_ms(explicit: Option<u64>, configured_default: Option<&str>) -> Option<u64> {
    explicit.or_else(|| {
        configured_default
            .and_then(|value| value.trim().parse::<u64>().ok())
            .filter(|value| *value > 0)
    })
}

struct BoundedCommandOutput {
    status: ExitStatus,
    stdout: CapturedStream,
    stderr: CapturedStream,
}

struct CapturedStream {
    bytes: Vec<u8>,
    total_bytes: u64,
}

async fn collect_bounded_output(command: &mut TokioCommand) -> io::Result<BoundedCommandOutput> {
    command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    #[cfg(unix)]
    command.process_group(0);
    let mut child = command.spawn()?;
    let mut process_group = ProcessGroupGuard::new(child.id());
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| io::Error::other("bash stdout pipe was not created"))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| io::Error::other("bash stderr pipe was not created"))?;

    let (status, stdout, stderr) =
        tokio::join!(child.wait(), drain_bounded(stdout), drain_bounded(stderr));
    process_group.disarm();
    Ok(BoundedCommandOutput {
        status: status?,
        stdout: stdout?,
        stderr: stderr?,
    })
}

struct ProcessGroupGuard {
    pid: Option<u32>,
}

impl ProcessGroupGuard {
    const fn new(pid: Option<u32>) -> Self {
        Self { pid }
    }

    fn disarm(&mut self) {
        self.pid = None;
    }
}

impl Drop for ProcessGroupGuard {
    fn drop(&mut self) {
        let Some(pid) = self.pid else {
            return;
        };
        #[cfg(unix)]
        {
            let _ = Command::new("kill")
                .args(["-KILL", "--", &format!("-{pid}")])
                .status();
        }
    }
}

async fn drain_bounded<R>(mut reader: R) -> io::Result<CapturedStream>
where
    R: AsyncRead + Unpin,
{
    let mut bytes = Vec::with_capacity(MAX_CAPTURED_STREAM_BYTES);
    let mut total_bytes = 0_u64;
    let mut buffer = [0_u8; OUTPUT_READ_BUFFER_BYTES];

    loop {
        let read = reader.read(&mut buffer).await?;
        if read == 0 {
            break;
        }
        total_bytes = total_bytes.saturating_add(read as u64);
        let remaining = MAX_CAPTURED_STREAM_BYTES.saturating_sub(bytes.len());
        bytes.extend_from_slice(&buffer[..read.min(remaining)]);
    }

    Ok(CapturedStream { bytes, total_bytes })
}

fn render_captured_stream(output: &CapturedStream, stream_name: &str) -> String {
    let mut rendered = String::from_utf8_lossy(&output.bytes).into_owned();
    if output.total_bytes > output.bytes.len() as u64 {
        let _ = write!(
            rendered,
            "\n\n[Claw truncated {stream_name}: captured the first {} of {} bytes. \
             Redirect large output to a file and inspect it with bounded filters or line ranges.]",
            output.bytes.len(),
            output.total_bytes
        );
    }
    rendered
}

fn sandbox_status_for_input(input: &BashCommandInput, cwd: &std::path::Path) -> SandboxStatus {
    let config = ConfigLoader::default_for(cwd).load().map_or_else(
        |_| SandboxConfig::default(),
        |runtime_config| runtime_config.sandbox().clone(),
    );
    let request = config.resolve_request(
        input.dangerously_disable_sandbox.map(|disabled| !disabled),
        input.namespace_restrictions,
        input.isolate_network,
        input.filesystem_mode,
        input.allowed_mounts.clone(),
    );
    resolve_sandbox_status_for_request(&request, cwd)
}

fn prepare_command(
    command: &str,
    cwd: &std::path::Path,
    sandbox_status: &SandboxStatus,
    create_dirs: bool,
) -> Command {
    if create_dirs {
        prepare_sandbox_dirs(cwd);
    }

    if let Some(launcher) = build_linux_sandbox_command(command, cwd, sandbox_status) {
        let mut prepared = Command::new(launcher.program);
        prepared.args(launcher.args);
        prepared.current_dir(cwd);
        prepared.envs(launcher.env);
        return prepared;
    }

    let mut prepared = Command::new("sh");
    prepared.arg("-lc").arg(command).current_dir(cwd);
    if sandbox_status.filesystem_active {
        prepared.env("HOME", cwd.join(".sandbox-home"));
        prepared.env("TMPDIR", cwd.join(".sandbox-tmp"));
    }
    prepared
}

fn prepare_tokio_command(
    command: &str,
    cwd: &std::path::Path,
    sandbox_status: &SandboxStatus,
    create_dirs: bool,
) -> TokioCommand {
    if create_dirs {
        prepare_sandbox_dirs(cwd);
    }

    if let Some(launcher) = build_linux_sandbox_command(command, cwd, sandbox_status) {
        let mut prepared = TokioCommand::new(launcher.program);
        prepared.args(launcher.args);
        prepared.current_dir(cwd);
        prepared.envs(launcher.env);
        return prepared;
    }

    let mut prepared = TokioCommand::new("sh");
    prepared.arg("-lc").arg(command).current_dir(cwd);
    if sandbox_status.filesystem_active {
        prepared.env("HOME", cwd.join(".sandbox-home"));
        prepared.env("TMPDIR", cwd.join(".sandbox-tmp"));
    }
    prepared
}

fn prepare_sandbox_dirs(cwd: &std::path::Path) {
    let _ = std::fs::create_dir_all(cwd.join(".sandbox-home"));
    let _ = std::fs::create_dir_all(cwd.join(".sandbox-tmp"));
}

#[cfg(test)]
mod tests {
    use super::{effective_timeout_ms, execute_bash, BashCommandInput};
    use crate::sandbox::FilesystemIsolationMode;
    use std::time::Duration;

    #[test]
    fn executes_simple_command() {
        let output = execute_bash(BashCommandInput {
            command: String::from("printf 'hello'"),
            timeout: Some(1_000),
            description: None,
            run_in_background: Some(false),
            dangerously_disable_sandbox: Some(false),
            namespace_restrictions: Some(false),
            isolate_network: Some(false),
            filesystem_mode: Some(FilesystemIsolationMode::WorkspaceOnly),
            allowed_mounts: None,
        })
        .expect("bash command should execute");

        assert_eq!(output.stdout, "hello");
        assert!(!output.interrupted);
        assert!(output.sandbox_status.is_some());
    }

    #[test]
    fn disables_sandbox_when_requested() {
        let output = execute_bash(BashCommandInput {
            command: String::from("printf 'hello'"),
            timeout: Some(1_000),
            description: None,
            run_in_background: Some(false),
            dangerously_disable_sandbox: Some(true),
            namespace_restrictions: None,
            isolate_network: None,
            filesystem_mode: None,
            allowed_mounts: None,
        })
        .expect("bash command should execute");

        assert!(!output.sandbox_status.expect("sandbox status").enabled);
    }

    #[test]
    fn configured_default_timeout_is_opt_in_and_explicit_timeout_wins() {
        assert_eq!(effective_timeout_ms(None, None), None);
        assert_eq!(effective_timeout_ms(None, Some("")), None);
        assert_eq!(effective_timeout_ms(None, Some("0")), None);
        assert_eq!(effective_timeout_ms(None, Some("invalid")), None);
        assert_eq!(effective_timeout_ms(None, Some("900000")), Some(900_000));
        assert_eq!(
            effective_timeout_ms(Some(1_234), Some("900000")),
            Some(1_234)
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn timeout_terminates_the_entire_shell_process_group() {
        let pid_file = std::env::temp_dir().join(format!(
            "claw-bash-timeout-child-{}-{}",
            std::process::id(),
            std::thread::current().name().unwrap_or("unnamed")
        ));
        let command = format!(
            "sleep 30 & child=$!; printf '%s' \"$child\" > {}; wait \"$child\"",
            pid_file.display()
        );
        let output = execute_bash(BashCommandInput {
            command,
            timeout: Some(100),
            description: None,
            run_in_background: Some(false),
            dangerously_disable_sandbox: Some(true),
            namespace_restrictions: None,
            isolate_network: None,
            filesystem_mode: None,
            allowed_mounts: None,
        })
        .expect("timed command should return a structured result");

        assert!(output.interrupted);
        assert_eq!(
            output.return_code_interpretation.as_deref(),
            Some("timeout")
        );
        let child_pid = std::fs::read_to_string(&pid_file)
            .expect("child pid should be recorded")
            .trim()
            .parse::<u32>()
            .expect("child pid should be numeric");
        for _ in 0..50 {
            if !std::path::Path::new(&format!("/proc/{child_pid}")).exists() {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(
            !std::path::Path::new(&format!("/proc/{child_pid}")).exists(),
            "timed-out shell descendant {child_pid} survived"
        );
        let _ = std::fs::remove_file(pid_file);
    }
}

#[cfg(test)]
mod output_preservation_tests {
    use super::*;

    #[test]
    fn long_command_output_is_preserved() {
        let expected = "x".repeat(64 * 1024);
        let output = execute_bash(BashCommandInput {
            command: String::from("python3 -c 'print(\"x\" * 65536, end=\"\")'"),
            timeout: Some(5_000),
            description: None,
            run_in_background: Some(false),
            dangerously_disable_sandbox: Some(true),
            namespace_restrictions: None,
            isolate_network: None,
            filesystem_mode: None,
            allowed_mounts: None,
        })
        .expect("large command output should execute");

        assert_eq!(output.stdout, expected);
    }

    #[test]
    fn oversized_command_output_is_bounded_without_blocking_the_child() {
        let emitted_bytes = MAX_CAPTURED_STREAM_BYTES * 3;
        let output = execute_bash(BashCommandInput {
            command: format!("python3 -c 'print(\"x\" * {emitted_bytes}, end=\"\")'"),
            timeout: Some(5_000),
            description: None,
            run_in_background: Some(false),
            dangerously_disable_sandbox: Some(true),
            namespace_restrictions: None,
            isolate_network: None,
            filesystem_mode: None,
            allowed_mounts: None,
        })
        .expect("oversized command output should be drained safely");

        assert!(output.stdout.starts_with(&"x".repeat(1_024)));
        assert!(output.stdout.contains("Claw truncated stdout"));
        assert!(output.stdout.contains(&emitted_bytes.to_string()));
        assert!(output.stdout.len() < MAX_CAPTURED_STREAM_BYTES + 512);
    }
}
