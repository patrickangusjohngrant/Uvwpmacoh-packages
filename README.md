# Uvwpmacoh-packages

Tooling that turns Debian `.deb` archives into JSON manifests + content-addressed blobs that the OCaml FUSE init in the sister
[Uvwpmacoh](../Uvwpmacoh) repo serves lazily over HTTP to a QEMU guest.

## Layout

| Path | What's in it |
|---|---|
| `source.deb/` | Raw `.deb`s downloaded by `download*.sh`. Inputs to depackagator. |
| `extracts/<pkg>.json` | Per-package manifest the guest fetches when installing the package. |
| `blobs/<sha256>` | Content-addressed file payloads referenced from the JSON manifests. |
| `overlays/<pkg>/` | Files merged on top of `<pkg>`'s extracted tree before manifesting (e.g. `openssh-server/etc/ssh/sshd_config`). |
| `source.system_definitions/default/` | The "system definition" — the root tree the guest mounts at boot. |
| `system_definitions/default.json` | Output produced from the above; what the guest downloads at boot. |
| `serve.sh` | `python3 -m http.server 8080` over the whole repo. |
| `download.sh` / `download_recursive.sh` | Drive `apt-get download` + run depackagator. |
| `depackagator.py` | Walks `source.deb/`, emits `extracts/`, `blobs/`, and `system_definitions/`. |

## Adding a package

There are two distinct things "adding a package" can mean. Be sure which one you want.

### A. Make a package _available_ to install on demand from inside the guest

The guest only knows about a package if `extracts/<pkg>.json` exists on the HTTP server. To add one:

```bash
# Download foo and all its transitive deps as .debs, then re-build extracts/.
./download_recursive.sh foo

# Restart the HTTP server so the new files are visible to the guest.
pkill -f 'http.server' || true
./serve.sh &
```

After that, from a shell inside the guest:

```bash
mkdir /control/packages/foo
```

The FUSE `mkdir` handler downloads `extracts/foo.json`, fetches its blobs lazily as files are read, and merges the package tree into the guest's root.

If you only want to add **one** specific .deb without pulling deps, use `./download.sh foo` instead. Beware: the guest will fail at install time if a transitive dep is missing — `download_recursive.sh` is what you usually want.

### B. Make a package _present at boot_ (no `mkdir` needed)

The system definition under `source.system_definitions/default/control/packages/<pkg>` lists packages that the boot-time `/init` will pre-install before exec-ing bash. To add one:

```bash
# 1. Make sure the package + deps are downloaded and extracted.
./download_recursive.sh foo

# 2. Tell the system definition to install it at boot.
mkdir source.system_definitions/default/control/packages/foo

# 3. Regenerate system_definitions/default.json.
./depackagator.py

# 4. Restart the HTTP server.
pkill -f 'http.server' || true
./serve.sh &
```

Then `cd ../Uvwpmacoh/initramfs && ./run.sh` and the new package will be present in `/usr/...` immediately at the bash prompt.

If you want it always installed on boot _without_ editing `init.ml`, also add it to the bootstrap list in
[`Uvwpmacoh/filesystem/init.ml`](../Uvwpmacoh/filesystem/init.ml) (the `try_install` loop near the chroot). Otherwise it's downloaded but not actively merged into the rootfs until something inside the guest does `mkdir /control/packages/<pkg>`.

### Adding a package with a daemon

Daemons are listed under `source.system_definitions/default/control/daemons/<daemon>`. See `sshd` as a worked example. The
init runs the daemon once you `echo 1 > /control/daemons/<daemon>/enabled` (or it's enabled by default in the system definition). Each entry in the daemon's directory is one argv chunk, in lexical filename order.

## Adding an overlay

Overlays drop opinionated config files (or pre-generated state like SSH host keys) onto a package's extracted tree before manifesting. To overlay file `etc/foo/bar.conf` on package `foo`:

```bash
mkdir -p overlays/foo/etc/foo
$EDITOR overlays/foo/etc/foo/bar.conf
./depackagator.py
```

Overlay files completely replace whatever was in the original .deb at the same path. Stat metadata can be controlled with sibling `*.stat-override.json` / `*.file-override.json` files; see `overlays/openssh-server/var/run/sshd.stat-override.json` for a working example.

## Regenerating from scratch

```bash
rm -rf source.deb extracts blobs system_definitions
./download_recursive.sh \
  bash coreutils sed grep findutils mount net-tools strace \
  ncurses-base locales binutils openssh-server
```

The depackagator is idempotent and skips packages whose `.json` already exists, so you can re-run it cheaply after editing overlays or system definitions. Delete the specific `extracts/<pkg>.json` to force a re-extract of one package.

## Removing a package

```bash
rm extracts/foo.json
rm -rf extracts/foo                 # the unpacked tree
rm source.deb/foo_*.deb
# (blobs/ is content-addressed; orphans are harmless. To garbage-collect:)
# python3 -c 'import json,glob,os
# refs=set()
# for f in glob.glob("extracts/*.json")+glob.glob("system_definitions/*.json"):
#     for line in open(f):
#         if "/blobs/" in line: refs.add(line.split("/blobs/")[1].split(chr(34))[0])
# for b in os.listdir("blobs"):
#     if b not in refs: os.remove(f"blobs/{b}")'
```

If the package was in the boot-time list, also `rmdir source.system_definitions/default/control/packages/foo` and re-run `depackagator.py`.

## How it actually works (short version)

1. `apt-get download` grabs `.deb`s as a normal user.
2. `depackagator.py` (re-execs itself under `fakeroot` so file owners survive) extracts each `.deb`, walks the tree, and:
   - copies every leaf file into `blobs/<sha256>` deduplicated by content,
   - emits `extracts/<pkg>.json` describing the directory tree, with leaves replaced by `"deserializer": "url", "file": "/blobs/<sha256>"`.
3. For each entry in `source.system_definitions/`, it runs the same packagator over that directory and writes `system_definitions/<name>.json`. The `control/` subtree is given the special `"deserializer": "control"` so the guest knows to attach the package_directory / daemon machinery.
4. `serve.sh` exposes everything over HTTP. The guest, given `--http-server http://10.0.2.2:8080`, downloads `system_definitions/default.json` at boot, then fetches `extracts/<pkg>.json` and `blobs/<sha>` on demand as files in `/control/packages/<pkg>` are mkdir'd or read.
