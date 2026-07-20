# Dedicated Claw sandbox VM

**Decision date:** 2026-07-20  
**Status:** approved target architecture; not provisioned by this change

## Decision

Move the Claw runtime, Telegram bridge, project workspaces, session files, OCR,
compilers, and agent-installed dependencies from the shared VM to **one
dedicated sandbox VM**.

For the first migration:

- use one VM shared by all Claw projects;
- do not add a container per project;
- do not create a GitHub deploy key;
- keep GitHub write credentials outside the sandbox;
- keep the Telegram bot and its token on the Telegram host;
- keep Gemma inference on the existing GPU servers.

Per-project containers and repository-scoped deploy keys remain explicitly
deferred upgrades. They must not block the initial VM migration.

## Why a separate VM

Claw can run shell commands, build software, perform OCR, and install packages.
A dedicated VM contains the blast radius of incompatible packages, accidental
file deletion, excessive CPU/RAM/disk use, background services, and build
artifacts. These operations must not be able to alter the shared VM.

The VM is a machine-level sandbox, not isolation between individual Claw
projects. In the initial phase, projects share its operating system and package
set. This is an accepted trade-off to keep deployment and recovery simple.

## Target topology

```text
Telegram bot host
    |
    | authenticated localhost reverse forward
    v
Dedicated Claw sandbox VM
    |- Claw Telegram bridge
    |- Claw binary and tool runtime
    |- project workspaces and attachments
    |- persisted Claw session files
    |- Tesseract/OCRmyPDF, compilers and installed packages
    `- localhost tunnel to the Gemma API
                    |
                    v
             Gemma GPU servers
```

After verified cutover, the shared VM is no longer part of the Claw execution
path. Its old services remain disabled but intact during the rollback window.

## Proposed initial size

The initial planning profile is:

| Resource | Proposed value | Reason |
|---|---:|---|
| vCPU | 16 | parallel builds and two concurrent Claw turns |
| RAM | 32 GiB | builds, OCR, package managers and filesystem cache |
| Disk | 250 GB fast thin-provisioned SSD | repositories, build trees, sessions and attachments |
| GPU | none | inference remains on the Gemma servers |
| OS | Ubuntu 24.04 LTS | stable package and systemd baseline |

This is a starting allocation, not a claim about currently available
hypervisor capacity. Verify free CPU, RAM, and storage before provisioning.
Keep at least 20% disk space free, enable log rotation, and alert on disk, inode,
memory, and load saturation. Increase to 64 GiB RAM only if measured builds need
it.

## Security boundary

Treat the whole sandbox VM as disposable and potentially controlled by the
agent. If Claw receives broad `sudo` so it can install system packages, it can
modify the VM; the security guarantee is that it cannot modify the shared VM,
the hypervisor, or the GPU hosts.

The sandbox must not contain:

- hypervisor or cluster-administration credentials;
- SSH keys for the shared VM or GPU hosts;
- the Telegram bot token;
- a personal GitHub token or a general-purpose SSH key;
- unrelated production data or host filesystem mounts.

Required runtime secrets must be minimal, root-readable, and outside Git. The
bridge remains bound to loopback and protected by a random bearer token. The
Telegram host reaches it only through the restricted reverse SSH forward. The
Gemma API is exposed to the sandbox only through a localhost tunnel; it is not
opened publicly.

Before broad package-install permission is enabled, the bridge should start
the agent with an explicit environment allowlist so bridge credentials are not
inherited by Claw or exposed to its shell tools.

## Storage and session compatibility

Use the same internal directory layout as the current installation during the
first migration. The project state contains exact absolute `session_path`
values; preserving paths allows existing projects to resume without rewriting
state.

Persist and back up, as one consistent set:

- the bridge JSON state file;
- all project workspaces and attachments;
- `.claw` session storage;
- any project-local durable notes created before compaction.

Do not copy these independently while the bridge is accepting turns. Stop or
quiesce the old bridge, take the final consistent copy, then start the new one.
If the target paths change, add and test a one-time state migration instead of
editing session paths manually.

## Migration sequence

1. Measure hypervisor capacity and create the VM from a clean template.
2. Apply OS updates, time synchronization, disk monitoring, log rotation, and
   a pre-provisioning snapshot.
3. Create the unprivileged service account and the existing writable directory
   layout.
4. Install pinned Claw, Python, OCR, compiler, and tunnel dependencies.
5. Configure the Gemma localhost tunnel and verify text and Vision requests.
6. Install the bridge with its environment file outside Git and confirm that it
   binds only to loopback.
7. Perform an initial state/workspace copy while the current service remains
   live, then stop it and perform a final consistent delta copy.
8. Stop the old reverse forward before starting the new forward, because both
   cannot own the same listen port on the Telegram host.
9. Run all acceptance tests below before declaring the new VM authoritative.
10. Keep the old services disabled and unchanged through the rollback window;
    remove them only after backups and the new VM have been proven stable.

## Acceptance tests

Cutover is complete only when all of the following pass:

- bridge and model-tunnel services are active with zero restart loops;
- ordinary Gemma Telegram text and screenshot handling still works;
- a new Claw project can run tools and install a harmless test package;
- close, list, switch, reopen, and stop work from Telegram;
- a completed marker survives bridge and VM reboot;
- two parallel projects have different session IDs/files and do not mix state;
- automatic screenshot/PDF OCR works without a separate OCR menu;
- existing migrated projects resume using their exact session files;
- no Telegram, cluster, hypervisor, or general GitHub credential is visible to
  the agent process;
- the shared VM shows no Claw build, package, process, or filesystem changes
  caused by the sandbox test.

## Rollback

Before rollback, stop the new bridge so state no longer changes. Copy any new
completed projects and session files back as one consistent set, stop the new
reverse forward, restore the previous forward and bridge, and rerun the resume
and health fixtures. Never run both bridges against independently changing
copies of the same logical project state.

## Deferred upgrades — not part of the first migration

### Container per project

A rootless Podman/Docker container per Telegram project would isolate package
versions, processes, and files between projects and make cleanup reproducible.
It is useful when projects begin to conflict or untrusted repositories are
handled, but it adds image lifecycle, storage, networking, and session-mount
complexity. The initial dedicated VM provides the required isolation from the
shared VM without this overhead.

### Repository-scoped GitHub deploy keys

The initial sandbox has no GitHub write credential. It can clone and fetch
public repositories; pushes remain in the existing controlled workflow.

If autonomous pushes become necessary, create a separate deploy key for each
approved repository, grant write access only where required, store the private
key outside Git with strict permissions, pin host keys, document ownership and
rotation, and test immediate revocation. Never copy a personal or multi-repo
credential into the sandbox.
