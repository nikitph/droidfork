#!/bin/sh
# Package the locally compiled, headless ARM64 engine with matching SDK resources.
set -eu
obj=$(cd "$1" && pwd)
out=$2
if [ -e "$out" ]; then echo "Choose a new directory for the development SDK" >&2; exit 1; fi
cp -cR "$ANDROID_HOME/emulator" "$out"
cp -RL "$obj/lib64/" "$out/lib64/"
engine=qemu/darwin-aarch64/qemu-system-aarch64-headless
# The bundled SwiftShader uses POSIX shm_open flags accepted under the vendor
# engine's Darwin 13.3 SDK compatibility behavior. Preserve that behavior.
vtool -set-build-version macos 13.0 13.3 -replace -output "$out/$engine" "$obj/$engine"
plist=$(mktemp)
trap 'rm -f "$plist"' EXIT
cat > "$plist" <<'PLIST'
<plist version="1.0"><dict><key>com.apple.security.hypervisor</key><true/></dict></plist>
PLIST
codesign -fs - --entitlements "$plist" "$out/$engine"
codesign --verify "$out/$engine"
