"""Terminal chat. Run with `python -m meta_agent`."""

from __future__ import annotations

import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm

from .agent import Agent
from .config import load_settings
from .guard import Guard
from .meta_client import make_backend
from .tools import build_tools

console = Console()

HELP = """Commands:
  /accounts   list ad accounts (free, no Claude call)
  /cost       tokens and USD spent this session
  /new        forget the conversation so far
  /help       this text
  /quit       exit
Anything else is sent to the agent."""


# The live "thinking..." spinner must be paused while we wait for a keypress,
# otherwise its redraws swallow the operator's input.
_status = None


def approve(request) -> bool:
    if _status is not None:
        _status.stop()
    try:
        console.print(Panel(request.text, title="[bold yellow]Approve this write?[/]", border_style="yellow"))
        return Confirm.ask("Proceed (y/n)", default=False)
    finally:
        if _status is not None:
            _status.start()


def main() -> int:
    # Windows consoles often run a legacy code page (cp1255 here); Hebrew and
    # symbols in Claude's replies must not crash the loop.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    settings = load_settings()
    backend = make_backend(settings)
    guard = Guard(settings, approver=approve)
    tools = build_tools(backend, guard)
    agent = Agent(settings, tools, on_text=lambda t: console.print(Markdown(t), style="dim"))

    mode = "[bold red]DRY RUN[/] (no META_ACCESS_TOKEN, using fake data)" if settings.dry_run \
        else f"[bold green]LIVE[/] Meta API {settings.meta_api_version or 'SDK default'}"
    console.print(Panel(
        f"Meta Ads agent  |  {mode}\n"
        f"Model: {settings.model} (effort {settings.effort})\n"
        f"Writes allowed on: {', '.join(settings.write_accounts) or '(none)'}   "
        f"Budget cap: {settings.max_daily_budget_minor} minor units\n"
        f"Log: {settings.log_path}",
        title="meta-ads-agent", border_style="blue"))
    console.print(HELP, style="dim")

    while True:
        try:
            text = console.input("[bold cyan]you>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not text:
            continue
        if text in ("/quit", "/exit", "/q"):
            break
        if text == "/help":
            console.print(HELP, style="dim")
            continue
        if text == "/new":
            agent.reset()
            console.print("Conversation cleared.", style="dim")
            continue
        if text == "/cost":
            u = agent.session_usage
            console.print(f"in {u.input_tokens:,}  out {u.output_tokens:,}  cache read {u.cache_read:,}  "
                          f"cache write {u.cache_write:,}  ~ ${u.usd(settings.model):.4f}")
            continue
        if text == "/accounts":
            for a in backend.list_ad_accounts():
                tag = "write" if a["id"] in settings.write_accounts else "read-only"
                console.print(f"  {a['id']}  {a.get('name')}  [{a.get('currency')}]  {tag}")
            continue

        global _status
        _status = console.status("thinking...", spinner="dots")
        try:
            _status.start()
            reply, turn = agent.send(text)
        except KeyboardInterrupt:
            console.print()
            console.print("[dim]turn aborted[/]")
            continue
        except Exception as e:  # keep the REPL alive; show the real error
            console.print(f"[red]Error:[/] {type(e).__name__}: {e}")
            continue
        finally:
            _status.stop()
            _status = None
        console.print(Markdown(reply))
        console.print(f"[dim]turn: {turn.input_tokens + turn.cache_read + turn.cache_write:,} in / "
                      f"{turn.output_tokens:,} out ~ ${turn.usd(settings.model):.4f}  "
                      f"session ~ ${agent.session_usage.usd(settings.model):.4f}[/]")

    u = agent.session_usage
    console.print(f"Session total ~ ${u.usd(settings.model):.4f}", style="dim")
    return 0


if __name__ == "__main__":
    sys.exit(main())
