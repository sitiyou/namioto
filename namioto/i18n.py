# SPDX-License-Identifier: AGPL-3.0-only
# ruff: noqa: E501
"""The language the interface speaks: the string catalogs and the one in force right now.

English is the source: every string the interface shows is written in it, and a catalog names the
text that replaces it. A missing entry falls back to the source, so a partly translated language
still runs. Qt-free on purpose, so the settings spec can offer the languages without widgets.
"""

from __future__ import annotations

import locale
import os

SYSTEM = "system"
DEFAULT = "en"

# (code, what to call the language on screen): a language names itself, so a picker reads the same
# in every language, and the id it picks is also the Qt locale the standard dialogs are translated by
LANGUAGES: tuple[tuple[str, str], ...] = (
    ("en", "English"),
    ("zh", "中文"),
)

LANGUAGE_CODES = (SYSTEM, *(code for code, _label in LANGUAGES))
LANGUAGE_LABELS = ("Follow system", *(label for _code, label in LANGUAGES))
LOCALES = {"en": "en_US", "zh": "zh_CN"}

CATALOGS: dict[str, dict[str, str]] = {
    "zh": {
        # settings window and its pages
        "Settings": "设置",
        "Restore defaults": "恢复默认",
        "Advanced": "高级",
        "General": "通用",
        "Analysis": "分析",
        "Tempo": "节拍",
        "System default ({name})": "系统默认（{name}）",
        "Analysis changes reach the spectrum the next time a file is loaded.": "分析设置会在下次载入文件时生效。",
        "Re-analyse now": "立即重新分析",
        "Run the analysis again with these settings": "用这些设置重新分析",
        "Configuration file": "配置文件",
        "Show folder": "打开所在目录",
        "Open the directory holding the settings file": "打开存放设置文件的目录",
        "Settings could not be saved: {error}": "设置无法保存：{error}",
        # settings fields
        "Language": "语言",
        "Which language the interface speaks; a change takes effect the next time it starts": "界面使用的语言；修改后下次启动生效",
        "Style": "样式",
        "Which widget style draws the window; the desktop's own unless another one is picked": "绘制窗口的控件样式；未选择时使用桌面自身的样式",
        "Follow system": "跟随系统",
        "Channels": "声道",
        "Which channels the analysis reads": "分析读取哪些声道",
        "Frames/s": "帧/秒",
        "Analysis frames per second: the time resolution of the spectrum": "每秒分析帧数：频谱的时间分辨率",
        "FFT points": "FFT 点数",
        "Window size of the analysis: the frequency resolution": "分析窗长：频率分辨率",
        "A4 (Hz)": "A4（Hz）",
        "Frequency of A4, followed by both the analysis bands and the played notes": "A4 的频率，分析频段与演奏的音符都遵循它",
        "Gain": "增益",
        "Energy it takes for the spectrum to reach full red": "频谱达到全红所需的能量",
        "Contrast": "对比度",
        "Exponent applied to the spectrum's energy": "施加到频谱能量上的指数",
        "Audio volume": "音频音量",
        "Starting volume of the analysed audio": "被分析音频的初始音量",
        "MIDI volume": "MIDI 音量",
        "Starting volume of the note playback": "音符播放的初始音量",
        "Latency (ms)": "延迟（ms）",
        "Offset between the sound and the displayed waveform": "声音与显示波形之间的偏移",
        "Playback speed in 5% steps, 0.10x to 2.00x; the pitch is left alone": "播放速度，5% 一档，0.10x 至 2.00x；音高保持不变",
        "Snap": "吸附",
        "Snap grid for the pen tool": "画笔工具的吸附网格",
        "Division": "刻度",
        "What the ruler's lower row and the drawn grid lines divide by": "标尺下行与所画网格线按什么划分",
        "Zoom x": "横向缩放",
        "Pixels per beat at startup": "启动时每拍的像素数",
        "Zoom y": "纵向缩放",
        "Pixels per semitone row at startup": "启动时每个半音行的像素数",
        "Auto page turn": "自动翻页",
        "Take the next page of the roll once the playhead reaches the right of the window": "播放头到达窗口右侧后翻到下一屏",
        "Overtone highlight": "泛音高亮",
        "Paint the overtones of the row under the mouse - f, 2f, 3f and 4f - as well": "同时高亮鼠标所在行的泛音——f、2f、3f 和 4f",
        "Which algorithm estimates the tempo from the audio": "用哪个算法从音频估计速度",
        "Algorithm": "算法",
        "Window (s)": "窗口（秒）",
        "Length of the windows the local tempo is fitted to": "局部速度拟合所用窗口的长度",
        "Hop (s)": "步进（秒）",
        "Distance between those windows": "窗口之间的距离",
        "Last directory": "上次目录",
        "Where the file chooser starts": "文件选择器的起始位置",
        "Window geometry": "窗口几何",
        "View center x": "视图中心 x",
        "View center y": "视图中心 y",
        "WaveTone compatibility": "WaveTone 兼容",
        "WaveTone's MIDI export starts every note one bar late: reading that back turns it back, and "
        "writing MIDI adds it, so files and the two programs agree": "WaveTone 导出的 MIDI 会让每个音符晚一小节：读入时抵消它，写出时加上它，从而与文件和两个程序保持一致",
        # field choices
        "mono": "单声道",
        "left": "左声道",
        "right": "右声道",
        "sum": "求和",
        "side": "侧声道",
        "both": "双声道",
        "beats": "拍",
        "seconds": "秒",
        "small": "小",
        "medium": "中",
        "large": "大",
        "Universal": "通用",
        "Japanese": "日语",
        "Cantonese": "粤语",
        "Mandarin": "普通话",
        "Off": "关闭",
        "New channel": "新通道",
        "Active channel": "当前通道",
        "Replace all notes": "替换全部音符",
        # transport and edit bars
        "Open a project (.nto) — Ctrl+O": "打开工程（.nto）— Ctrl+O",
        "Save the project — Ctrl+S, with Shift for Save As": "保存工程 — Ctrl+S，加 Shift 为另存为",
        "Export the notes as a MIDI file — every channel": "将音符导出为 MIDI 文件 — 包含全部通道",
        "Rewind to the beginning": "回到开头",
        "Stop": "停止",
        "Play from the beginning": "从头播放",
        "Play from the cursor": "从光标处播放",
        "Go to the end": "跳到结尾",
        "Playback position": "播放位置",
        "Speed": "速度",
        "Playback speed in 5% steps, 0.10x to 2.00x: the song is rerendered, so the pitch stays": "播放速度，5% 一档，0.10x 至 2.00x：歌曲会重新合成，音高保持不变",
        "Reset the playback speed to 1.00x": "将播放速度恢复为 1.00x",
        "Tempo of the beat grid in BPM, until a tempo map is analysed\nRight-click to double or halve it": "节拍网格的速度（BPM），直到分析出速度图\n右键可加倍或减半",
        "Estimate the tempo of the loaded audio": "估计已载入音频的速度",
        "Settings: the advanced options the bars have no control for": "设置：工具栏上没有控件的进阶选项",
        "Global offset between audio playback and the displayed waveform": "音频播放与显示波形之间的全局偏移",
        "Auto page turn: take the next page of the roll once the playhead reaches the right": "自动翻页：播放头到达右侧后翻到下一屏",
        "Overtone highlight: paint f, 2f, 3f and 4f of the row under the mouse, the way WaveTone marks them": "泛音高亮：像 WaveTone 那样，把鼠标所在行的 f、2f、3f 和 4f 一并标出",
        "Channels: colours, mute and instruments, one card per channel": "通道：颜色、静音与乐器，每个通道一张卡片",
        "Time division: checked follows the beats of the tempo map, unchecked follows seconds": "时间刻度：勾选跟随速度图的拍，取消则跟随秒",
        "Use this tempo": "采用此速度",
        "Dismiss this estimate": "忽略此估计",
        "≈{bpm} BPM": "≈{bpm} BPM",
        ", over {windows} windows: {agreement} of them agree.": "，共 {windows} 个窗口，其中 {agreement} 一致。",
        "\nBeat fit residual {ms} ms.": "\n节拍拟合残差 {ms} ms。",
        "{detail}\nNothing changes until you click the tick.": "{detail}\n点击对勾后才会生效。",
        "WaveTone volume-envelope DFT": "WaveTone 音量包络 DFT",
        "Librosa beat tracking and least-squares fit": "Librosa 节拍跟踪与最小二乘拟合",
        "TempoCNN model": "TempoCNN 模型",
        "Double tempo  (*)": "速度加倍  (*)",
        "Halve tempo  (/)": "速度减半  (/)",
        "Pause playback (Space)": "暂停播放（空格）",
        "Play from the cursor (Space)": "从光标处播放（空格）",
        "Edit mode: draw, move and select notes (off: a click in the roll moves the playhead)": "编辑模式：绘制、移动和选择音符（关闭时，点击卷帘移动播放头）",
        "Pen: click or drag an empty row to draw a note": "画笔：点击或拖动空白行绘制音符",
        "Select: drag a box, ctrl-click a note to add, drag a note to move": "选择：拖框选择，Ctrl 点击加选，拖动音符移动",
        "Snap grid for the pen tool: the note the grid is divided by": "画笔工具的吸附网格：网格划分的音符时值",
        "Quantize: put the starts and ends of the notes on the snap grid, the selection if there is one": "量化：把音符的起点与终点对齐到吸附网格，有选区时只处理选区",
        "Transcribe the singing voice of the loaded audio with GAME": "用 GAME 转录已载入音频中的歌声",
        "Spectrum gain: how much energy it takes to reach full red": "频谱增益：达到全红所需的能量",
        "Spectrum contrast: exponent applied to the energy": "频谱对比度：施加到能量上的指数",
        "Volume of the analysed audio track": "被分析音轨的音量",
        "Volume of the note playback": "音符播放的音量",
        "Volume of the note playback through {player}": "通过 {player} 播放音符的音量",
        "Audio": "音频",
        # channel sidebar
        "Channel {number}": "通道 {number}",
        "Lock: notes on this channel cannot be edited": "锁定：此通道的音符不可编辑",
        "Show or hide the notes of this channel": "显示或隐藏此通道的音符",
        "Mute this channel during playback": "播放时静音此通道",
        "Rename…": "重命名…",
        "Volume…": "音量…",
        "Delete channel": "删除通道",
        "Rename channel": "重命名通道",
        "Name:": "名称：",
        "Channel volume": "通道音量",
        "Volume (0-127):": "音量（0-127）：",
        "Add channel": "添加通道",
        # MIDI import dialog
        "Import MIDI": "导入 MIDI",
        "{notes} notes in {channels} channels from {source}.": "来自 {source} 的 {notes} 个音符，{channels} 个通道。",
        "The notes already on the roll are replaced, or merged into the channels below.": "卷帘上已有的音符会被替换，或合并到下面的通道中。",
        "File channel": "文件通道",
        "Lands on": "落到",
        "Merge": "合并",
        "Replace": "替换",
        "{name} (channel {number})": "{name}（通道 {number}）",
        "Point the file at existing channels to stay within {count}": "把文件指向已有通道，以保持在 {count} 个以内",
        # transcription window
        "Transcribe the singing voice with GAME": "用 GAME 转录歌声",
        "Ready": "就绪",
        "Transcribe": "转录",
        "Transcribing …": "正在转录…",
        "Insert": "插入",
        "Close": "关闭",
        "Transcribe with GAME": "用 GAME 转录",
        "A run with these exact parameters already found {count} notes.\n"
        "Use it instead of running the model again?": "使用完全相同参数的运行已经找到了 {count} 个音符。\n要用它代替重新运行模型吗？",
        "saved run reused: {count} notes": "已复用保存的结果：{count} 个音符",
        "Transcribing {name} …": "正在转录 {name}…",
        "Starting GAME …": "正在启动 GAME…",
        "Downloading the model … {done}{where} MB": "正在下载模型… {done}{where} MB",
        " of {total}": " / {total}",
        "Extracting … {done}/{total}": "正在提取… {done}/{total}",
        "{count} notes": "{count} 个音符",
        "Failed": "失败",
        "GAME stopped unexpectedly (exit code {code})": "GAME 意外停止（退出码 {code}）",
        "the result could not be saved: {error}": "结果无法保存：{error}",
        "Model": "模型",
        "Backend": "后端",
        "Quantize": "量化",
        "Target": "目标",
        "Batch": "批大小",
        "Segment threshold": "分段阈值",
        "Segment radius": "分段半径",
        "Estimate threshold": "估计阈值",
        "D3PM T0": "D3PM T0",
        "D3PM steps": "D3PM 步数",
        "Silence slicing": "静音切分",
        "GAME's ONNX model, downloaded into the data directory on first use": "GAME 的 ONNX 模型，首次使用时下载到数据目录",
        "Where the models run; CUDA falls back to the CPU when it cannot be set up": "模型运行的位置；CUDA 无法配置时回退到 CPU",
        "Singing language among the ones the model knows; Universal leaves it to the model": "模型支持的演唱语言；通用则交给模型判断",
        "Snap the notes to the beat grid, this many cells per quarter note": "将音符吸附到节拍网格，每四分音符划分这么多格",
        "Where the notes land: a channel of their own, the active channel, or a fresh start": "音符的去向：独立通道、当前通道，或重新开始",
        "Chunks per inference batch": "每次推理的块数",
        "Boundary decoding threshold": "边界解码阈值",
        "Boundary decoding radius": "边界解码半径",
        "Note presence threshold": "音符存在阈值",
        "Starting T of D3PM sampling": "D3PM 采样的起始 T",
        "Number of D3PM sampling steps": "D3PM 采样步数",
        "Cut the audio at its silences before inference": "推理前在静音处切分音频",
        # main window
        "Namioto project (*{suffix})": "Namioto 工程 (*{suffix})",
        "Audio file ({patterns})": "音频文件（{patterns}）",
        "MIDI file ({patterns})": "MIDI 文件（{patterns}）",
        "All files (*)": "所有文件 (*)",
        "Untitled": "未命名",
        "Save the changes to {name}?": "保存对 {name} 的更改？",
        "Open": "打开",
        "Save project": "保存工程",
        "Export MIDI": "导出 MIDI",
        "Project could not be opened: {error}": "工程无法打开：{error}",
        "Opened {name} — {notes} notes": "已打开 {name} — {notes} 个音符",
        " (audio not found: {path})": "（未找到音频：{path}）",
        "Project could not be saved: {error}": "工程无法保存：{error}",
        "Saved {name} — {notes} notes": "已保存 {name} — {notes} 个音符",
        "MIDI file could not be read: {error}": "MIDI 文件无法读取：{error}",
        "Merged": "已合并",
        "Imported": "已导入",
        "{verb} {notes} notes from {origin}": "{verb} {notes} 个音符，来自 {origin}",
        "{notes} tempo changes; the grid takes the first tempo": "{notes} 处速度变化；网格采用第一个速度",
        "{notes} note events left out": "忽略了 {notes} 个音符事件",
        "channels {numbers} left out": "忽略了通道 {numbers}",
        "MIDI file could not be written: {error}": "MIDI 文件无法写入：{error}",
        "Exported {name} — {notes} notes": "已导出 {name} — {notes} 个音符",
        "GAME found no notes in the loaded audio": "GAME 在已载入的音频中没有找到音符",
        "All 16 channels are in use; the notes went to the active channel": "16 个通道都在使用中；音符放到了当前通道",
        "GAME found {notes} notes on channel {channel}": "GAME 在通道 {channel} 上找到 {notes} 个音符",
        "Spectrum failed: {error}": "频谱失败：{error}",
        "Analysing {path} …": "正在分析 {path}…",
        "Playback failed: {error}": "播放失败：{error}",
        "Drawing into {channel}": "正在绘制到 {channel}",
        "Tempo estimation failed: {error}": "速度估计失败：{error}",
        "Tempo set to {bpm} BPM from the audio": "已根据音频将速度设为 {bpm} BPM",
        "Nothing to play: load a file or draw some notes": "没有可播放的内容：载入文件或绘制一些音符",
        "{player} did not accept the notes": "{player} 没有接受这些音符",
        "Analysing … {percent}%": "正在分析… {percent}%",
        "{frames} frames x {bands} bands, {ms} ms/frame, {seconds} s": "{frames} 帧 × {bands} 个频段，{ms} ms/帧，{seconds} 秒",
        "{document} — Namioto — {notes} notes": "{document} — Namioto — {notes} 个音符",
        "space: play or pause  |  click (outside edit mode): move the playhead  |  "
        "pen: drag an empty row to draw  |  select: drag a box, ctrl-click to add  |  "
        "shift drag a note: trim its start (left half) or end (right half)  |  right click: delete  |  "
        "ctrl C: copy the selection, ctrl V: paste it at the playhead  |  "
        "ctrl Z: undo, ctrl shift Z: redo  |  "
        "middle drag: pan  |  ctrl wheel: zoom x, ctrl shift wheel: zoom y  |  gear: settings": "空格：播放或暂停  |  点击（编辑模式之外）：移动播放头  |  "
        "画笔：在空白行拖动绘制  |  选择：拖框选择，Ctrl 点击加选  |  "
        "Shift 拖动音符：修剪起点（左半）或终点（右半）  |  右键：删除  |  "
        "Ctrl+C：复制选区，Ctrl+V：在播放头处粘贴  |  "
        "Ctrl+Z：撤销，Ctrl+Shift+Z：重做  |  "
        "中键拖动：平移  |  Ctrl 滚轮：横向缩放，Ctrl+Shift 滚轮：纵向缩放  |  齿轮：设置",
    },
}


_current = DEFAULT


def _match(name: str) -> str:
    """The language a locale name belongs to: its primary subtag, or English when unsupported."""
    primary = name.replace("-", "_").split("_", 1)[0].split(".", 1)[0].lower()
    return primary if any(primary == code for code, _label in LANGUAGES) else DEFAULT


def system_language() -> str:
    """The language the session asks for, from the usual locale variables and then the C library."""
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name)
        if value:
            return _match(value)
    try:
        value = locale.getlocale()[0]
    except (TypeError, ValueError):
        value = None
    return _match(value or "")


def resolve(code: str) -> str:
    """A stored choice as a concrete language: the system's own when it does not name one."""
    return system_language() if not code or code == SYSTEM else _match(code)


def set_language(code: str) -> None:
    global _current
    _current = resolve(code)


def current() -> str:
    return _current


def tr(text: str, **fields) -> str:
    """The text in the language in force, source English when it has no translation there."""
    translated = CATALOGS.get(_current, {}).get(text, text)
    return translated.format(**fields) if fields else translated
