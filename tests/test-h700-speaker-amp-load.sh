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
echo 'h700-speaker-amp-load tests passed'
