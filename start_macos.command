#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
py="${LOCAL_VOICE_ARCHIVE_PYTHON:-python3}"
"$py" r3_service.py ensure
[[ -d "Local Voice Archive.app" ]] || ./build_macos.command
open "Local Voice Archive.app"
