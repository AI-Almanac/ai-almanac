# Running on an NVIDIA DGX Spark (rootless Docker)

`scripts/spark.sh` runs the whole application in one container, as an ordinary
(non-root) user. The host needs only Docker; pixi and every workload
environment live inside the container.

One container holds the web UI and API, the SQLite database, the job
supervisor, and the pixi workload environments (benchmark, blending, forecast,
and the optional local LLM). It installs those environments into its data
directory on first use and runs jobs as subprocesses, exactly as a native
install does. It never mounts the Docker socket or launches other containers.

## One-time host setup

DGX OS ships everything below except the per-user daemon. On another Ubuntu
host, an administrator installs it once:

| Requirement | Check | Administrator fix |
|---|---|---|
| Rootless Docker extras | `which dockerd-rootless-setuptool.sh` | `apt install docker-ce-rootless-extras uidmap` |
| A subordinate UID/GID range per user | `grep $USER /etc/subuid /etc/subgid` | `usermod --add-subuids 100000-165535 --add-subgids 100000-165535 <user>` |
| AppArmor profile for rootlesskit (Ubuntu 24.04+) | `ls /etc/apparmor.d/rootlesskit` | installed by `docker-ce-rootless-extras` |
| NVIDIA CDI spec | `ls /etc/cdi/nvidia.yaml /var/run/cdi/nvidia.yaml` | `nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml` |

Optional: `sudo loginctl enable-linger <user>` keeps a user's Docker daemon,
and therefore running jobs, alive after they log out.

Each user then starts their own daemon, without root:

```bash
dockerd-rootless-setuptool.sh install     # add --force if you can also reach the rootful daemon
export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock
```

The GPU is injected through CDI (`nvidia.com/gpu=all`), which rootless Docker
supports without the NVIDIA runtime's `no-cgroups` setting.

## Use

```bash
scripts/spark.sh up        # build, start (setting up on first run, else updating any
                           # workload environment the image changed), print the URL to open
scripts/spark.sh prepare   # reinstall the workload environments
scripts/spark.sh url       # print the URL again
scripts/spark.sh logs
scripts/spark.sh status    # container state and the GPU it sees
scripts/spark.sh down      # stop; data is kept
```

Open the printed `?token=` URL.

The first `up` on a new data directory sets the install up without the wizard:
the assistant runs `ALMANAC_LLM_MODEL` on this machine, `/datasets` is the
dataset root, and the workload environments (forecast ones included, since
there is a GPU) and the model's weights are installed. That downloads tens of
gigabytes and takes a while; it is cached in the data directory, survives
image rebuilds, and resumes if interrupted. A data directory that is already
set up is left as it is, so changes made in Settings stick.

Every `up` then registers the datasets it hasn't seen yet (see below).

| Variable | Default | Purpose |
|---|---|---|
| `ALMANAC_DATA_DIR` | `~/.local/share/ai-almanac-spark` | Database, jobs, environments, and download caches |
| `ALMANAC_DATASETS_DIR` | `./datasets` | Host directory mounted read-only at `/datasets` |
| `ALMANAC_PORT` | `8765` | Loopback port |
| `ALMANAC_LLM_MODEL` | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` | The assistant model a fresh install runs |

The data directory is a bind mount, so outputs and the database are ordinary
files owned by you. Use a local disk: SQLite refuses a network filesystem.
Environments are tied to the container's `/data` path, so this directory cannot
be shared with a native install.

## Registering datasets

Put each dataset in its own directory of yearly NetCDF files under the datasets
directory, e.g. `india/obs/` and `india/models/graphcast/`. `scripts/spark.sh up`
registers each new one it finds, under the name `deploy/spark-datasets.yaml`
gives its path (and with the live forecast model the file links it to), or a
name inferred from the path when the file doesn't list it. To ship a dataset
to every machine under a chosen name, add it there:

```yaml
sources:
  india/models/graphcast:
    name: GraphCast
    forecast_model_id: graphcast_operational
```

`up` creates an empty datasets directory if there isn't one, so data can be
copied in later; the next `up` registers it. Copy into the directory rather
than replacing it: a container keeps seeing the directory it started with, so
after a replacement `up` recreates the container (a plain restart wouldn't).

To preview or register without restarting:

```bash
scripts/spark.sh discover              # list what it finds and how each would register
scripts/spark.sh discover --register   # register the ready ones
```

Or use **Find sources** on the Data Sources page, which lets you rename sources
and pick a region before registering. Each directory's kind, variable, file
pattern, and region are inferred from the files and path; a region is only
suggested from coverage when it matches a configured region's box closely, so
a source outside every region is left for you to assign. Symlinked directories
are not followed, since their targets aren't mounted in the container.

## Security model

- The port is published on `127.0.0.1` only; other machines cannot reach it.
- `serve --container` (set by the compose file) allows binding all interfaces
  inside the container and always requires an access token. Every local user of
  the Spark can reach a loopback port, so the token is what keeps them out. It is
  generated once and stored in `secrets.env` in the data directory.
- The process runs as container root. Rootless Docker maps that to your own
  unprivileged account, so it has no more access to the host than you do.

## Several users on one Spark

Each user runs their own daemon, image, and data directory, so nothing is
shared between them. Give each user a different `ALMANAC_PORT`, and plan disk
space for one copy of the image and environments per user. All instances share
the one GPU and its unified memory, so concurrent forecasts compete for it.
