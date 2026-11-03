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
def repair(
    run_id: str = typer.Option(..., "--run-id", help="Halted run to repair."),
    approve: bool = typer.Option(False, "--approve", help="Apply the patch."),
    state_db: str = typer.Option(".croncrete/state.db", help="Checkpoint database."),
) -> None:
    """Propose a one-step repair."""
    from croncrete.repair.splice import repair as _repair

    proposal = _repair(run_id, state_db=state_db)
    typer.echo(proposal.diff)
    typer.echo(proposal.test_results)
    if approve:
        proposal.approve()
        typer.echo(f"applied to run {run_id}")


@app.command()
def resume(
    run_id: str = typer.Option(..., "--run-id", help="Halted run to resume."),
    base_url: str = typer.Option(..., help="Mock services base URL."),
    state_db: str = typer.Option(".croncrete/state.db", help="Checkpoint database."),
) -> None:
    """Resume a halted run."""
    import sqlite3

    from croncrete.runtime.executor import load_workflow
    from croncrete.runtime.executor import resume as _resume
    from croncrete.runtime.tools_client import ToolsClient

    con = sqlite3.connect(state_db)
    row = con.execute("SELECT workflow FROM runs WHERE run_id=?", (run_id,)).fetchone()
    con.close()
    if row is None:
        raise typer.BadParameter(f"unknown run_id: {run_id}")
    mod = load_workflow(row[0])
    result = _resume(run_id, tools=ToolsClient(base_url, list(mod.SCOPES)), state_db=state_db)
    typer.echo(f"{result.status} {result.run_id}")
    if result.status != "completed":
        raise typer.Exit(code=1)


@app.command()
def report(
    traces: str = typer.Option(..., help="Directory of recorded trace JSON files."),
    workflow: str = typer.Option(..., help="Compiled workflow module path."),
    out: str = typer.Option("report.md", help="Markdown report path."),
) -> None:
    """Report agent vs script."""
    from croncrete.report import report as _report

    md = _report(traces, workflow).to_markdown()
    typer.echo(md)
    Path(out).write_text(md)
