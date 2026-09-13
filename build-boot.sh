#!/bin/sh
# Build an opt-in profiled boot partition from one target's preserved firmware.
# This does not change boot-prefix.img, a composed release image, or a device.
# Usage: ./build-boot.sh <target> [minimal|baseline]
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:?usage: $0 <target> [minimal|baseline]}"
VARIANT="${2:-minimal}"
[ "$#" -le 2 ] || { echo "usage: $0 <target> [minimal|baseline]" >&2; exit 2; }
case "$VARIANT" in
  minimal|baseline) ;;
  *) echo "variant must be minimal or baseline" >&2; exit 2 ;;
esac
python3 "$HERE/tools/device_profile.py" shell "$TARGET" >/dev/null
WORK="$HERE/work/$TARGET"
python3 "$HERE/tools/source_manifest.py" verify "$WORK/source.json" "$TARGET"

# Resolve p4 from this target's manifest; never borrow another model's kernel
# or device tree. The extracted original is kept next to the derived image.
python3 - "$WORK" <<'PY'
import json
from pathlib import Path
import sys

work = Path(sys.argv[1])
manifest = json.loads((work / 'source.json').read_text())
part = next(p for p in manifest['layout']['partitions'] if p['number'] == 4)
if part['name'] != 'boot':
    raise SystemExit('partition 4 must be the vendor boot partition')
size = part['sector_count'] * 512
with (work / 'boot-prefix.img').open('rb') as source:
    source.seek(part['start_sector'] * 512)
    boot = source.read(size)
if len(boot) != size:
    raise SystemExit('truncated boot partition in the prepared prefix')
(work / 'boot-original.img').write_bytes(boot)
PY

python3 "$HERE/tools/minimal_initramfs.py" \
  --input-boot "$WORK/boot-original.img" \
  --output-boot "$WORK/boot-$VARIANT.img" \
  --variant "$VARIANT" \
  --output-cpio "$WORK/initramfs-$VARIANT.cpio" \
  --report "$WORK/boot-$VARIANT.json"

echo "Boot partition: $WORK/boot-$VARIANT.img"
echo "This is a separate experimental p4 image; .bosupd updates only the rootfs."
