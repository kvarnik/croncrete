"""CLI: thin wrappers over the library (stubs until M2-M6)."""

import typer

app = typer.Typer(help="Compile agent traces into checked Python.")


@app.command()
def compile() -> None:
    """Compile traces into a workflow module (M2-M3)."""
    raise NotImplementedError


@app.command()
def run() -> None:
    """Run a workflow module (M4)."""
    raise NotImplementedError


@app.command()
def repair() -> None:
    """Propose a one-step repair (M5)."""
    raise NotImplementedError


@app.command()
def resume() -> None:
    """Resume a halted run (M5)."""
    raise NotImplementedError


@app.command()
def report() -> None:
    """Report agent vs script (M6)."""
    raise NotImplementedError
