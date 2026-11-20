#!/bin/sh
set -eu
src=$(CDPATH='' cd "$(dirname "$0")" && pwd)
out=$1
mkdir -p "$out/classes" "$out/dex"
out=$(cd "$out" && pwd)
bt="$ANDROID_HOME/build-tools/35.0.0"
jar="$ANDROID_HOME/platforms/android-36/android.jar"
mkdir -p "$out/res/raw"
cp -R "$src/observer/res/" "$out/res/"
openssl rand 32 > "$out/res/raw/observer_key"
"$bt/aapt2" compile --dir "$out/res" -o "$out/resources.zip"
"$bt/aapt2" link -o "$out/observer.apk" --manifest "$src/observer/AndroidManifest.xml" -I "$jar" "$out/resources.zip"
case "${DF_SINGLE_TREE:-0}" in
    0) single_tree=false ;;
    1) single_tree=true ;;
    *) echo "DF_SINGLE_TREE must be 0 or 1" >&2; exit 1 ;;
esac
cat > "$out/ObserverBuild.java" <<JAVA
package org.droidfork.observer;
final class ObserverBuild { static final boolean SINGLE_TREE = $single_tree; }
JAVA
javac --release 8 -cp "$jar" -d "$out/classes" "$src/observer/Observer.java" "$out/ObserverBuild.java"
(cd "$out/classes" && jar cf "$out/classes.jar" .)
"$bt/d8" --min-api 30 --lib "$jar" --output "$out/dex" "$out/classes.jar"
(cd "$out/dex" && zip -q "$out/observer.apk" classes.dex)
if [ ! -f "$out/local.jks" ]; then
    keytool -genkeypair -keystore "$out/local.jks" -storepass android -keypass android -alias observer -dname CN=DroidFork -keyalg RSA -validity 3650 >/dev/null
fi
"$bt/apksigner" sign --ks "$out/local.jks" --ks-pass pass:android "$out/observer.apk"
