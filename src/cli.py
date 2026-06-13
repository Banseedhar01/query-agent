"""CLI entry point: `agent ingest` and `agent review`."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

from src.config import load_config

app = typer.Typer(
    name="agent",
    help="Impala SQL Query Review & Optimization Agent",
    add_completion=False,
)
console = Console()


def _setup_logging(level: str = "INFO") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-5s  %(name)-30s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Silence noisy third-party internals — show WARNING+ only regardless of level
    for noisy in ("httpcore", "httpx", "openai._base_client", "openai.http_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# ingest command
# ---------------------------------------------------------------------------

@app.command()
def ingest(
    excel: Path = typer.Argument(..., help="Path to the primary mapping Excel workbook (Sheet 1)"),
    mart: Optional[Path] = typer.Option(None, "--mart", "-m", help="Path to a separate Mart/Org Excel workbook (Sheet 2), if in a different file"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="config.yaml path"),
    db: Optional[str] = typer.Option(None, "--db", help="DuckDB path override"),
) -> None:
    """Load/refresh metadata from Excel workbook(s) into DuckDB."""
    cfg = load_config(config_file)
    _setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    db_path = db or cfg["duckdb"]["path"]

    if not excel.exists():
        console.print(f"[red]Error:[/red] File not found: {excel}")
        raise typer.Exit(1)

    if mart is not None and not mart.exists():
        console.print(f"[red]Error:[/red] Mart file not found: {mart}")
        raise typer.Exit(1)

    msg = f"[bold]Ingesting[/bold] {excel}"
    if mart:
        msg += f" + {mart}"
    msg += f" → {db_path}"
    console.print(msg)

    import time
    from src.ingestion.excel_loader import load_excel
    t0 = time.perf_counter()
    try:
        counts = load_excel(excel, db_path=db_path, mart_path=mart)
        elapsed = time.perf_counter() - t0
        table = Table(title="Ingestion Results", show_header=True)
        table.add_column("Table", style="cyan")
        table.add_column("Rows", justify="right", style="green")
        for tbl, rows in counts.items():
            table.add_row(tbl, str(rows))
        console.print(table)
        console.print(f"[green]Done in {elapsed:.1f}s[/green]")
    except Exception as exc:
        console.print(f"[red]Ingestion failed:[/red] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# review command
# ---------------------------------------------------------------------------

@app.command()
def review(
    query_file: Path = typer.Argument(..., help="Path to .sql file"),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write JSON report to this path"),
    offline: bool = typer.Option(False, "--offline", help="Skip Impala cluster calls"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="config.yaml path"),
    db: Optional[str] = typer.Option(None, "--db", help="DuckDB path override"),
) -> None:
    """Run full query review pipeline and print the report."""
    cfg = load_config(config_file)
    _setup_logging(cfg.get("logging", {}).get("level", "INFO"))
    db_path = db or cfg["duckdb"]["path"]

    if not query_file.exists():
        console.print(f"[red]Error:[/red] Query file not found: {query_file}")
        raise typer.Exit(1)

    sql = query_file.read_text(encoding="utf-8").strip()
    if not sql:
        console.print("[red]Error:[/red] Query file is empty")
        raise typer.Exit(1)

    console.print(Panel(
        f"Reviewing [cyan]{query_file}[/cyan] "
        f"({'offline' if offline else 'online'}, db={db_path})",
        title="Impala Query Review Agent",
    ))

    from src.agent.graph import run_review
    try:
        final_state = run_review(sql, cfg, db_path=db_path, offline=offline)
    except Exception as exc:
        console.print(f"[red]Review pipeline failed:[/red] {exc}")
        logging.exception("Pipeline error")
        raise typer.Exit(1)

    report = final_state.get("report")
    if report is None:
        console.print("[yellow]Warning:[/yellow] No report generated")
        raise typer.Exit(1)

    _print_report(report)

    if json_out:
        json_out.write_text(report.to_json(), encoding="utf-8")
        console.print(f"\n[green]JSON report written:[/green] {json_out}")


def _print_report(report: Any) -> None:
    from src.report.schema import ReviewReport, Severity
    assert isinstance(report, ReviewReport)

    console.print(f"\n[bold]Query Hash:[/bold] {report.query_hash}")
    console.print(f"[bold]Metadata Coverage:[/bold] {report.metadata_coverage:.1%}")

    if report.pii_flags:
        console.print(Panel(
            "\n".join(report.pii_flags),
            title="[red]PII Flags[/red]",
            border_style="red",
        ))

    if not report.issues:
        console.print("[green]No issues found.[/green]")
        return

    severity_colors = {
        Severity.LOW: "blue",
        Severity.MEDIUM: "yellow",
        Severity.HIGH: "red",
        Severity.CRITICAL: "bold red",
    }

    table = Table(title=f"Issues ({len(report.issues)} total)", show_header=True, show_lines=True)
    table.add_column("Rule", style="cyan", no_wrap=True)
    table.add_column("Severity", justify="center")
    table.add_column("Issue", max_width=60)
    table.add_column("Verified", justify="center")

    for issue in report.issues:
        color = severity_colors.get(issue.severity, "white")
        table.add_row(
            issue.evidence_from_plan[:40] if issue.evidence_from_plan else "",
            f"[{color}]{issue.severity.value}[/{color}]",
            issue.issue,
            "[green]✓[/green]" if issue.verified else "[dim]–[/dim]",
        )
    console.print(table)

    if report.validated_rewrites:
        for i, vr in enumerate(report.validated_rewrites, 1):
            console.print(Panel(
                f"[bold]Rationale:[/bold] {vr.rationale}\n\n"
                f"[bold]Targets:[/bold] {', '.join(vr.targets_finding_ids)}\n\n"
                f"[bold]Verdict:[/bold] {vr.verdict} "
                f"{'[green](verified)[/green]' if vr.verified else '[dim](unverified)[/dim]'}\n\n"
                f"```sql\n{vr.candidate_sql}\n```",
                title=f"Rewrite #{i}",
            ))


# Allow `from src.cli import app` type hints
from typing import Any
