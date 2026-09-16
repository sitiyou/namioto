# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the lyrics sidecar: its file, its prompt, the model call and the editor command."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from namioto import lyrics

KEY = "sk-secret-value"
BASE = "https://api.example.com/v1"


class JsonResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> JsonResponse:
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class HtmlResponse:
    def read(self) -> bytes:
        return b"<html>oops</html>"

    def __enter__(self) -> HtmlResponse:
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class StreamResponse:
    """An SSE body: iterating it hands the lines over one at a time, as urllib's response does."""

    def __init__(self, lines: list[str]):
        self.lines = [line.encode("utf-8") for line in lines]

    def __iter__(self):
        return iter(self.lines)

    def read(self) -> bytes:
        return b"".join(self.lines)

    def __enter__(self) -> StreamResponse:
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def sse(*deltas: dict) -> list[str]:
    """The `data:` lines of a streamed completion, closed the way a server closes it."""
    return [f"data: {json.dumps({'choices': [{'delta': delta}]})}\n" for delta in deltas] + ["data: [DONE]\n"]


def answer(text: str, *, fenced: bool = False) -> dict:
    content = f"```krc\n{text}\n```" if fenced else text
    return {"choices": [{"message": {"content": content}}]}


def fake_opener(payload=None, error=None):
    """An opener that records its calls and answers, or raises, instead of reaching the network."""
    calls: list[dict] = []

    def open(request, timeout=None):
        calls.append({"request": request, "timeout": timeout})
        if error is not None:
            raise error
        return JsonResponse(payload)

    open.calls = calls
    return open


def translate(payload=None, error=None, **fields):
    opener = fake_opener(payload, error)
    parameters = {"base_url": BASE, "api_key": KEY, "model": "a-model"}
    parameters.update(fields)
    text = lyrics.translate("君の名は", opener=opener, **parameters)
    return text, opener.calls[0]


def test_a_project_keeps_its_lyrics_beside_it() -> None:
    assert lyrics.path_for("/tmp/song.nto") == Path("/tmp/song.krc")
    assert lyrics.path_for("C:/work/うた.nto").name == "うた.krc"


def test_a_round_trip_keeps_the_text(tmp_path) -> None:
    path = tmp_path / "song.krc"
    lyrics.save(path, "季節[き,せつ]は移[うつ]ろい")
    assert lyrics.load(path) == "季節[き,せつ]は移[うつ]ろい"


def test_a_missing_file_reads_as_empty(tmp_path) -> None:
    assert lyrics.load(tmp_path / "nothing.krc") == ""
    assert lyrics.load(tmp_path) == ""  # a directory is not a file


def test_saving_leaves_nothing_half_written(tmp_path) -> None:
    path = tmp_path / "song.krc"
    lyrics.save(path, "first")
    lyrics.save(path, "second")

    assert [entry.name for entry in tmp_path.iterdir()] == ["song.krc"]
    assert lyrics.load(path) == "second"


def test_a_save_creates_the_folder_it_needs(tmp_path) -> None:
    path = tmp_path / "project" / "song.krc"
    lyrics.save(path, "歌[うた]")
    assert lyrics.load(path) == "歌[うた]"


def test_the_prompt_carries_the_lyrics_under_it() -> None:
    assert lyrics.build_prompt("君の名は") == f"{lyrics.DEFAULT_PROMPT}\n君の名は"
    assert lyrics.build_prompt("君の名は", "注音：") == "注音：\n君の名は"


def test_the_call_posts_the_prompt_and_the_lyrics() -> None:
    text, call = translate(answer("覚[おぼ]えてる"))
    request = call["request"]

    assert text == "覚[おぼ]えてる"
    assert request.get_full_url() == f"{BASE}/chat/completions"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {KEY}"
    assert request.get_header("Content-type") == "application/json"
    body = json.loads(request.data)
    assert body["model"] == "a-model"
    assert body["temperature"] == 0.2
    assert "stream" not in body
    assert body["messages"] == [
        {"role": "system", "content": lyrics.DEFAULT_PROMPT},
        {"role": "user", "content": "君の名は"},
    ]


def test_a_trailing_slash_on_the_base_does_not_double() -> None:
    _text, call = translate(answer("x"), base_url=f"{BASE}/")
    assert call["request"].get_full_url() == f"{BASE}/chat/completions"


def test_the_timeout_and_the_prompt_are_the_callers() -> None:
    _text, call = translate(answer("x"), prompt="注音：", temperature=0.7, timeout=5.0)
    assert call["timeout"] == 5.0
    body = json.loads(call["request"].data)
    assert body["temperature"] == 0.7
    assert body["messages"][0]["content"] == "注音："


def test_a_fenced_answer_is_unwrapped() -> None:
    text, _call = translate(answer("覚[おぼ]えてる", fenced=True))
    assert text == "覚[おぼ]えてる"


def test_a_fenced_answer_with_a_preamble_is_unwrapped_too() -> None:
    answer_with_prose = {"choices": [{"message": {"content": "Here it is:\n```\n歌[うた]\n```"}}]}
    text, _call = translate(answer_with_prose)
    assert text == "歌[うた]"


def test_streaming_asks_for_a_stream_and_reports_every_delta() -> None:
    lines = sse(
        {"reasoning_content": "考え"},
        {"reasoning_content": "て"},
        {"content": "歌[うた]"},
        {"content": "を"},
    )
    seen: list[tuple[str, str]] = []
    calls: list = []

    def open(request, timeout=None):
        calls.append(request)
        return StreamResponse(lines)

    text = lyrics.translate(
        "x",
        base_url=BASE,
        api_key=KEY,
        model="m",
        stream=True,
        on_delta=lambda kind, chunk: seen.append((kind, chunk)),
        opener=open,
    )
    assert json.loads(calls[0].data)["stream"] is True
    assert text == "歌[うた]を"
    assert seen == [("reasoning", "考え"), ("reasoning", "て"), ("content", "歌[うた]"), ("content", "を")]


def test_a_fenced_streamed_answer_is_unwrapped() -> None:
    lines = sse({"content": "```\n歌[うた]\n```"})
    text = lyrics.translate(
        "x", base_url=BASE, api_key=KEY, model="m", stream=True, opener=lambda *a, **k: StreamResponse(lines)
    )
    assert text == "歌[うた]"


def test_an_endpoint_that_ignores_the_stream_is_still_read() -> None:
    whole = json.dumps(answer("歌[うた]")) + "\n"
    text = lyrics.translate(
        "x", base_url=BASE, api_key=KEY, model="m", stream=True, opener=lambda *a, **k: StreamResponse([whole])
    )
    assert text == "歌[うた]"


def test_a_stream_of_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="not JSON"):
        lyrics.translate(
            "x", base_url=BASE, api_key=KEY, model="m", stream=True, opener=lambda *a, **k: StreamResponse([])
        )


def test_an_http_error_names_the_status_and_never_the_key() -> None:
    error = urllib.error.HTTPError(f"{BASE}/chat/completions", 401, "Unauthorized", None, None)
    try:
        with pytest.raises(ValueError) as caught:
            translate(error=error)
    finally:
        error.close()  # an HTTPError is a file object, and an unclosed one warns when it is collected
    message = str(caught.value)
    assert "401" in message
    assert KEY not in message


def test_an_unreachable_service_says_so() -> None:
    with pytest.raises(ValueError, match="could not be reached"):
        translate(error=urllib.error.URLError("connection refused"))


def test_an_answer_that_is_not_json_is_refused() -> None:
    with pytest.raises(ValueError, match="not JSON"):
        lyrics.translate("x", base_url=BASE, api_key=KEY, model="m", opener=lambda *a, **k: HtmlResponse())


def test_an_answer_without_a_message_is_refused() -> None:
    with pytest.raises(ValueError, match="without a message"):
        translate({"choices": []})
    with pytest.raises(ValueError, match="without a message"):
        translate({"choices": [{"message": {"content": 3}}]})


def test_the_set_editor_command_wins() -> None:
    assert lyrics.editor_command("code --wait") == ["code", "--wait"]


def test_linux_prefers_the_sessions_editor_then_xdg_open() -> None:
    assert lyrics.editor_command("", platform="linux", environ={"EDITOR": "nvim -f"}) == ["nvim", "-f"]
    assert lyrics.editor_command("", platform="linux", environ={}) == ["xdg-open"]


def test_windows_tries_known_editors_then_notepad() -> None:
    def which(name):
        return "C:/tools/code.cmd" if name == "code" else None

    assert lyrics.editor_command("", platform="win32", which=which) == ["C:/tools/code.cmd"]
    assert lyrics.editor_command("", platform="win32", which=lambda _name: None) == ["notepad"]


def test_macos_hands_the_file_to_open() -> None:
    assert lyrics.editor_command("", platform="darwin", environ={}) == ["open"]
