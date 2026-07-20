# Dedicated Claw sandbox VM

**Decision date:** 2026-07-20
**Deployment date:** 2026-07-21
**Status:** deployed and verified on the dedicated sandbox VM

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

## Deployed size and capacity decision

The original planning profile was 16 vCPU, 32 GiB RAM, and 250 GB SSD. A
capacity check showed that 32 GiB would be unsafe on the selected hypervisor,
which has 62 GiB physical RAM and also runs a 32 GiB shared VM. The deployed
profile is therefore:

| Resource | Deployed value | Reason |
|---|---:|---|
| vCPU | 8 | enough for two concurrent Claw turns without unnecessary host contention |
| RAM | 16 GiB | leaves about 14 GiB (22%) unassigned after both VM limits; measured Claw/OCR use is far below this limit |
| Disk | 250 GB fast thin-provisioned SSD | repositories, build trees, sessions and attachments |
| GPU | none | inference remains on the Gemma servers |
| OS | Ubuntu 24.04 LTS | stable package and systemd baseline |

The VM was expanded from its previous 80 GB disk before migration. After the
deployment it had 239 GB free, while the hypervisor SSD had 550 GB free. The VM
is configured to boot automatically with the hypervisor. Keep at least 20% disk
space free and continue monitoring disk, inodes, memory, and load. Increase RAM
only from measurements and only after rechecking hypervisor headroom; do not
return to the rejected 32 GiB profile on the current host.

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

Before broad package-install permission is enabled, the bridge starts the agent
with an explicit environment allowlist so bridge credentials are not inherited
by Claw or exposed directly to its shell tools.

The deployed VM intentionally allows Claw to use `sudo` and runs it with
`danger-full-access`. This would be inappropriate on the shared VM. It is
acceptable here only because the complete VM is the disposable security
boundary and contains no cluster, Telegram, or general GitHub credential.

The guest firewall denies inbound traffic by default, accepts SSH only from the
hypervisor management address, and blocks outbound traffic to private IPv4
ranges after the specific DNS, management SSH, model-tunnel, and reverse-tunnel
requirements are evaluated. Public internet access remains available for
package downloads and public repository access. The temporary migration rule
and key for the shared VM were removed after final synchronization.

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

## Completed migration record

The existing stopped VM reserved for this purpose was reused instead of
creating another VM. It is located on the primary server SSD, not on the small
secondary GPU host. A pre-migration snapshot was taken before resizing and
provisioning.

The final cutover followed the sequence above:

1. install the runtime, OCR/build tools, firewall, service account, and exact
   legacy directory layout;
2. clone the public Claw repository and install the pinned production binary;
3. create two restricted SSH identities: one local forward to the Gemma API and
   one reverse forward to the Telegram host;
4. perform an initial copy, stop the bot and old bridge, and perform a final
   exact state/workspace/session copy;
5. disable the old bridge and forward, start the new bridge and forward, then
   restart the Telegram bot;
6. remove the temporary source-VM key and firewall exception;
7. reboot the VM and verify automatic service recovery.

The reverse-forward account uses a nonstandard system home directory. Its
`authorized_keys` file must be placed below the home reported by `getent
passwd`, not an assumed `/home/<user>` path. Both the old rollback key and the
new VM key were retained in that correct directory with restricted forwarding
options.

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

All non-interactive infrastructure and bridge checks passed on 2026-07-21. In
particular, a live agent used public internet access, installed a harmless
package with noninteractive `sudo`, and returned the expected marker. After a
VM reboot, all three guest services (model tunnel, bridge, and reverse forward)
were enabled, active, and at zero restarts. A marker was recalled using the
same session ID. Two simultaneous projects completed in 18.297 seconds wall
time versus 36.400 seconds summed turn time, with distinct session IDs. An
image sent through the same bridge path was automatically passed through local
OCR and the agent returned its exact test number. The two real migrated
Telegram session files matched the source SHA-256 hashes byte for byte, and the
active project metadata remained unchanged. The Telegram bot reconnected and
reported zero restarts; no unsolicited user-visible test message was sent.

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
