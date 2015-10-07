#!/bin/bash

set -e
set -x

PACKAGES=$@

cd source.deb
aptitude download $PACKAGES
cd ..
./depackagator.py

git add extracts/*.json
git commit extracts/ -m "Adding packages $PACKAGES"
git push origin master

