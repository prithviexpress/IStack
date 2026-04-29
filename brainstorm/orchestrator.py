import sys
import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from .agents import AGENTS, AgentConfig
from .utils import format_context

console = Console()


class BrainstormOrchestrator:
    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.agent_outputs: list[dict] = []

    def _build_context(self, seed_idea: str) -> str:
        if not self.agent_outputs:
            return ""
        lines = [
            f"## ORIGINAL SEED IDEA\n{seed_idea}\n",
            "---",
            "## PANEL ANALYSIS SO FAR",
        ]
        for entry in self.agent_outputs:
            lines.append(f"\n### {entry['emoji']} {entry['name']} — {entry['role']}\n")
            lines.append(entry["output"])
        return "\n".join(lines)

    def _run_agent(self, agent: AgentConfig, seed_idea: str, ctx_block: str = "") -> str:
        panel_ctx = self._build_context(seed_idea)

        if panel_ctx:
            user_content = (
                f"## ORIGINAL SEED IDEA\n{seed_idea}\n\n"
                + (f"{ctx_block}\n\n" if ctx_block else "")
                + f"---\n\n{panel_ctx}\n\n---\n\n"
                + "Now provide your analysis of the seed idea above, informed by the panel's work so far."
            )
        else:
            user_content = f"## SEED IDEA\n\n{seed_idea}"
            if ctx_block:
                user_content += f"\n\n{ctx_block}"

        create_kwargs: dict = {
            "model": "claude-opus-4-7",
            "max_tokens": agent.max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": agent.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user_content}],
            "output_config": {"effort": agent.effort},
        }

        if agent.use_thinking:
            create_kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}

        full_output = []
        with self.client.messages.stream(**create_kwargs) as stream:
            for text in stream.text_stream:
                sys.stdout.write(text)
                sys.stdout.flush()
                full_output.append(text)

        print()  # newline after stream ends
        return "".join(full_output)

    def run(self, seed_idea: str, context: dict | None = None) -> list[dict]:
        ctx_block = format_context(context or {})

        console.print()
        console.print(
            Panel(
                f"[bold white]{seed_idea}[/bold white]",
                title="[bold cyan]SEED IDEA[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
        if ctx_block:
            console.print(Panel(ctx_block, border_style="dim", padding=(0, 2)))
        console.print()

        for agent in AGENTS:
            _print_agent_header(agent)

            output = self._run_agent(agent, seed_idea, ctx_block)

            self.agent_outputs.append(
                {
                    "name": agent.name,
                    "emoji": agent.emoji,
                    "role": agent.role,
                    "color": agent.color,
                    "output": output,
                }
            )

            console.print()

        return self.agent_outputs


def _print_agent_header(agent: AgentConfig) -> None:
    title = Text()
    title.append(f"{agent.emoji}  ", style="bold")
    title.append(agent.name, style=f"bold {agent.color}")
    title.append(f"  ·  {agent.role}", style="dim")

    console.print(Rule(style=agent.color))
    console.print(Panel(title, border_style=agent.color, padding=(0, 2)))
    console.print()
