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
def run(
    workflow: str = typer.Option(..., help="Compiled workflow module path."),
    input: str = typer.Option(..., help="Input email JSON file."),
    base_url: str = typer.Option(..., help="Mock services base URL."),
    state_db: str = typer.Option(".croncrete/state.db", help="Checkpoint database."),
) -> None:
    """Run a workflow module."""
    from croncrete.runtime.executor import load_workflow
    from croncrete.runtime.executor import run as _run
    from croncrete.runtime.tools_client import ToolsClient

    email = json.loads(Path(input).read_text())
    mod = load_workflow(workflow)
    result = _run(workflow, email, tools=ToolsClient(base_url, list(mod.SCOPES)), state_db=state_db)
    typer.echo(f"{result.status} {result.run_id}")
    if result.status != "completed":
        raise typer.Exit(code=1)


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
