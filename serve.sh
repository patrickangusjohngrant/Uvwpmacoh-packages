#!/bin/bash
# Serves this directory over HTTP for the QEMU guest to fetch package
# manifests and blobs from. From the guest's user-mode network the host is
# 10.0.2.2:8080.
exec python3 -m http.server --bind 0.0.0.0 8080
