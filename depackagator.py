#!/usr/bin/python3
"""
Walk every .deb in source.deb/, extract, and emit a JSON manifest per package
into extracts/ describing the files. File contents are not embedded; instead
each leaf file is copied to ./blobs/<sha256> and the JSON stores the
*relative* URL "/blobs/<sha256>". The guest's web.ml prepends --http-server
at runtime, so a local Python http.server can host this directory tree.

This replaces the original S3/CloudFront upload path; everything is local.
"""

import debian.debfile
from glob import glob
import hashlib
import json
import os
import shutil
import sys
import stat


# Packages that exist as virtual/relationship targets only (no real .deb).
# Originally Ubuntu-flavoured; now Debian trixie-flavoured.
placeholders = [
    "awk",
    "default-mta",
    "mail-transport-agent",
    "perl:any",
    "perlapi-5.40.0",
    "python3:any",
]


# Use fakeroot so that when the debs are extracted, the correct metadata is
# preserved.
if os.getuid() != 0:
    f = "/usr/bin/fakeroot"
    os.execv(f, [f] + sys.argv)


BLOBS_DIR = "blobs"


def store_blob(path):
    """Copy `path` into BLOBS_DIR keyed by sha256 of its content; return a
    relative URL ("/blobs/<sha256>") that the guest will resolve against
    --http-server."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    os.makedirs(BLOBS_DIR, exist_ok=True)
    target = os.path.join(BLOBS_DIR, digest)
    if not os.path.exists(target):
        shutil.copy2(path, target)
    return f"/{target}"


def packagator(extract, url, force_root=False):
    """
    Recursively creates dictionaries which represent the package (suitable for
    serialization).
    """
    s = os.lstat(extract)

    override = extract + ".stat-override.json"
    fileoverride = extract + ".file-override.json"
    partialoverride = extract + ".partial-override.json"

    if os.path.exists(fileoverride):
        return json.load(open(fileoverride))

    if os.path.exists(override):
        stat_ = json.load(open(override))
    else:
        stat_ = {
            'perm': s.st_mode,
            'uid': s.st_uid,
            'gid': s.st_gid,
            'ctime': s.st_ctime,
            'mtime': s.st_mtime,
            "size": s.st_size
        }
        if force_root:
            stat_['uid'] = 0
            stat_['gid'] = 0

    ret = {
        "stat": stat_,
    }

    if stat.S_ISDIR(s.st_mode):
        ret["deserializer"] = "directory"
        tmp = {}
        for i in os.listdir(extract):
            if not (
                    i.endswith(".stat-override.json") or
                    i.endswith(".file-override.json") or
                    i.endswith(".partial-override.json") or
                    i.endswith(".depackageatorignore")
                ):
                tmp[i] = packagator(
                    os.path.join(extract, i),
                    os.path.join(url, i)
                )
        ret["file"] = tmp
    elif stat.S_ISLNK(s.st_mode):
        ret["deserializer"] = "symlink"
        ret["file"] = os.readlink(extract)
    else:
        ret["deserializer"] = "url"
        ret["file"] = store_blob(extract)

    if os.path.exists(partialoverride):
        overrides = json.load(open(partialoverride))
        for k, v in overrides.items():
            ret[k] = v

    return ret


def safe_mkdirs(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def depackage(source, destination, force=False, metadata_overrides={}):
    """
    Takes a "source" deb package as a DebFile and creates a "destination" json
    file.
    """

    metadata = {
        'debcontrol': dict(source.debcontrol()),
        'files': [],
        'dirs': [],
        'symlinks': [],
    }
    metadata.update(metadata_overrides)

    json_file = os.path.join(
        destination,
        "%s.json" % metadata['debcontrol']['Package']
    )

    if os.path.exists(json_file):
        return

    extract_dir = os.path.join(
        destination,
        metadata['debcontrol']['Package'],
        metadata['debcontrol']['Version']
    )

    if 'Depends' in metadata['debcontrol']:
        for dependency in metadata['debcontrol']['Depends'].split(","):
            safe_mkdirs(
                os.path.join(
                    extract_dir,
                    "control",
                    "packages",
                    dependency.strip()
                )
            )

    # python-debian on trixie handles .xz and .zst transparently, so the old
    # private-attribute hack (._DebPart__tgz) is gone.
    source.data.tgz().extractall(extract_dir)

    # Add over the overlay stuff
    overlay_dir = os.path.join(
        "overlays",
        metadata['debcontrol']['Package']
    )

    for dirpath, dirnames, filenames in os.walk(overlay_dir):
        for dirname in dirnames:
            dirname = os.path.join(dirpath, dirname).replace(overlay_dir, extract_dir)
            if not os.path.exists(dirname):
                os.mkdir(dirname)
        for filename in filenames:
            new_filename = os.path.join(dirpath, filename).replace(overlay_dir, extract_dir)
            shutil.copy(
                os.path.join(dirpath, filename),
                new_filename
            )

    package = packagator(
        extract_dir,
        os.path.join(
            metadata['debcontrol']['Package'],
            metadata['debcontrol']['Version']
        )
    )

    # Now make everything world readable
    for dirpath, _, filenames in os.walk(extract_dir):
        for f in filenames:
            f = os.path.join(dirpath, f)
            if not os.path.islink(f):
                os.chmod(f, 0o644)

    with open(json_file, "w") as f:
        json.dump(package, f, indent=4)


def main(force=False):
    destination = "extracts/"
    if os.path.isdir(destination):
        if force:
            shutil.rmtree(destination)
    safe_mkdirs(destination)

    for package in glob("source.deb/*.deb"):
        print("Processing " + package)
        debfile = debian.debfile.DebFile(package)

        json_overrides_file = \
            "source.json/%s.json" % debfile.debcontrol()['Package']

        if os.path.exists(json_overrides_file):
            json_overrides = json.load(open(json_overrides_file))
        else:
            json_overrides = {}

        depackage(
            debfile,
            destination,
            force=force,
            metadata_overrides=json_overrides
        )

    for placeholder in placeholders:
        print("Generating placeholder for " + placeholder)
        with open("extracts/%s.json" % placeholder, "w") as f:
            json.dump(
                {
                    "file": {},
                    "deserializer": "directory",
                },
                f,
                indent=4
            )

    for definition in glob("source.system_definitions/*"):
        print("Processing system definition: %s" % os.path.basename(definition))
        with open("system_definitions/%s.json" % os.path.basename(definition), "w") as f:
            json.dump(packagator(definition, "", force_root=True), f, indent=4)


if __name__ == "__main__":
    main()
