# Unrestricted Claw deployment contract

This profile is intentionally specific to the dedicated operator-controlled
VM. It must not be reused on a shared or untrusted host. Its purpose is to make
all Claw capabilities available without inherited approval, sandbox, network,
tool, environment, or output-truncation blockers.

## Restrictions removed

| Former restriction | Unrestricted behavior | Reason for removal |
|---|---|---|
| Parent `--allowedTools` list | The flag is omitted; every built-in, plugin, runtime MCP, and deferred tool can be advertised. | A static list hid valid tools such as `RemoteTrigger`, `MCP`, `ToolSearch`, notebook, task, team, worker, LSP, and configuration tools. |
| Per-role child tool subsets | Every child role receives the complete built-in registry. | A role label must guide behavior, not remove capabilities needed to finish the delegated task. |
| Child loop capped at 32 iterations | Child runtime uses Claw's unbounded default loop. | Complex tool work can legitimately require more than 32 model/tool iterations. |
| Minimal process environment | Claw inherits the complete bridge service environment; `/etc/claw-agent-credentials.env` can add operator-managed credentials. | Shell, Git, APIs, MCP servers, and remote automation must see the credentials supplied by the operator. |
| Permission deny/ask rules and approval prompts | `CLAW_UNRESTRICTED=1` forces `danger-full-access` and ignores deny/ask rules. | A non-interactive Telegram turn cannot answer an inherited terminal approval prompt. |
| Blocking pre-tool hooks | Runtime and plugin hooks are not installed in unrestricted mode. | A repository hook must not silently reintroduce execution denial. |
| Runtime namespace/filesystem/network sandbox | All layers resolve disabled when `CLAW_UNRESTRICTED=1`. | The dedicated VM, not a per-command namespace, is the execution boundary. |
| systemd hardening inherited by child processes | The dedicated-VM drop-in disables the relevant `Protect*`, `PrivateTmp`, `RestrictSUIDSGID`, `LockPersonality`, and `NoNewPrivileges` settings. | Noninteractive `sudo`, package installation, system administration, and standard credential locations must work. |
| Private IPv4 egress denial | The production UFW deny rules are removed. | Internal HTTP, SSH, Git, API, DNS, model, and administration endpoints must be reachable when routed. |
| Forced HTTP-to-HTTPS rewrite | `WebFetch` preserves an explicitly supplied `http://` URL. | Internal services and development endpoints often expose HTTP only. |
| Native-web result, redirect, and wall-clock caps | Web fetch returns the complete normalized body, search returns every parsed hit, redirects are not count-limited, requests have no automatic wall-clock cutoff, and `RemoteTrigger` returns the complete response body. | Native tools should be useful without immediately falling back to shell commands; `/stop` is the operator-controlled cancellation path. |
| Implicit MCP lifecycle/tool-call timeouts | MCP initialize, discovery, resource, and tool calls have no implicit cutoff in unrestricted mode; an explicitly configured MCP tool timeout is still honored. | Long-running MCP automation must not be terminated by a hidden default. |
| Stale-branch workspace-test preflight | The preflight is bypassed when `CLAW_UNRESTRICTED=1`. | Branch freshness may be useful advice, but it must not suppress an explicitly requested command in this profile. |
| Bash output capped at 16 KiB | stdout and stderr are returned in full. | Build logs, test reports, API responses, and diagnostics were losing decisive tail content. |
| File read/write and search result caps | The 10 MiB file caps and default glob/grep result caps are disabled in unrestricted mode. | Large generated files and repository-wide analysis must remain available. |
| Project instruction prompt capped at 4,000/12,000 characters | All discovered instruction files are included in full in unrestricted mode. | The user requires the complete prompt, not a silently truncated subset. |
| Bridge prompt and Vision/OCR text slicing | Prompt, Vision context, and OCR text are forwarded in full. | Relevant input must not disappear before reaching Claw. |
| Claw, Telegram-to-Claw, Vision, and OCR wall-clock cutoffs | `CLAW_TURN_TIMEOUT=0`, `CLAW_REQUEST_TIMEOUT=0`, `CLAW_VISION_REQUEST_TIMEOUT=0`, and `CLAW_OCR_TIMEOUT=0` keep work open until completion or operator cancellation. | Builds, package installation, Vision/OCR, child work, and long tool loops can exceed a fixed wall-clock timeout. |

## Limits retained because they are physical or protocol requirements

| Retained behavior | Value | Technical reason |
|---|---:|---|
| Simultaneously active built-in child Agents | **1** | The server has exactly two inference slots: parent plus one child. A shared OS advisory `flock` enforces this across separate top-level Claw processes, releases automatically if a process dies, and also prevents recursive fan-out. |
| Simultaneous top-level bridge turns | **2** | Matches the same two physical model slots. Extra chats wait rather than overcommitting inference. |
| Claw completion ceiling | **32,000 tokens** | Matches the current default Opus completion budget in Claw Code. It is only a ceiling; EOS still ends short answers normally. |
| Automatic compaction input point | **110,000 tokens** | Measured for a 163,840-token slot. It preserves room for the complete system/project prompt, all tool schemas/results, and up to 32,000 output tokens. |
| Telegram message chunk | **3,900 characters** | Telegram's message-size protocol requires chunking; the sender preserves every returned character in order. |
| Incoming attachment | **20 MiB** | Matches the deployed Telegram download path. The bridge request-body limit is 64 MiB so base64 expansion does not reduce this effective size. |
| Per-chat serialization | **1 active turn per chat** | Prevents two processes from writing the same Claw session simultaneously. It does not restrict different chats. |
| Loopback bridge plus bearer authentication | enabled | Protects the control transport; it does not limit Claw's outbound network, tools, filesystem, or credentials. |

`/stop` remains the explicit cancellation mechanism. Automatic compaction is
not disabled or moved: `110000` is the verified operating point requested by
the deployment owner.
