#!/bin/bash
cd "$(dirname "$0")"
git add .
git commit -m "Auto-Commit $(date '+%Y-%m-%d %H:%M:%S')"
git push
