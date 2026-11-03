"""Core must never pull in the example's server stack."""

import sys


def test_import_croncrete_has_no_fastapi():
    import croncrete  # noqa: F401

    assert "fastapi" not in sys.modules
