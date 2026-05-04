#!/bin/bash
#
# Recursively downloads the named packages plus all transitive dependencies
# into source.deb/, then runs depackagator.py to build extracts/ + blobs/.
#
# Usage: ./download_recursive.sh pkg1 pkg2 ...

set -e

ROOTS="$@"
if [ -z "$ROOTS" ]; then
    echo "usage: $0 package [package...]" >&2
    exit 1
fi

mkdir -p source.deb

# BFS over Depends to get a flat package set. Strip any :arch suffix and
# pin everything to the host architecture so multiarch i386 doesn't pull
# duplicates.
ARCH=$(dpkg --print-architecture)
PACKAGES=$(
    apt-cache depends --recurse --no-recommends --no-suggests \
        --no-conflicts --no-breaks --no-replaces --no-enhances \
        $ROOTS \
    | grep -E '^\w' \
    | sed -e 's/:.*//' \
    | sort -u
)

cd source.deb
echo "$PACKAGES" | sed "s/$/:${ARCH}/" | xargs apt-get download
cd ..

./depackagator.py
