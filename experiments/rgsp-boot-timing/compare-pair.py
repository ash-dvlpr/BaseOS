#!/usr/bin/env python3
import importlib.util
import sys

spec = importlib.util.spec_from_file_location('measure', __file__.replace('compare-pair.py', 'measure-reboots.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
variant = sys.argv[1]
expected = {'gzip': '83b6e8a62cf1d0479dd8a9132d2656af00c8aa15c27cdd583d29da6509071233', 'original': 'd42252f30730d35ca7759a651da7260ba482144130e58f05f23ea5e33270a6f5'}[variant]
assert m.shell('sha256sum /dev/mmcblk0p4').split()[0] == expected
for n in range(1, 4):
    label = f'{variant}-logged-{n}'
    assert not (m.OUT / (label + '.json')).exists()
    print(f'{variant} timing boot {n}/3 via NextUI reboot_next', flush=True)
    m.reboot()
    m.sample(label)
print(f'{variant} three handoff measurements complete.', flush=True)
