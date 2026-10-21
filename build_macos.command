#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
appdir="$PWD/Local Voice Archive.app"
mkdir -p "$appdir/Contents/MacOS"
cache="${TMPDIR:-/tmp}/local-voice-archive-swift-cache"
swiftc -module-cache-path "$cache" VoiceAppR3.swift -o "$appdir/Contents/MacOS/ArchiveVoice" -framework Cocoa -framework WebKit -framework Speech -framework AVFoundation -framework CoreAudio -framework AudioToolbox
cp resources/Info.plist "$appdir/Contents/Info.plist"
xattr -cr "$appdir"
codesign --force --sign - "$appdir"
codesign --verify --deep --strict "$appdir"
print "Built: $appdir (local ad-hoc signature, not notarized)"
