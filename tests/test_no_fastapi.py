"""Core must never pull in the example's server stack."""

import subprocess
import sys


def test_import_croncrete_has_no_fastapi():
    code = "import croncrete, sys; sys.exit(1 if 'fastapi' in sys.modules else 0)"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
