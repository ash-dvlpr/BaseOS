#!/bin/sh
# Verify the exact load arguments without touching host audio or loading code.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
export BASEOS_AUDIO_CONFIG="$TMP/config"
export BASEOS_AUDIO_MODULE="$TMP/module.ko"
export BASEOS_AUDIO_INSMOD="$TMP/insmod"
export BASEOS_AUDIO_TEST_ARGS="$TMP/args"
cat > "$TMP/insmod" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" > "$BASEOS_AUDIO_TEST_ARGS"
exit "${BASEOS_AUDIO_TEST_FAIL:-0}"
EOF
chmod +x "$TMP/insmod"
loader="$HERE/overlay/usr/sbin/h700-speaker-amp-load"
sh "$loader"
[ ! -e "$TMP/args" ] # No module available: harmless skip.
touch "$TMP/module.ko"
check() {
    sh "$loader"
    retain=0
    [ "$1" != true ] || retain=1
    printf '%s\n' "$TMP/module.ko" apply=1 "headphone_pop_fix=$retain" > "$TMP/want"
    cmp "$TMP/want" "$TMP/args"
}
check false # Missing config.
printf 'hostname=rgsp\nmdns=true\n' > "$TMP/config"
check false # Missing key.
printf 'headphone_pop_fix=true\n' > "$TMP/config"
check true
printf 'headphone_pop_fix=false\n' > "$TMP/config"
check false
printf 'headphone_pop_fix=true\nheadphone_pop_fix=invalid\n' > "$TMP/config"
check false
printf 'headphone_pop_fix=$(touch %s)\n' "$TMP/injected" > "$TMP/config"
check false
[ ! -e "$TMP/injected" ]
export BASEOS_AUDIO_TEST_FAIL=7
status=0
sh "$loader" || status=$?
[ "$status" -eq 7 ]
# Exercise the rcS handoff: audio attachment must finish before frontends can
# run, and USB storage maintenance must skip it entirely.
awk '/^if .*USB_STORAGE_MODE.*h700-speaker-amp-load/ { copying=1 }
     copying { print }
     copying && /^fi$/ { exit }' "$HERE/overlay/etc/init.d/rcS" \
    | sed "s#/usr/sbin/h700-speaker-amp-load#$TMP/boot-loader#g; s#/run/h700-speaker-amp.log#$TMP/boot.log#g" \
    > "$TMP/boot-audio"
[ -s "$TMP/boot-audio" ]
cat > "$TMP/boot-loader" <<'EOF'
#!/bin/sh
sleep 0.05
: > "$BASEOS_AUDIO_TEST_READY"
EOF
chmod +x "$TMP/boot-loader"
export BASEOS_AUDIO_TEST_READY="$TMP/ready"
# An asynchronous loader would return before the ready marker exists.
( USB_STORAGE_MODE=0; . "$TMP/boot-audio"; [ -f "$TMP/ready" ] )
rm "$TMP/ready"
( USB_STORAGE_MODE=1; . "$TMP/boot-audio"; [ ! -e "$TMP/ready" ] )
echo 'h700-speaker-amp-load tests passed'
