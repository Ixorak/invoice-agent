"""Command line: python -m invoice_agent {run,watch,demo}."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.text import Text

from . import __version__
from .backends import BackendUnavailable, create_backend
from .config import Config, ConfigError, Workspace, load_config
from .console import ConsoleReporter, export, make_console, summary_table, totals_line
from .demo import SAMPLES_DIR, DemoFolderError, copy_samples, markdown_report, print_evaluation, reset_demo_folder
from .evaluation import evaluate
from .excel import ExcelLockedError
from .locking import AlreadyRunning, FolderLock
from .pipeline import Agent, BatchResult, list_pdfs
from .watcher import watch


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", type=Path, help="path to config.json (default: ./config.json)")
    common.add_argument("--export-console", type=Path, metavar="FILE",
                        help="also save the console output to FILE (.svg, .html or .txt)")
    common.add_argument("--width", type=int, help="console width in characters (useful with --export-console)")

    parser = argparse.ArgumentParser(
        prog="python -m invoice_agent",
        description="Reads supplier invoices (PDF) with an AI, checks them with Python rules, "
                    "writes Excel + JSON and lists what needs a person.",
    )
    parser.add_argument("--version", action="version", version=f"invoice-agent {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="{run,watch,demo}")
    sub.add_parser("run", parents=[common], help="process every PDF in the inbox once")
    p_watch = sub.add_parser("watch", parents=[common], help="watch the inbox and process new PDFs (Ctrl+C to stop)")
    p_watch.add_argument("--interval", type=float, help="seconds between two checks (default: watch_interval_seconds)")
    p_demo = sub.add_parser("demo", parents=[common],
                            help="clean demo workspace, process the 10 fictional samples, compare with expected.json")
    p_demo.add_argument("--eval-report", type=Path, metavar="FILE",
                        help="also write the evaluation (Markdown) to FILE, e.g. docs/eval.md")
    return parser


def _short(path: Path) -> str:
    """Path relative to the current folder when it is inside it."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _header(console: Console, agent: Agent, what: str) -> None:
    info = agent.backend_info
    console.print(Text.assemble(
        (f"invoice-agent {__version__}", "bold"),
        (f"  |  Claude Code {info.get('claude_code_version')}  |  model {info.get('model_setting') or 'default'}", "dim"),
        (f"  |  {what}", "dim"),
    ))


def _summary(console: Console, batch: BatchResult, workspace: Workspace) -> None:
    if not batch.results:
        return
    console.print()
    console.print(summary_table(batch.results))
    console.print(totals_line(batch.results))
    if batch.processed:
        for label, path in (("Excel", workspace.excel_path), ("JSON", workspace.json_dir),
                            ("OK", workspace.processed), ("Review", workspace.review)):
            console.print(Text.assemble((f"  {label:<7}", "dim"), (_short(path), "dim")))
    if batch.error:
        console.print(Text(f"Stopped: {batch.error}", style="bold red"))


def cmd_run(console: Console, config: Config, base_dir: Path) -> int:
    workspace = Workspace.from_config(config, base_dir)
    workspace.create()
    with FolderLock(workspace.lock_path):
        agent = Agent(config, workspace, create_backend(config), ConsoleReporter(console))
        files = list_pdfs(workspace.inbox)
        _header(console, agent, f"{len(files)} PDF(s) in {_short(workspace.inbox)}")
        if not files:
            console.print(f"Nothing to do: put PDF invoices in {_short(workspace.inbox)}")
            return 0
        batch = agent.process_inbox(files)
        _summary(console, batch, workspace)
        return 1 if batch.error else 0


def cmd_watch(console: Console, config: Config, base_dir: Path, interval: float | None) -> int:
    workspace = Workspace.from_config(config, base_dir)
    workspace.create()
    interval = interval or config.watch_interval_seconds
    with FolderLock(workspace.lock_path):
        agent = Agent(config, workspace, create_backend(config), ConsoleReporter(console))
        _header(console, agent, f"watching {_short(workspace.inbox)} every {interval:g} s, Ctrl+C to stop")

        def on_batch(batch: BatchResult) -> None:
            _summary(console, batch, workspace)
            if batch.error:
                console.print(Text("Will try again later (waiting longer after each failure).", style="yellow"))

        try:
            watch(agent, interval, on_batch)
        except KeyboardInterrupt:
            console.print("\nStopped.")
        return 0


def cmd_demo(console: Console, config: Config, base_dir: Path, eval_report: Path | None) -> int:
    root = Path(config.demo_folder).expanduser()
    root = (root if root.is_absolute() else base_dir / root).resolve()
    reset_demo_folder(root)
    workspace = Workspace.under(root)
    workspace.create()
    with FolderLock(workspace.lock_path):
        agent = Agent(config, workspace, create_backend(config), ConsoleReporter(console))
        files = copy_samples(workspace.inbox)
        _header(console, agent, f"demo: {len(files)} fictional sample invoices copied to {_short(workspace.inbox)}")
        started = datetime.now()
        batch = agent.process_inbox(files)
        _summary(console, batch, workspace)
        if batch.error:
            return 1
        report = evaluate(batch.results, SAMPLES_DIR / "expected.json")
        print_evaluation(console, report)
        models = sorted({a.meta.get("model") for r in batch.results for a in r.attempts if a.meta.get("model")})
        markdown = markdown_report(report, backend_info=agent.backend_info, models=models, started=started)
        targets = [workspace.output / "eval.md"] + ([eval_report] if eval_report else [])
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8")
        console.print(Text(f"Evaluation written to {', '.join(_short(Path(t).resolve()) for t in targets)}", style="dim"))
        return 0 if report.status_correct == len(report.invoices) else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    console = make_console(record=bool(args.export_console), width=args.width)
    code = 1
    try:
        config, base_dir = load_config(args.config)
        if args.command == "run":
            code = cmd_run(console, config, base_dir)
        elif args.command == "watch":
            code = cmd_watch(console, config, base_dir, args.interval)
        else:
            code = cmd_demo(console, config, base_dir, args.eval_report)
    except (ConfigError, BackendUnavailable, DemoFolderError) as exc:
        console.print(Text(f"Error: {exc}", style="bold red"))
        code = 2
    except (AlreadyRunning, ExcelLockedError) as exc:
        console.print(Text(f"Error: {exc}", style="bold red"))
        code = 1
    except KeyboardInterrupt:
        console.print("\nInterrupted. Files that were not finished are still in the inbox.")
        code = 130
    finally:
        if args.export_console:
            export(console, args.export_console)
    return code
