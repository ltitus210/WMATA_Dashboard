#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUILD="$ROOT/build"
DIST="$ROOT/dist"
JDK_BIN="${JDK_HOME:-}/bin"

if [ ! -x "$JDK_BIN/javac" ]; then
    echo "Set JDK_HOME to a Java 8 JDK." >&2
    exit 1
fi

rm -rf "$BUILD"
mkdir -p "$BUILD/classes" "$DIST"

"$JDK_BIN/javac" -source 1.4 -target 1.4 \
    -classpath "$ROOT/lib/jailbreak.jar" \
    -d "$BUILD/classes" \
    $(find "$ROOT/src" "$ROOT/stubs" -name '*.java' -print)

# The stubs are compile-time declarations only. The Kindle supplies the real
# KDK and patched json-simple classes at runtime.
rm -rf "$BUILD/classes/com/amazon" "$BUILD/classes/org/json"

(cd "$BUILD/classes" && unzip -q "$ROOT/lib/jailbreak.jar" 'ixtab/jailbreak/*.class')
cp "$ROOT/cover.jpg" "$BUILD/classes/cover.jpg"

OUTPUT="$DIST/WMATA-Dashboard-PW2-v1.7.azw2"
rm -f "$OUTPUT"
"$JDK_BIN/jar" cfm "$OUTPUT" "$ROOT/WMATA-Dashboard.manifest" -C "$BUILD/classes" .

for ALIAS in dktest ditest dntest; do
    "$JDK_BIN/jarsigner" -keystore "$ROOT/developer.keystore" \
        -storepass password -sigalg SHA256withRSA -digestalg SHA-256 \
        "$OUTPUT" "$ALIAS"
done

"$JDK_BIN/jarsigner" -verify "$OUTPUT"
echo "Built $OUTPUT"
