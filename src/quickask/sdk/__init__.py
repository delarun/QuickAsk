# QuickAsk — https://github.com/delarun/quickask — MIT
"""SDK для MCP-серверов, которые расширяют интерфейс QuickAsk.

    from quickask.sdk import Server, Result, ui

    srv = Server("notes")

    @srv.tool("add_note", "Сохранить заметку", {"text": {"type": "string"}}, ["text"])
    def add_note(text: str) -> Result:
        NOTES.append(text)
        return Result("OK: сохранил", [ui.open("notes", label="Открыть заметки")])

    @srv.view("notes")
    def notes() -> dict:
        return ui.page("Заметки", color="teal", blocks=[ui.card(ui.line(n)) for n in NOTES])

    srv.button("Заметки", ui.open("notes"))

    if __name__ == "__main__":
        srv.run()

Только стандартная библиотека. Протокол — в protocol.py, описание — в README.md рядом.
"""
from . import protocol, ui
from .server import Result, Server

__all__ = ["Server", "Result", "ui", "protocol"]
