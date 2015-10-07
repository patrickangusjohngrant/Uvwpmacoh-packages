#!/usr/bin/python3

import debian.debfile
debian.debfile.PART_EXTS += ['xz']

from glob import glob
import json
import os
import pprint
import shutil
import sys
import stat
import lzma
import re

from boto.s3.key import Key
import boto.s3.connection

bucket = boto.s3.connection.S3Connection().get_bucket("uvwpmacoh")

placeholders = [
    "perlapi-5.18.1",
    "python3:any",
    "python:any",
    "upstart-job",
    "xorg-input-abi-19",
    "xorg-input-abi-20",
    "xorg-input-abi-21",
    "xorg-video-abi-14",
    "xorg-video-abi-15",
    "xorg-video-abi-18",
]

# Use fakeroot so that when the debs are extracted, the correct metadata is
# preserved.
if os.getuid() != 0:
    f = "/usr/bin/fakeroot"
    os.execv(f, [f] + sys.argv)


def packagator(extract, url, force_root=False):
    """
    Recursively creates dictionaries which represent the package (suitable for
    serialization).
    """
    s = os.lstat(extract)

    # TODO: use partialoverride for stat override?
    override = extract + ".stat-override.json"
    fileoverride = extract + ".file-override.json"
    partialoverride = extract + ".partial-override.json"
    
    print(fileoverride)

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

        # Upload to amazon
        key = Key(bucket)
        key.set_contents_from_filename(extract)
        key.change_storage_class("REDUCED_REDUNDANCY")

        # Um. Ssssh.
        ret["file"] = key.generate_url(expires_in=0, query_auth=False).replace(
            "https://uvwpmacoh.s3.amazonaws.com:443/",
            "http://d1fhml1jgvxldu.cloudfront.net/"
        )
 
    if os.path.exists(partialoverride):
        overrides = json.load(open(partialoverride))
        for k, v in overrides.items():
            ret[k] = v

    return ret


def safe_mkdirs(path):
    """
    Same as os.makedirs except without throwing an exception if "path" exists.
    """
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
        #print "Skipping " + metadata['debcontrol']['Package']
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

    # Don't look! "python-debian" doesn't support .xz files. This patches that
    # up.
    if source.data._DebPart__member.name.endswith("xz"):
        import tempfile
        import tarfile
        t1 = tempfile.TemporaryFile(mode="rb+")
        t1.write(source.data._DebPart__member.read())
        t1.seek(0)
        t2 = tempfile.NamedTemporaryFile(mode="rb+")
        lz = lzma.LZMAFile(filename="/proc/%s/fd/%d" % (os.getpid(), t1.fileno()))
        t2.write(lz.read())
        t2.seek(0)
        source.data._DebPart__tgz = tarfile.TarFile(fileobj=t2, mode='r')

    # Extract the .tar
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

    # And invert it
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
                os.chmod(
                    f,
                    0o644
                )
   
    # Aand write to disk.
    with open(json_file, "w") as f:
        json.dump(
            package,
            f,
            indent=4
        )

def main(force=False):
    destination = "extracts/"
    if os.path.isdir(destination):
        if force:
            shutil.rmtree(destination)

    for package in glob("source.deb/*.deb"):
        print("Processing " + package)
        debfile = debian.debfile.DebFile(package)

        # dead code?
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
