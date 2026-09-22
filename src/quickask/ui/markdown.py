# QuickAsk — https://github.com/delarun/quickask — MIT
"""Рендер Markdown в Gtk.TextBuffer через TextTag-и, без WebKit."""
from __future__ import annotations

import re

from gi.repository import Gtk, Pango

class MdRenderer:
    """Рендер Markdown в Gtk.TextBuffer через TextTag'и (без WebKit).

    Блоки: заголовки, абзацы, ```код``` (с подписью языка), списки (-, *, 1.),
    цитаты, таблицы, ---. Инлайн: **bold**, *italic*, `code`, ~~strike~~,
    [text](url) и голые URL (кликабельны).
    Стриминг: вызываем render(весь_markdown) на каждом флаше — для ответов
    в несколько сотен токенов это дёшево, а незакрытые конструкции просто
    показываются как есть, пока не закроются.
    """

    INLINE = re.compile(
        r"(?P<code>`+)(?P<code_t>.+?)(?P=code)"
        r"|\*\*(?P<bold>.+?)\*\*"
        r"|__(?P<bold2>.+?)__"
        r"|~~(?P<strike>.+?)~~"
        r"|(?<![\w*])\*(?!\s)(?P<ital>[^*]+?)(?<!\s)\*(?![\w*])"
        r"|(?<!\w)_(?!\s)(?P<ital2>[^_]+?)(?<!\s)_(?!\w)"
        r"|\[(?P<link_t>[^\]]+)\]\((?P<link_u>[^)\s]+)\)"
        r"|(?P<url>https?://[^\s<>()\[\]\"']+)",
        re.S,
    )
    LIST_RE = re.compile(r"^(\s*)(?:([-*+])|(\d+)[.)])\s+(.*)$")
    HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
    HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
    TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

    PAD = 12          # общие поля у блочных плашек (код, цитата, таблица)

    def __init__(self, buf: Gtk.TextBuffer):
        self.buf = buf
        t, PAD = buf.create_tag, self.PAD
        self.tags = {
            "h1": t("h1", weight=700, scale=1.45, pixels_above_lines=10, pixels_below_lines=4),
            "h2": t("h2", weight=700, scale=1.25, pixels_above_lines=8, pixels_below_lines=3),
            "h3": t("h3", weight=700, scale=1.1, pixels_above_lines=6, pixels_below_lines=2),
            "bold": t("bold", weight=700),
            "ital": t("ital", style=Pango.Style.ITALIC),
            "strike": t("strike", strikethrough=True),
            "code": t("code", family="monospace", background="#313244", foreground="#f5c2e7", scale=0.92),
            "codeblock": t("codeblock", family="monospace", scale=0.9, foreground="#cdd6f4",
                           paragraph_background="#181825", left_margin=PAD, right_margin=PAD,
                           pixels_above_lines=1, pixels_below_lines=1),
            "codelang": t("codelang", family="monospace", scale=0.75, foreground="#6c7086",
                          paragraph_background="#181825", left_margin=PAD, right_margin=PAD,
                          pixels_above_lines=8),
            "codepad": t("codepad", paragraph_background="#181825", scale=0.55,
                         left_margin=PAD, right_margin=PAD),
            "quote": t("quote", style=Pango.Style.ITALIC, foreground="#a6adc8",
                       left_margin=PAD + 6, right_margin=PAD, paragraph_background="#232334",
                       pixels_above_lines=7, pixels_below_lines=7),
            "list": t("list", left_margin=6),
            "bullet": t("bullet", foreground="#89b4fa", weight=700),
            "hr": t("hr", foreground="#45475a", justification=Gtk.Justification.CENTER,
                    pixels_above_lines=4, pixels_below_lines=4),
            "table": t("table", family="monospace", scale=0.88, paragraph_background="#181825",
                       left_margin=PAD, right_margin=PAD, pixels_above_lines=3, pixels_below_lines=3),
            # заголовок таблицы — цветом и подложкой, а не жирным: синтетический bold шире
            # моноширинного начертания и ломает выравнивание столбцов
            "thead": t("thead", foreground="#89b4fa", paragraph_background="#232334"),
            "para": t("para", pixels_below_lines=4),
        }
        self.links: dict[str, str] = {}   # tag name → url
        self._link_n = 0

    # -- публичное ------------------------------------------------------------
    def render(self, md: str) -> None:
        buf = self.buf
        tt = buf.get_tag_table()
        for name in self.links:
            tag = tt.lookup(name)
            if tag:
                tt.remove(tag)
        self.links.clear()
        buf.set_text("")

        lines = md.split("\n")
        i, n = 0, len(lines)
        prev_blank = True
        while i < n:
            line = lines[i]
            s = line.strip()

            if s.startswith("```"):
                lang = s[3:].strip()
                j = i + 1
                code = []
                while j < n and not lines[j].strip().startswith("```"):
                    code.append(lines[j])
                    j += 1
                self._block_code("\n".join(code), lang)
                i = j + 1
                prev_blank = False
                continue

            if not s:
                if not prev_blank:
                    self._nl()
                prev_blank = True
                i += 1
                continue
            prev_blank = False

            if self.HR_RE.match(line):
                self._ins("─" * 40 + "\n", ["hr"])
                i += 1
                continue

            m = self.HEAD_RE.match(line)
            if m:
                lvl = min(len(m.group(1)), 3)
                self._inline(m.group(2), [f"h{lvl}"])
                self._nl()
                i += 1
                continue

            if s.startswith(">"):
                q = []
                while i < n and lines[i].strip().startswith(">"):
                    q.append(lines[i].strip()[1:].strip())
                    i += 1
                self._inline(" ".join(q), ["quote"])
                self._nl()
                continue

            if "|" in line and i + 1 < n and self.TABLE_SEP_RE.match(lines[i + 1]):
                rows = [self._split_row(line)]
                i += 2
                while i < n and "|" in lines[i] and lines[i].strip():
                    rows.append(self._split_row(lines[i]))
                    i += 1
                self._block_table(rows)
                continue

            m = self.LIST_RE.match(line)
            if m:
                indent = len(m.group(1).replace("\t", "  ")) // 2
                bullet = "•" if m.group(2) else f"{m.group(3)}."
                text = m.group(4)
                # строки-продолжения (с отступом, не новый пункт)
                while (i + 1 < n and lines[i + 1].strip()
                       and lines[i + 1].startswith(" ") and not self.LIST_RE.match(lines[i + 1])
                       and not lines[i + 1].strip().startswith("```")):
                    text += " " + lines[i + 1].strip()
                    i += 1
                self._ins("    " * indent + bullet + " ", ["list", "bullet"])
                self._inline(text, ["list"])
                self._nl()
                i += 1
                continue

            # абзац: склеиваем соседние непустые строки без блочных маркеров
            para = [line.rstrip()]
            while (i + 1 < n and lines[i + 1].strip() and not self._is_block_start(lines[i + 1], i + 1, lines)):
                para.append(lines[i + 1].rstrip())
                i += 1
            self._inline(" ".join(p.strip() for p in para), ["para"])
            self._nl()
            i += 1

        self._strip_tail()

    # -- служебное ------------------------------------------------------------
    def _is_block_start(self, line: str, idx: int, lines: list[str]) -> bool:
        s = line.strip()
        return (s.startswith("```") or s.startswith(">") or bool(self.HEAD_RE.match(line))
                or bool(self.HR_RE.match(line)) or bool(self.LIST_RE.match(line))
                or ("|" in line and idx + 1 < len(lines) and bool(self.TABLE_SEP_RE.match(lines[idx + 1]))))

    def _ins(self, text: str, tag_names: list[str]) -> None:
        if not text:
            return
        tags = [self.tags[t] if t in self.tags else self.buf.get_tag_table().lookup(t) for t in tag_names]
        self.buf.insert_with_tags(self.buf.get_end_iter(), text, *[t for t in tags if t])

    def _nl(self) -> None:
        self.buf.insert(self.buf.get_end_iter(), "\n")

    def _strip_tail(self) -> None:
        """Срезать пустые строки в конце — иначе под ответом висит лишний отступ."""
        end = self.buf.get_end_iter()
        it = end.copy()
        while it.backward_char():
            if it.get_char() != "\n":
                it.forward_char()
                break
        if not it.equal(end):
            self.buf.delete(it, end)

    def _block_code(self, code: str, lang: str) -> None:
        self._ins((lang or "code") + "\n", ["codelang"])
        self._ins((code.rstrip("\n") or " ") + "\n", ["codeblock"])
        self._ins(" \n", ["codepad"])

    @staticmethod
    def _split_row(line: str) -> list[str]:
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    def _block_table(self, rows: list[list[str]]) -> None:
        ncol = max(len(r) for r in rows)
        rows = [r + [""] * (ncol - len(r)) for r in rows]
        plain = [[self.INLINE.sub(lambda m: m.group("code_t") or m.group("bold") or m.group("bold2")
                                  or m.group("strike") or m.group("ital") or m.group("ital2")
                                  or m.group("link_t") or m.group("url") or "", c) for c in r] for r in rows]
        widths = [min(max(len(plain[r][c]) for r in range(len(rows))), 40) for c in range(ncol)]
        for ri, row in enumerate(rows):
            base = ["table", "thead"] if ri == 0 else ["table"]
            for ci, cell in enumerate(row):
                pad = widths[ci] - len(plain[ri][ci])
                self._ins("   " if ci else " ", base)
                self._inline(cell, base)
                self._ins(" " * max(pad, 0), base)
            self._ins("\n", base)
        self._ins(" \n", ["codepad"])

    def _inline(self, text: str, base: list[str]) -> None:
        pos = 0
        for m in self.INLINE.finditer(text):
            if m.start() > pos:
                self._ins(text[pos:m.start()], base)
            g = m.groupdict()
            if g["code_t"] is not None:
                self._ins(g["code_t"], base + ["code"])
            elif g["bold"] is not None or g["bold2"] is not None:
                self._inline(g["bold"] or g["bold2"], base + ["bold"])
            elif g["strike"] is not None:
                self._inline(g["strike"], base + ["strike"])
            elif g["ital"] is not None or g["ital2"] is not None:
                self._inline(g["ital"] or g["ital2"], base + ["ital"])
            elif g["link_t"] is not None:
                self._link(g["link_t"], g["link_u"], base)
            elif g["url"] is not None:
                url = g["url"].rstrip(".,;:!?")
                self._link(url, url, base)
                self._ins(g["url"][len(url):], base)
            pos = m.end()
        if pos < len(text):
            self._ins(text[pos:], base)

    def _link(self, text: str, url: str, base: list[str]) -> None:
        self._link_n += 1
        name = f"link:{self._link_n}"
        self.buf.create_tag(name, foreground="#89b4fa", underline=Pango.Underline.SINGLE)
        self.links[name] = url
        if text == url:
            self._ins(text, base + [name])        # голый URL — без повторного разбора
        else:
            self._inline(text, base + [name])

    def url_at(self, view: Gtk.TextView, x: float, y: float) -> str | None:
        bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, it = view.get_iter_at_location(bx, by)
        if not ok:
            return None
        for tag in it.get_tags():
            name = tag.get_property("name")
            if name and name in self.links:
                return self.links[name]
        return None
