# Python karaoke templates

Select **Export karaoke… → Load Python template** and open a trusted Python file. The editor remembers the file and the selected template's controls after a successful export. The editor keeps these choices in its local configuration. A song can be shared as KRC and MIDI without including executable Python.

For a named project, put a trusted script at `template.py` beside the `.nto`, or place several scripts under `template/*.py`. The export dialog lists these files automatically. Selecting a file runs its registration and exposes its recipes and parameters. A successful export remembers a project recipe for that project only.

A template file defines `register(registry)`. Call `registry.add` with a unique string ID, a display title, optional parameter declarations, and a Python builder. The default supported formats are `("ass", "hook")`. A template using native ASS events should declare `formats=("ass",)`. Each parameter is a `namioto.params.Field` with a name, kind, default and caption. Supported kinds are `bool`, `int`, `float`, `choice`, `text`, and `color`. Set numeric bounds with `low` and `high`, or list enum values in `choices`. Colour parameters reach the builder as six RGB hex digits. The optional `validate(values)` callback raises `ValueError` for incompatible combinations.

The builder receives `(song, values)` and returns a kara-templater `Scene`. The host writes the requested format. `song.sources` contains one structured lyric source per display line, including independently timed readings, colour runs, section identity in `actor`, the vocal role in `style`, and countdown markers. Each singing interval is in absolute seconds. `song.bpm` comes from the input MIDI. Use `default_scene(song)` to reuse the default layout and motions, or create a `Document` and pass the sources and your own tracks to `prepare_tracks`. Register a matching cue preset on tracks displaying marked sources.

[Runnable template with two editable parameters](../examples/cool_ink.py)

```sh
namioto-cli export --script examples/cool_ink.py --list-templates
namioto-cli export --script examples/cool_ink.py --template cool-ink --template-help
namioto-cli export --script examples/cool_ink.py --template cool-ink \
    --set ink=CDEEFF --set pulse=1.08 --krc song.krc --midi song.mid --out song.hook
```

File exports load the scripts named by `--script`. In session mode the CLI reuses the editor’s selected recipe, its parameters and scripts remembered locally for that project. Use `--script` to supply your own copy or `--template default` to select the built-in recipe. Watch mode reloads the selected scripts on edits. A failed build reports the error and preserves the previous file.
