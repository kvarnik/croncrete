"""CLI: thin wrappers over the library."""

import json
from pathlib import Path

import typer

app = typer.Typer(help="Compile agent traces into checked Python.")


@app.command()
def compile(
    traces: str = typer.Option(..., help="Directory of recorded trace JSON files."),
    tools_schemas: str = typer.Option(..., help="JSON file mapping tool names to endpoints."),
    template: str = typer.Option(..., help="Reply template file."),
    sla: str = typer.Option(..., help="SLA table JSON file."),
    out: str = typer.Option(..., help="Generated workflow module path."),
    trusted_fields: str = typer.Option("input.sender", help="Comma-separated trusted roots."),
) -> None:
    """Compile traces into a workflow module."""
    from croncrete.compiler.codegen import compile as _compile

    schemas = json.loads(Path(tools_schemas).read_text())
    wf = _compile(
        traces,
        tools=schemas,
        template=template,
        sla=sla,
        trusted_fields=trusted_fields.split(","),
        out=out,
    )
    typer.echo(str(wf))


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
