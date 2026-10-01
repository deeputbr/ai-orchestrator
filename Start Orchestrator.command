#!/bin/sh
PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PATH
cd "$(dirname "$0")" || exit 1
python3 -m orchestrator.web --open
