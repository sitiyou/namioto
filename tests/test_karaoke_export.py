"""External Hook recipe registration and export, without a display or audio device."""

from dataclasses import replace
from pathlib import Path

import pytest
from kara_templater import CueMarker

from namioto import export_cli, midi, project
from namioto.channels import Channel
from namioto.karaoke import ExportSettings
from namioto.karaoke.default_template import default_scene
from namioto.karaoke.song import prepare_song
from namioto.karaoke.song_input import load_project_input
from namioto.karaoke.templates import load_templates

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "cool_ink.py"


def test_project_input_uses_saved_note_seconds_without_audio_or_midi(tmp_path):
    path = tmp_path / "song.nto"
    project.save(project.Project(
        audio="missing-recording.wav",
        lyrics=project.Lyrics(text="京[きょう]\n---\n@1{あ}"),
        notes=(project.Note(4.0001, 0.6, 60, 0), project.Note(4.8, 1, 62, 0), project.Note(7, 1, 64, 1)),
    ), path)
    song = load_project_input(path, offset_ms=-100, harmony_tracks="1")
    first, harmony = song.sources
    assert first.lyrics.units[0].segments[0].timing.start == pytest.approx(3.9001)
    assert (first.chapter, harmony.chapter, harmony.style) == ("0", "1", "top")
    assert list(tmp_path.iterdir()) == [path]


def test_script_builds_a_hook_and_failure_preserves_last_export(tmp_path):
    krc, mid = tmp_path / "song.krc", tmp_path / "song.mid"
    krc.write_text("abc", encoding="utf-8")
    midi.write(mid, (Channel(channel=0),), (project.Note(1, 1, 60, 0),), 120)
    args = ["--no-wavetone", "--krc", str(krc), "--midi", str(mid), "--script", str(EXAMPLE), "--template", "cool-ink"]
    dest = tmp_path / "out.hook"
    assert export_cli.main([*args, "--out", str(dest), "--set", "ink=BA9876"]) == 0
    data = dest.read_bytes()
    assert b"//!HOOK" in data and b"//!TEXTURE" in data
    assert export_cli.main([*args, "--out", str(dest), "--set", "pulse=3"]) == 1
    assert dest.read_bytes() == data
    ass = tmp_path / "out.ass"
    assert export_cli.main([*args, "--out", str(ass)]) == 1
    assert not ass.exists()


def test_registration_errors_identify_the_script_and_can_be_recovered(tmp_path):
    script = tmp_path / "effect.py"
    script.write_text("def register(registry): registry.add(id='default', title='broken', build=print)")
    with pytest.raises(ValueError, match="effect.py.*default"):
        load_templates([script])
    script.write_text(EXAMPLE.read_text())
    recipe = load_templates([script]).get("cool-ink")
    with pytest.raises(ValueError, match="cool-ink.*unknown"):
        recipe.resolve({"pules": 1.1})
    assert recipe.resolve({"pulse": "1.2"})["pulse"] == 1.2


def test_single_double_and_harmony_layout_are_independent_of_singing_clocks():
    settings = ExportSettings(harmony_tracks="2", cue="off", fade_in_ms=0, fade_out_ms=0)
    text = "あ\nい\n@2{う}"
    spans = [[(0.0003, 1.0004)], [(2.35, 3.1)], [(2.1, 3.001)]]
    song = prepare_song(text, spans, settings)
    assert song.sources[-1].style == "top"
    assert song.sources[0].lyrics.units[0].segments[0].timing.start == 0.0003
    for mode, expected in (("single", [0, 0]), ("double", [0, 1])):
        scene = default_scene(replace(song, settings=replace(settings, layout=mode)))
        main = [plan for plan in scene.plans if plan.track_id == "default"]
        assert [plan.placement.row for plan in main] == expected
        assert scene.plans[-1].placement.position.y < main[0].placement.position.y


def test_default_pulses_each_reading_at_its_own_time_and_pivot():
    settings = ExportSettings(cue="off", pulse_peak=0.5)
    scene = default_scene(prepare_song("京[きょう]", [[(1, 2), (3, 4)]], settings))
    line = scene.lines[0]
    evaluator = scene.evaluator()
    (body,) = line.query("character")
    readings = line.query("ruby")
    assert evaluator.at(body.node_id, 2.5).scale.x == pytest.approx(settings.pulse)
    for time, active in ((1.25, 0), (1.75, 1), (2.5, None), (3.5, 2)):
        for i, target in enumerate(readings):
            state = evaluator.at(target.node_id, time)
            baseline = evaluator.at(target.node_id, 1)
            if i == active:
                assert state.scale.x == pytest.approx(baseline.scale.x * settings.pulse)
            else:
                assert state.scale == baseline.scale
                assert state.position == baseline.position


@pytest.mark.parametrize("guide", ["bars", "dots"])
@pytest.mark.parametrize("layout", ["single", "double"])
@pytest.mark.parametrize("show_ruby", [True, False])
@pytest.mark.parametrize("top", [True, False])
def test_guides_clear_the_allocated_lyric_row(guide, layout, show_ruby, top):
    settings = ExportSettings(layout=layout, cue=guide, show_ruby=show_ruby, ruby_ratio=0.75, harmony_ratio=0.75,
                              harmony_tracks="2" if top else "")
    song = prepare_song("@2{名前[なまえ]}", [[(5, 5.5), (5.5, 6), (6, 7)]], settings)
    if top:
        source, = song.sources
        body = replace(source.lyrics.body, cues=(CueMarker("lead", "lead", placement="independent"),))
        song = replace(song, sources=(replace(source, projection=replace(source.lyrics, body=body)),))
    (plan,) = default_scene(song).plans
    line = plan.line
    (cue,) = line.independent
    lyric_top = line.state.position.y + line.layout.bounds.top
    host = line.layout.body.characters[0].object.font_size
    assert host == pytest.approx(settings.font_size * (settings.harmony_ratio if top else 1))
    guide_size = int(host * settings.cue_size_ratio + 0.5)
    diameter = guide_size + (2 * settings.border if guide == "dots" else 0)
    clearance = host / 8
    if guide == "dots":
        lamps = [node for node in cue.children if not node.id.endswith("/sung")]
        centers = sorted(node.state.position.x for node in lamps)
        assert centers[1] - centers[0] - diameter == pytest.approx(guide_size * 0.3)
    if top:
        assert cue.state.position.y == pytest.approx(lyric_top + line.layout.bounds.height + clearance)
    else:
        assert cue.state.position.y + diameter == pytest.approx(lyric_top - clearance)
    assert settings.margin_v <= cue.state.position.y <= 1080 - settings.margin_v - diameter


def test_wide_krc_reading_is_centered_over_its_kanji():
    song = prepare_song("幸[しあわ]せ", [[(0, 1), (1, 2), (2, 3), (3, 4)]], ExportSettings())
    layout = default_scene(song).lines[0].layout
    host = layout.body.range_bounds(0, 1)
    readings = [ruby.layout.bounds for ruby in layout.ruby]
    assert readings[0].left < host.left
    assert readings[-1].right > host.right
    assert (readings[0].left + readings[-1].right) / 2 == pytest.approx(host.center)


def test_fast_handoff_keeps_successive_leads_on_opposite_sides():
    settings = ExportSettings(cue="off")
    spans = [[(1, 2.26)], [(2, 3.12)], [(3.12, 4.1)], [(4.14, 5)]]
    scene = default_scene(prepare_song("あ\nい\nう\nえ", spans, settings))
    places = [plan.placement for plan in scene.plans]
    assert [p.row for p in places] == [0, 1, 0, 1]
    for first, following in zip(places, places[2:], strict=False):
        assert first.display.end <= following.display.start
    for plan, [(start, end)] in zip(scene.plans, spans, strict=True):
        assert (plan.layout_request.timing.start, plan.layout_request.timing.end) == (start, end)
