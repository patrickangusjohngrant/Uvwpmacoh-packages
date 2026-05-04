#!/bin/bash

set -e
set -x

PACKAGES=$@

mkdir -p source.deb
cd source.deb
apt-get download $PACKAGES
cd ..
./depackagator.py
