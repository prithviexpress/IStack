#!/usr/bin/env python3
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from brainstorm.agents import AGENTS
from brainstorm.orchestrator import BrainstormOrchestrator

load_dotenv()
console = Console()


def _print_banner() -> None:
    banner = Text()
    banner.append("  IdeaStack  ", style="bold white on blue")
    banner.append("  AI Brainstorming Collective", style="bold cyan")

    console.print()
    console.print(Panel(banner, border_style="blue", padding=(1, 4)))
    console.print()

    console.print("[dim]Your panel of thinkers:[/dim]")
    for agent in AGENTS:
        console.print(
            f"  {agent.emoji}  [bold {agent.color}]{agent.name}[/bold {agent.color}]"
            f"  [dim]·[/dim]  [dim]{agent.role}[/dim]"
        )
    console.print()


def _save_session(seed_idea: str, outputs: list[dict], path: Path) -> None:
    lines = [
        f"# IdeaStack Session — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"## Seed Idea",
        "",
        seed_idea,
        "",
        "---",
        "",
    ]
    for entry in outputs:
        lines.append(f"## {entry['emoji']} {entry['name']} — {entry['role']}")
        lines.append("")
        lines.append(entry["output"])
        lines.append("")
        lines.append("---")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"\n[dim]Session saved to[/dim] [bold]{path}[/bold]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="IdeaStack — AI multi-agent brainstorming collective"
    )
    parser.add_argument(
        "idea",
        nargs="?",
        help="Seed idea to brainstorm (prompted if omitted)",
    )
    parser.add_argument(
        "--save",
        metavar="FILE",
        help="Save the session output to a Markdown file",
    )
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[bold red]Error:[/bold red] ANTHROPIC_API_KEY is not set.")
        console.print("Copy [bold].env.example[/bold] to [bold].env[/bold] and add your key.")
        sys.exit(1)

    _print_banner()

    if args.idea:
        seed_idea = args.idea.strip()
    else:
        seed_idea = console.input("[bold cyan]Enter your seed idea:[/bold cyan] ").strip()
        if not seed_idea:
            console.print("[red]No idea provided. Exiting.[/red]")
            sys.exit(1)

    orchestrator = BrainstormOrchestrator(api_key=api_key)
    outputs = orchestrator.run(seed_idea)

    if args.save:
        _save_session(seed_idea, outputs, Path(args.save))


if __name__ == "__main__":
    main()
