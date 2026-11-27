# Platform support and Raspberry Pi deployments

The edge agent and the complete headend have deliberately different support
matrices. The agent is a static Go binary and is suitable for small 32-bit
devices. The headend includes Python, PostgreSQL or SQLite, Node.js, and the
Next.js native compiler, so its production baseline is 64-bit.

## Support matrix

| Role | Platform | Status | Typical hardware |
|---|---|---|---|
| Edge agent | Linux x86-64 (`amd64`) | Supported | Servers, PCs, hypervisors |
| Edge agent | Linux x86 32-bit (`386`) | Supported, legacy | Older PCs and appliances |
| Edge agent | Linux ARM64 (`arm64`) | Supported | Pi 3/4/5 with 64-bit OS, ARM servers |
| Edge agent | Linux ARMv7 32-bit (`armv7`) | Supported | Pi 2 and 32-bit Pi 3/4 images |
| Edge agent | Linux ARMv6 32-bit (`armv6`) | Supported, constrained | Pi 1, Pi Zero, Pi Zero W |
| Edge agent | macOS Apple Silicon (`darwin-arm64`) | Supported | MacBook, Mac Studio, Mac mini (M-series) |
| Edge agent | macOS Intel (`darwin-amd64`) | Supported | Intel Macs |
| Edge agent | Windows x86-64 (`windows-amd64`) | Supported | Windows 10/11 PCs and Windows Server |
| Edge agent | Windows ARM64 (`windows-arm64`) | Supported | Snapdragon / ARM64 Windows laptops |
| Full headend | Linux x86-64 (`amd64`) | Supported | VM, mini PC, server |
| Full headend | Linux ARM64 (`arm64`) | Supported | Pi 4/5, ARM VM or server |
| Full headend | 32-bit x86 or ARM | Not supported | Use the device as an agent instead |

“Supported” means the target is built by CI. Hardware acceptance tests should
cover at least one ARM64 Pi and one ARMv7 Pi before calling a release generally
available. ARMv6 is build-tested and intended for low-rate telemetry; Docker,
SMART, ZFS, Vault scans, and diagnostics still depend on what the host exposes.

The 32-bit headend boundary is intentional. The official Node image used by the
dashboard is currently published for x86-64 and ARM64, not 32-bit ARM. A custom
32-bit frontend toolchain would create a fragile support fork. Put the full
headend on a Pi 4/5 with a 64-bit OS or another 64-bit system, and enroll older
Pis as agents.

## Build native agent artifacts

Go 1.21 or newer and Python 3.10 or newer are required on the build machine.
The build script disables CGO, produces all supported Linux binaries, and writes
a checksum manifest:

```bash
python scripts/build_agent_artifacts.py
(cd dist/agents && sha256sum --check SHA256SUMS)
```

Build only one target by repeating `--target` as needed:

```bash
python scripts/build_agent_artifacts.py --target linux-arm64 --target linux-armv7
```

Target selection on a device:

```bash
uname -m
# x86_64 -> linux-amd64        i386/i686 -> linux-386
# aarch64 -> linux-arm64       armv7l -> linux-armv7
# armv6l -> linux-armv6
```

Install the selected binary and service definition, then edit the environment
file before starting it:

```bash
sudo install -m 0755 dist/agents/control-plane-agent-linux-arm64 \
  /usr/local/bin/control-plane-agent
sudo install -m 0600 deploy/agent.env.example /etc/control-plane-agent.env
sudo install -m 0644 deploy/control-plane-agent.service \
  /etc/systemd/system/control-plane-agent.service
sudoedit /etc/control-plane-agent.env
sudo systemctl daemon-reload
sudo systemctl enable --now control-plane-agent
sudo journalctl -u control-plane-agent -f
```

Replace `linux-arm64` with the architecture selected above. The provided unit
runs as root because Docker socket access, SMART, and some host diagnostics can
require it. For telemetry-only installations, a dedicated account with narrowly
granted Docker access is preferable. The enrollment token lives only in the
root-owned `0600` environment file, not the world-readable unit.

## Multi-platform agent container

The agent Dockerfile consumes BuildKit's target architecture and variant. Build
or publish a manifest containing the common container platforms with:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -f deploy/Dockerfile.agent \
  -t ghcr.io/OWNER/control-plane-agent:VERSION \
  --push .
```

Use the native ARMv6 binary for Pi 1 and original Pi Zero deployments. A native
binary also uses less memory than keeping a container runtime on these devices.

## Raspberry Pi 4/5 headend

Recommended baseline:

- Raspberry Pi 4 with 4 GB or more, or Raspberry Pi 5
- 64-bit Raspberry Pi OS Lite
- USB 3 SSD or NVMe storage for the Docker data root and Hub data
- wired Ethernet, a quality power supply, cooling, and time synchronization
- Docker Engine with the Compose plugin

The included Pi profile uses one Hub process, embedded SQLite, and the dashboard.
It is intended for a small self-hosted fleet. Use the PostgreSQL/Redis cloud
profile on larger or highly available installations.

```bash
cp deploy/pi.env.example deploy/pi.env
# Replace both token placeholders; review bind address and CORS origin.
docker compose --env-file deploy/pi.env \
  -f deploy/docker-compose.pi.yml up -d --build
docker compose --env-file deploy/pi.env \
  -f deploy/docker-compose.pi.yml ps
```

With the default loopback binding, put Caddy, Traefik, or another TLS reverse
proxy on the Pi. If `BIND_ADDRESS=0.0.0.0` is used for a trusted lab LAN, set
`CORS_ORIGINS` to the dashboard URL clients actually open and restrict ports
3000/8080 at the host firewall.

Building the dashboard on a Pi is slower than on x86-64. For repeatable releases,
publish `linux/amd64,linux/arm64` Hub and dashboard images from CI and let Compose
pull the matching image. Do not build production ARM images through emulation
without subsequently smoke-testing them on real ARM64 hardware.

## Hardware acceptance checklist

For every agent architecture claimed by a release:

1. Start the binary on the target architecture and confirm it stays connected
   for at least 30 minutes.
2. Confirm CPU, memory, load, filesystem, and available Docker telemetry.
3. Issue `ping`, then exercise any enabled Vault, Proxmox, update, and emergency
   diagnostic paths.
4. Interrupt network access, restore it, and verify reconnect without duplicate
   node records.
5. Verify the downloaded artifact against `SHA256SUMS`.

For an ARM64 Pi headend, also run the full test-environment smoke test from an
external machine and complete the SQLite backup/restore drill in
`docs/disaster-recovery.md`.

## Upstream architecture references

- Go targets and ARM levels: <https://go.dev/doc/install/source> and
  <https://go.dev/wiki/GoArm>
- Docker multi-platform builds: <https://docs.docker.com/build/building/multi-platform/>
- Raspberry Pi OS 32/64-bit guidance:
  <https://www.raspberrypi.com/documentation/computers/os.html#architecture>
- Node official container architecture metadata:
  <https://github.com/docker-library/official-images/blob/master/library/node>
