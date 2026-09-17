"""Importer fixture has no executable firmware: stub only gzip derivation."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import source_manifest as source

root = Path(sys.argv[1])
prefix = root / "out1/boot-prefix.img"
data = source.load(root / "out1/source.json", "rg40xxv")
p4 = source.partition(data["layout"], 4)
with prefix.open("rb") as handle:
    handle.seek(p4["start_sector"] * 512)
    boot = handle.read(p4["sector_count"] * 512)
identity = dict(expected_boot=boot, profile=dict(package_offset=2048), package=b"")
with patch.object(source, "derive_gzip", return_value=identity):
    source.verify_composed(data, prefix, root / "composed.img", root / "logo.bmp")
