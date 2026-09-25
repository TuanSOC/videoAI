"""Command-line entry point."""

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from vidgen.config import Format, Lang, get_settings

# Windows consoles default to cp1252; Vietnamese text and arrows need UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def doctor() -> None:
    """Check FFmpeg, GPU, API keys and local AI services."""
    from vidgen.doctor import run_checks

    table = Table("check", "status", "detail")
    failed = False
    for c in run_checks(get_settings()):
        status = "[green]ok[/]" if c.ok else ("[red]missing[/]" if c.required else "[yellow]warn[/]")
        failed |= c.required and not c.ok
        table.add_row(c.name, status, c.detail)
    console.print(table)
    raise typer.Exit(1 if failed else 0)


def _print_script(out_dir: Path) -> None:
    from vidgen.models import Script

    sc = Script.model_validate_json((out_dir / "script.json").read_text(encoding="utf-8"))
    table = Table("#", "narration", "visual_query", "type")
    for scene in sc.scenes:
        table.add_row(str(scene.id), scene.narration, scene.visual_query, scene.visual_type)
    console.print(f"[bold]{sc.title}[/]  ({sc.word_count} words, {len(sc.scenes)} scenes)")
    console.print(table)


def _run(out_dir: Path, force: str | None) -> None:
    from vidgen.pipeline import run_stages

    def show(name: str, status: str) -> None:
        console.print(f"  {name:<9} {status}")

    try:
        timings = run_stages(out_dir, get_settings(), force=force, on_stage=show)
    except Exception as e:
        console.print(f"[red]failed:[/] {e}")
        console.print(f"fix the cause, then: [bold]vidgen resume {out_dir.name}[/]")
        raise typer.Exit(1) from e
    final = out_dir / "final.mp4"
    console.print(f"[green]done[/] in {sum(timings.values()):.0f}s → {final}")
    console.print(f"metadata → {out_dir / 'metadata.json'}")


@app.command()
def make(
    topic: str,
    format: Format = typer.Option("short", "--format", "-f"),
    lang: Lang = typer.Option("vi", "--lang", "-l"),
    review: bool = typer.Option(True, "--review/--no-review",
                                help="Stop after script.json so you can edit it, then `resume`."),
    angle: int = typer.Option(1, "--angle", "-a", min=1, max=3,
                              help="Which brief angle to write: 1 explain, 2 myth-busting, 3 story."),
) -> None:
    """Topic → brief (3 angles) → script.json (review) → voice → visuals → final.mp4 + metadata.json."""
    from vidgen.pipeline import create_script, load_brief

    out_dir = create_script(topic, format, lang, get_settings(), angle=angle - 1)
    brief = load_brief(out_dir)
    table = Table("#", "style", "title", "sources")
    for i, a in enumerate(brief.angles, 1):
        mark = "[green]✓[/] " if i - 1 == brief.chosen else ""
        table.add_row(f"{mark}{i}", a.style, a.title, ", ".join(s.title for s in a.sources) or "—")
    console.print(table)
    _print_script(out_dir)
    if review:
        console.print(f"\nreview/edit [bold]{out_dir / 'script.json'}[/] (narration, visual_query), then:")
        console.print(f"  [bold]vidgen resume {out_dir.name}[/]")
        return
    _run(out_dir, force=None)


@app.command()
def resume(
    slug: str = typer.Argument(..., help="Folder name under output/ (or a path)"),
    force: str | None = typer.Option(None, "--force", help="Redo this stage and everything after it: "
                                     "voice | visuals | render | metadata"),
) -> None:
    """Continue a video from its first missing artifact."""
    from vidgen.pipeline import STAGE_NAMES, resolve_output_dir

    if force and force not in STAGE_NAMES:
        raise typer.BadParameter(f"--force must be one of {', '.join(STAGE_NAMES)}")
    out_dir = resolve_output_dir(slug, get_settings())
    if not (out_dir / "script.json").exists():
        raise typer.BadParameter(f"no script.json in {out_dir}")
    _run(out_dir, force)


@app.command()
def ui(
    port: int = typer.Option(8000, "--port", "-p"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the browser automatically"),
) -> None:
    """Start the local web studio at http://127.0.0.1:PORT."""
    import threading
    import webbrowser

    import uvicorn

    from vidgen.web.app import create_app

    url = f"http://127.0.0.1:{port}"
    console.print(f"vidgen studio → [bold]{url}[/]  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    # 127.0.0.1 only: the API can write .env and delete videos, never expose it on the network
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


@app.command()
def version() -> None:
    """Print version."""
    from vidgen import __version__

    console.print(__version__)
