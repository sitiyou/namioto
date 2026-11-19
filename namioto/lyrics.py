# SPDX-License-Identifier: AGPL-3.0-only
# ruff: noqa: E501
"""KRC text I/O, the prompt that fills it, and the model call.

Qt-free on purpose. The project keeps the text itself as its baseline; external editing uses a
temporary working copy, while imports and exports may live anywhere. `load` and `save` do not check the syntax
- a file with a mistake in it is still the user's to fix in an editor - while `conversion_error` and
`syntax_error` are what tell a model's answer, or a box about to be written, from a readable one.
`convert` takes its opener as an argument, so a call can be exercised without a network.

Align starts a mapping session; saving its final map writes NOTE counts and groups into the KRC
and returns to faithful reading. Text edits never start alignment automatically. The endpoint,
the key and the model are the settings'; the built-in `DEFAULT_PROMPT` is the one here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from namioto import net
from namioto.i18n import tr
from namioto.karaoke import KrcError, parse
from namioto.utils import write_text

SUFFIX = ".krc"
DEFAULT_PROMPT = """请为输入的日语歌词添加汉字和汉字重复符号「々」的读音，严格遵守以下规则：
只添加注音，不改写、增删或替换原文中的任何字符；保留原有换行和空白。只输出标注后的歌词，不要解释或 Markdown 代码块。
1. 对于输入的歌词中的汉字，在其后用方括号进行注音。
示例：
输入：覚えてる　君がくれた
输出：覚[おぼ]えてる　君[きみ]がくれた
2. 根据上下文判断词语边界，再为同一个词内连续的汉字整体注音。汉字相邻不代表属于同一个词，不得跨词合并注音。使用逗号将读音与汉字逐字对应；只有熟字训等确实无法逐字对应的读音才不分隔。
示例：
输入1：季節は移ろい
正确输出：季節[き,せつ]は移[うつ]ろい
错误输出1：季節[きせつ]は移[うつ]ろい  # 没有使用逗号分隔注音
错误输出2：季[き]節[せつ]は移[うつ]ろい  # 季節没有作为一个整体进行标注

输入2：明日をえがく
正确输出：明日[あした]をえがく
错误输出：明日[あ,した]をえがく  # 明日（あした）是熟字训，读音不可逐字分配

输入3：背中合わせだとしても
正确输出：背中[せ,なか]合[あ]わせだとしても
错误输出：背中合[せ,なか,あ]わせだとしても  # 背中与合わせ是两个词，不可因汉字相邻而合并
3. 对于汉字与假名混合的词语，只标注其中连续的汉字部分，保留假名在方括号外，不得为了词语完整性将假名包含在被注音的原文中。
示例：
输入：映し出す　気持ちは
输出：映[うつ]し出[だ]す　気持[き,も]ちは
错误输出：映し出す[うつしだす]　気持ち[きもち]は
4. 「々」表示重复前一个汉字。保留原来的「々」，与前面的汉字一起注音；逐字写出实际读音，包括连浊。
示例：
输入：日々を重ねる
正确输出：日々[ひ,び]を重[かさ]ねる
错误输出：日[ひ]々を重[かさ]ねる  # 「々」也需要读音
5. 除「々」外，平假名、片假名及其他非汉字字符不添加注音。读音使用平假名，并根据歌词上下文选择实际读法。

现在请对以下歌词进行注音标注："""

_FENCE = re.compile(r"```[^\n]*\n(?P<body>.*?)\n?```", re.DOTALL)
_DOTS = re.compile(r"\.+")
_PUNCTUATION = str.maketrans("#[]()-,{}@", "＃［］（）－，｛｝＠")
_MAX_DIAGNOSTIC_LOCATIONS = 3


def text_key(text: str) -> str:
    """A short key for a `.krc` text, so the times stored beside it can tell when it changed."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def path_for(project: str | Path) -> Path:
    """A suggested KRC export path beside a project file."""
    return Path(project).with_suffix(SUFFIX)


def load(path: str | Path) -> str:
    """The `.krc` text; a missing or unreadable file is an empty one."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def save(path: str | Path, text: str) -> Path:
    """Write the whole file at once, so a half-written `.krc` never exists to be read."""
    return write_text(path, text)


def normalize_plain_text(text: str) -> str:
    """Make literal lyric punctuation safe for KRC before adding annotations."""
    return _DOTS.sub(lambda match: _normalized_dots(match[0]), text).translate(_PUNCTUATION)


def _normalized_dots(dots: str) -> str:
    return "．" if len(dots) == 1 else "…" if len(dots) < 5 else "……"


def build_prompt(lyrics: str, prompt: str = DEFAULT_PROMPT, retry: str = "") -> str:
    """The prompt that goes to a model, with the text to annotate under it.

    `retry` is what an earlier refusal said, appended so a second try knows what to fix.
    """
    text = f"{prompt}\n{normalize_plain_text(lyrics)}"
    return f"{text}\n\n{retry}" if retry else text


def retry_prompt(answer: str, error: str) -> str:
    """What a rejected answer earns: the model's own output again, and why it was refused."""
    return (
        f"你上一次的输出：\n{answer}\n\n"
        f"它没有通过检查：{error}\n"
        "请严格按上面的规则重新注音，只输出修正后的歌词本身，不要任何解释。"
    )


def conversion_error(text: str, source: str) -> str:
    """Why `text` is not `source` with the readings added, or "" when it is.

    The model is asked for the readings alone, never to rewrite, so once the readings and the `.krc`
    markers are taken away the two must carry the same characters; a model that dropped a line,
    changed a word or answered with prose is caught here. Every kanji must also carry a reading: an
    unread one has no sound to align. The kanji repetition mark is treated the same way.
    Whether the readings themselves are right is the user's to judge.
    """
    expected = _squeezed(normalize_plain_text(source))
    if expected and not text.strip():
        return _changed_lyrics(source, text, expected, "", [])
    try:
        converted = parse(text)
    except KrcError as error:
        return str(error)
    units = [unit for chapter in converted.chapters for line in chapter.lines for unit in line.units]
    written = "".join(unit.text for unit in units)
    actual = _squeezed(written)
    assert converted.source is not None
    offsets = converted.source.lyric_offsets
    if expected != actual:
        krc_offsets = [offset for char, offset in zip(written, offsets, strict=True) if not char.isspace()]
        return _changed_lyrics(source, text, expected, actual, krc_offsets)
    unread = []
    start = 0
    for unit in units:
        if unit.ruby is None and unit.is_kanji():
            unread.append((unit.text, offsets[start]))
        start += len(unit.text)
    if unread:
        details = [
            tr("KRC {position} ({excerpt})", position=_position(text, offset), excerpt=_excerpt(chars))
            for chars, offset in unread[:_MAX_DIAGNOSTIC_LOCATIONS]
        ]
        return _diagnostic(tr("These characters have no reading."), details, len(unread))
    return ""


def _changed_lyrics(source: str, krc: str, expected: str, actual: str, krc_offsets: list[int]) -> str:
    # Keep offsets into the editors' text even when comparison ignores whitespace or KRC syntax.
    source_offsets = [
        offset
        for match in re.finditer(r"\.+|[^\s.]+", source)
        for offset in range(match.start(), match.start() + len(normalize_plain_text(match[0])))
    ]
    changes = [
        (kind, start, end, found, found_end)
        for kind, start, end, found, found_end in SequenceMatcher(None, expected, actual, autojunk=False).get_opcodes()
        if kind != "equal"
    ]
    details = []
    for kind, start, end, found, found_end in changes[:_MAX_DIAGNOSTIC_LOCATIONS]:
        if kind == "delete":
            description = tr("Missing text")
        elif kind == "insert":
            description = tr("Extra text")
        else:
            description = tr("Replaced text")
        details.append(
            tr(
                "{change}: source {source_position}, expected {expected}; KRC {krc_position}, got {actual}.",
                change=description,
                source_position=_position(source, source_offsets[start] if start < len(source_offsets) else None),
                expected=_excerpt(expected[start:end]),
                krc_position=_position(krc, krc_offsets[found] if found < len(krc_offsets) else None),
                actual=_excerpt(actual[found:found_end]),
            )
        )
    return _diagnostic(tr("The answer changed the lyrics instead of annotating them."), details, len(changes))


def _position(text: str, offset: int | None) -> str:
    position = len(text) if offset is None else offset
    line, column = text.count("\n", 0, position) + 1, position - text.rfind("\n", 0, position)
    if offset is None:
        return tr("end of text (line {line}, column {column})", line=line, column=column)
    return tr("line {line}, column {column}", line=line, column=column)


def _excerpt(text: str) -> str:
    return repr(text[:24] + "…" if len(text) > 24 else text) if text else tr("(none)")


def _diagnostic(summary: str, details: list[str], count: int) -> str:
    if count > len(details):
        details.append(tr("More locations: {count}.", count=count - len(details)))
    return "\n".join([summary, *details])


def syntax_error(text: str) -> str:
    """Why `text` is not a readable `.krc`, or "" when it is; empty text is readable."""
    if not text.strip():
        return ""
    try:
        parse(text)
    except KrcError as error:
        return str(error)
    return ""


def _squeezed(text: str) -> str:
    return "".join(char for char in text if not char.isspace())


def convert(
    lyrics: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str = DEFAULT_PROMPT,
    retry: str = "",
    temperature: float = 0.2,
    timeout: float = 120.0,
    stream: bool = False,
    on_delta: Callable[[str, str], None] | None = None,
    opener: Callable[..., Any] | None = None,
) -> str:
    """Ask an OpenAI-compatible endpoint to annotate `lyrics`, and return what it wrote.

    `base_url` is the endpoint up to its `/v1`. The key rides in the header and nowhere else, and
    nothing raised here repeats it. `retry` is what an earlier refusal said, sent with the lyrics so
    the model can correct itself. With `stream`, every delta is handed to `on_delta(kind, text)`
    as it arrives - `kind` being `"reasoning"` or `"content"` - and the answer is still returned.
    Without an `opener`, the request goes through the proxy named in the settings.
    """
    lyrics = normalize_plain_text(lyrics)
    body: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"{lyrics}\n\n{retry}" if retry else lyrics},
        ],
    }
    if stream:
        body["stream"] = True
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    open_url = opener or net.opener()
    try:
        with open_url(request, timeout=timeout) as response:
            if stream:
                return _read_stream(response, on_delta)
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ValueError(f"the service answered HTTP {error.code} {error.reason}") from None
    except urllib.error.URLError as error:
        raise ValueError(f"the service could not be reached: {error.reason}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"the service answered something that is not JSON: {error}") from None
    return _content(payload)


def _read_stream(response: Any, on_delta: Callable[[str, str], None] | None) -> str:
    """Read an SSE completion as it arrives, handing every delta on before the answer is done."""
    raw: list[str] = []
    pieces: list[str] = []
    found = False
    for line in response:
        text = line.decode("utf-8")
        raw.append(text)
        entry = text.strip()
        if not entry.startswith("data:"):
            continue
        data = entry[5:].strip()
        if data == "[DONE]":
            break
        try:
            delta = json.loads(data)["choices"][0]["delta"]
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        for key, kind in (("reasoning_content", "reasoning"), ("reasoning", "reasoning"), ("content", "content")):
            chunk = delta.get(key) if isinstance(delta, dict) else None
            if not isinstance(chunk, str) or not chunk:
                continue
            found = True
            if kind == "content":
                pieces.append(chunk)
            if on_delta is not None:
                on_delta(kind, chunk)
    if not found:  # an endpoint that ignored the stream and answered whole
        return _content(json.loads("".join(raw)))
    return _unfence("".join(pieces))


def _content(payload: Any) -> str:
    """The message out of a chat completion, whether or not it came wrapped in a code fence."""
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("the service answered without a message") from None
    if not isinstance(content, str):
        raise ValueError("the service answered without a message")
    return _unfence(content)


def _unfence(text: str) -> str:
    """A model that wrapped its answer in a code fence still said what it was asked."""
    text = text.strip()
    match = _FENCE.search(text)
    return match.group("body") if match else text


def editor_command(
    configured: str = "",
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """The program that opens a `.krc`: the setting, else the platform's own choice of editor."""
    if configured.strip():
        return shlex.split(configured)
    system = sys.platform if platform is None else platform
    values = os.environ if environ is None else environ
    if system.startswith("win"):
        for name in ("code", "notepad++", "subl"):
            found = which(name)
            if found:
                return [found]
        return ["notepad"]
    if system == "darwin":
        return ["open"]
    editor = values.get("EDITOR", "").strip()
    return shlex.split(editor) if editor else ["xdg-open"]
