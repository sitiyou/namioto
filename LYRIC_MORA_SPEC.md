# 歌词 Sound ↔ NOTE 规范（SPEC）

> 这份文档是**行为契约**：改 `namioto/karaoke/timeline.py`、`namioto/ui/{roll,strips,app}.py` 里与
> 歌词轴相关的代码前，先对照本文；不一致要么改代码、要么先改本文并说清原因。
> 目的：防止「歌词轴 / NOTE 映射 / `.0` 判定 / 拖动」再次 regression。

---

## 0. 范围

覆盖：`.krc` 歌词 → **flatten（把一个 `Unit` 展成 `unit.mora` 个 Sound）** → 原始对齐时间 →
映射到 channel 1（内部 `channel == 0`）的 NOTE → 条带显示 → 拖动编辑 → 存进 `.nto`。也覆盖
**`.krc` 的 group/mora 语法在该模型里的读取**（`karaoke/model.py`/`parser.py`/`transforms.py`/
`timeline.py::_row`，见 §13）。
不覆盖：对齐模型本身（`analysis/align.py`）、MIDI 导出。

---

## 1. 术语

| 词 | 含义 | 出处 |
| --- | --- | --- |
| `.krc` | 歌词 sidecar，与工程同名。**编辑器从不写它**（只有导入对话框会写）。 | |
| Word | 一个字符，`Group` 的原子。 | `model.Word` |
| Group | 一串 `Word`：`.krc` 的 `(...)`，或归一化折叠出的 base。 | `model.Group` |
| Unit | `.krc` 的文本单元：base(`Word`/`Group`) + 可选读音(`Ruby`) + 可选 `.N`(`override`)。 | `model.Unit` |
| Ruby | `Unit` 的读音：逗号分隔的 parts，每 part 是一串 `Unit`。 | `model.Ruby` |
| **mora** | **数量属性（整数）**：一个 `Unit`/`Ruby`/`Line` 读作几个可唱音。**不是实体。** | `Unit.mora` / `total_mora` |
| `.N` | `word.N`：**override 这个 `Unit` 的 mora 数量**（`Unit.override`），**不是**「覆盖的 NOTE 数」。 | `model.Unit.override` |
| **Sound** | timeline 上的最小文本单元：把一个 `Unit` 按 mora 数 flatten 后的一份；带显示 `label` 与一个 aligner `token`。 | `timeline.Sound` |
| NOTE | 卷帘上 channel 1 的音符 `(start, end)` 秒，按 start 排序；**也指**对齐后每个 Sound 落到的那个时间戳。 | |
| 对齐 | `sum(每个 Unit 的 mora) == NOTE 总数`；每个 Sound 恰好对一个 NOTE。 | §2 |
| raw 时间 | 每个 Sound 的原始对齐时间；`.nto` 的 `project.Lyrics.lines` 存 `(start, end)`。 | |
| onset 链 | 只保留 raw 的 **start**；`end` 由下一个 Sound 的 start 决定（见 §3）。 | |
| `|` | 条带上每个 Sound 起点的竖线，也是拖动把手。 | |
| 块 | 条带上 Sound 映射到的 NOTE 矩形。 | |
| `.0` | 没分到任何 NOTE 的 Sound；显示为灰、不画块。 | |
| group | 多个 Sound 共享同一个 NOTE；**只是显示**（悬浮高亮）。不要求能折成 `.krc` 的 `(...)`，也不改写 `.krc`（见 §13）。 | |

---

## 2. 数量对应（核心不变量）

**`sum(Unit.mora)` == NOTE 总数。**

- `Unit.mora` = 该 Unit 占的 **NOTE 槽数**（`.N` 或 `natural_mora`）；`sum(Unit.mora)` 就是 NOTE 总数。
- **Sound 数 = `sum(Unit.natural_mora)`**（读音数），与 NOTE 数**不必相等**：
  - `mora > Sound 数`：一个 Sound 跨多个 NOTE（held），如 `あ.2` → あ 从 NOTE1.start 到
    NOTE2.end；
  - `mora < Sound 数`：多个 Sound 共用一个 NOTE（**group**），如 `(あい).1` → あ、い 各占
    半个 NOTE。
- **`.N` override 的是「这个 Unit 占几个 NOTE」（`Unit.mora`），不是「有几个读音」。**
- 落空的 NOTE 必须交回前一个 Sound（并标红怀疑），否则总数对不上。
- 所以 `.0` **不是**「时长短」，而是「这个 Sound 没分到 NOTE」；显示为灰、不画块。
- 推论：一个完整占住自己的（哪怕很短的）NOTE 的 Sound **不得**判 `.0`，也就是**不得变灰**，
  否则那个 NOTE 没人覆盖。

---

## 3. onset 链：`karaoke.timeline.contiguous`

```
contiguous(times) -> list[list[(start, end)]]
```

- 对每一行：`spans[i].end = spans[i+1].start`（当两者 start 都非 None）。**忽略 aligner 给的 end。**
- 该行最后一个 mora 保留它自己的 end（没有下一个 `|` 可接）。
- `None` 不动：某 mora 的 start 为 `None` 时，前一个 mora 的 end 不被改写。

不变量：
- **INV-1** 一行内 mora 首尾相接：`raw[i].end == raw[i+1].start`。
- 只有 start 携带时间信息；end 是派生量。
- 作用点：`PianoRollView.load_lyrics`（载入/映射后）与 `set_sound_boundary`（拖动后）。

---

## 4. 映射算法：`karaoke.timeline.map_sounds`

签名：

```python
map_sounds(lines, times, notes, text="", flagged=None, *, aligned=True) -> list[list[Placement]]
```

`times` 必须是 onset 链（§3）。`notes` 是 channel 1 的 `(start, end)` 列表。
`flagged` 是 aligner 标为可疑的行。

常量（`timeline.py` 顶部）：

```
TOLERANCE = 0.05   # 帧级容差，用于 _inside / _count
SHARE = 0.25       # 共享同一 NOTE 的 mora，share 达到它才留下；不足的落 .0
```

NOTE 的 share 之和 ≈ 1（onset 链在 NOTE 内不重叠），所以「留下的都 ≥ 1/4」自然蕴含「最大最小之差
≤ 1/2」：两个 sharer 时等价于「边界落在 [1/4, 3/4] 内」。不再需要单独的 `EQUAL_BAND`。

### 4.1 步骤

1. **拉平**：把每行每个 mora 展成 `(row, column, start, end)` 的 `flat`。
2. **holders**：对每个 NOTE，收集**时间上与其重叠**的 mora（`min(end,high) > max(start,low)`）。
   start/end 为 `None` 或 `end < start` 的 mora 不参与。
3. **owner**（每个 NOTE 选归属）：
   - 无 holder → 先空着，进第 4 步。
   - 1 个 holder → 它独占该 NOTE。
   - ≥2 个 holder → 算每个的 `share`：
     - share = 从 `max(start, low)` 到 `min(下一个 mora 的 start, high)` 占整段 NOTE 的比例；
     - **每个 `share >= SHARE` 的 holder 都成为 owner**（≥2 个时成组，记 `grouped[mora] = note`）；
       `share < SHARE` 的落到 `.0`；
     - 若**没有任何** holder 达标（如 5 个均分各 0.2），只留最大 share 的那个当 owner。
       即：一个弱的 sharer 不得把强的那些一起拖落（R16）。
4. **落空的 NOTE**：对每个没有 owner 的 NOTE，交给**前一个**有 owner 的 mora（若前面没有则交给
   后面的），并把该 mora 记入 `doubted`（→ 红）。保证每个 NOTE 都被覆盖（§2）。
5. **covered / pieces**：
   - `covered[mora]` = 它拥有的 NOTE 列表；
   - 单 owner 的 NOTE：该 mora 拿到整段 `(low, high)`；
   - 多 owner（group）的 NOTE：按 share/total 把 NOTE 切成相邻的片，每个 owner 一片。
6. **Placement**：每个 mora 输出
   - 有 `covered` → `Placement(span=覆盖片段的并集, notes=拥有的 NOTE, red=flagged 或 doubted, group=grouped 或 -1)`；
   - 无 → `Placement(span=(point, point), zero=True, red=flagged)`，`point` = 自身 start（没有则
     第一个 NOTE 的 start）。

### 4.2 `Placement` 字段

```
span     # 映射后跨度（覆盖 NOTE 的并集，group 的切片，或 .0 的点）——**不是**条带画的东西
notes    # 覆盖的 NOTE 下标
zero     # 没分到 NOTE（→ .0）
red      # 被怀疑（行被 flagged，或 NOTE 是交回前一个 mora 的）
group    # 共享的 NOTE 下标；-1 表示独立
```

> 注意：条带的块画的是 **`Placement.span`（映射后）**，与卷帘音符对齐；`|`/标签画的是 raw
> 起点。`contiguous` 只喂给 `map_sounds`，不决定块宽。

### 4.3 `group_sounds`（不参与映射）

成组与否只看 share（§13.2），与 `.krc` 能否折成 `(...)` 无关；`group_sounds` 只服务 `.krc` 折叠/导出与测试。

---

## 5. 判定与颜色

| 状态 | 条件 | 条带 |
| --- | --- | --- |
| 绿 | 有 NOTE（`zero == False`）且 `red == False` | 画块，绿 |
| 红 | 有 NOTE 且 `red == True` | 画块，红 |
| 灰 | `zero == True` | **不画块**，`|` 与标签用灰色（`LYRIC_ZERO`） |

不变量：
- **INV-2** 灰色 ⟺ `.0`。**没有**基于"时长阈值"的灰色：占住自己 NOTE 的短 mora 是绿的。
- **INV-3** 非 `.0` 的 mora 永不灰。

---

## 6. 条带显示（`ui/strips.py::SoundStrip`）

- 与卷帘共用列/时间轴（`ViewportStrip.origin` + `view.mapFromScene`）。
- 每行按 `line.sounds`：
  - `start = raw[row][col][0]`；为 `None` 跳过。
  - 块：**映射后跨度 `lyric_times[row][col]`**（`map_sounds` 的 `Placement.span`）——它贴到 NOTE、
    与卷帘对齐，并且覆盖整个 NOTE（包括旁边 `.0` mora 所处的时段）；仅当 `end > start` 且非 `.0`。
  - `|`：在边界处画 `SOUND_LINE_PX` 宽竖线，颜色 = 白（非 `.0`）/灰（`.0`）。
  - **零长 mora 的两条相邻边界不得重合**：`_layout` 让**零长 mora 自己的那条 `|` 向左退一步**
    `SOUND_GAP_PX`，而它后面的 mora（起点在同一时刻）**留在真实位置**——即优先保证非 `.0`
    的位置正确，牺牲 `.0` 的显示位置。`SOUND_GAP_PX` = 一个 CJK 字宽 + 两侧 padding（当前 20px），
    所以退出来的格子还能显示一个字。
  - 标签（**自适应，不 elide**）：`room` = 本 `|` 到下一个 `|`（按下标计算，至少 0）：
    - `Sound.label` 放得下 → 画整条；
    - 否则是 ruby 词（`Sound.rubied`）且 `Sound.ruby` 放得下 → 只画 ruby（丢掉括号里的 base）；
    - 否则不画文字。
- 绘制顺序：块（窄的在后）→ group 高亮 → `|`+标签。
- **共享 NOTE 的内部切缝不画边**：两个 mora 同属一个 NOTE（`group[col] >= 0` 且相邻列 `group` 相同）
  时，它们相接的那条竖边不画（`_paint_body` 的 `left_join`/`right_join`），所以两块读起来是一整块。
  条带上只剩两种竖线：带标签的 `|`，以及不同 NOTE 块之间的边界。
- 悬浮：`_sound_at(x)` 命中某 mora，`_group_run` 取同 `group` 下标的连续 run；`_paint_groups` 给整段
  半透明 `LYRIC_SELECT` 填充 + 描边。
- tooltip = 命中 mora 的 `Sound.label`。
- 滚轮横向滚动。

---

## 7. 拖动编辑

- 把手：`|`。`_boundary_at(x)` 在所有行的边界里取最近、距离 `<= SOUND_GRAB_PX` 的；距离相同取
  先遇到的（低行/低下标）。边界位置用 `_layout`：零长 mora 的 `|` 左退 `SOUND_GAP_PX`，其余在
  真实位置，所以重合的对也能各自可抓。**被抓住的那条在拖动时画回它的真实时间**（`_layout`
  对该下标不做退让），拖动跟的是数据而不是显示上的偏移，松手后重新退开。
- 边界语义：边界 `i` = mora `i` 的 start = mora `i-1` 的 end（**共享边**）；边界 `len` = 最后一个
  mora 的 end。
- **拖动量按位移算**：press 时记下 `_press_seconds`（该边界真实秒）与 `_press_x`；move 时
  `travelled = seconds_at(x) - seconds_at(_press_x)`，`value = _press_seconds + travelled`。
  这样被 `_layout` 推开画出的边界不会在起拖时跳。
- `PianoRollView.set_sound_boundary(row, boundary, seconds, base)`：
  - `low = raw[boundary-1].start`（boundary 0 时 0.0），`high = raw[boundary].end`（末尾时 ∞）；
  - `value = clamp(seconds, low, high)`；写 `raw[boundary-1].end = value` 与 `raw[boundary].start = value`；
  - 再对整行 `contiguous`；emit `lyrics_changed`。
  - `base` = 拖动开始时该行的快照，每次 move 从 `base` 重算，所以拖回原位完全还原。
- **平滑**：`seconds` 直接是指针处时间，不做量化。
- **磁吸**：仅当指针距某条**画出来的节拍线**（`view.grid_step()`，含 `view.offset`）在
  `SOUND_MAGNET_PX` 像素内，才吸到该线（`_magnet_seconds`）。这是「另一套吸附」：不用 note snap，
  因为多个 mora 可能落在同一 note 格内。
- **撤销**：press 时 `begin_gesture("Move lyrics")`，release 时 `commit_gesture()`。一次拖动一步。
  `_RollState` 存 `lyric_raw`，`_state_data` 含它，所以歌词拖动会被 `_push` 识别。
- **提交后**：`commit_gesture` emit `notes_changed` → app `_remap_lyrics_async` 重跑 `map_sounds`
  → 刷新绿/红/灰/group；同时 `_mark_dirty` 置 dirty。
- 拖动只改 `lyric_raw`，**不动 NOTE**（A 方案）。

---

## 8. 对齐接入

- `AlignDialog` 产出每 mora `(start, end)`；若选了 Quantize，先过 `karaoke.snap_to_beats`。
- `snap_to_beats` 会把两端落在同一格内的 mora 收成零长（`start == end`）——这是 `.0` 的一个来源，
  等价于「对齐时选的 snap 决定初值」。
- 结果存 `project.Lyrics`，并 `_remap_lyrics`。
- `.krc` 外部改动：有缓存 pass 时 `lyrics.auto_align` 后台重对齐；否则保持 1:1 映射并提示。

---

## 9. 持久化与 dirty

- `.nto` 的 `lyrics: Lyrics{text, key, model, mode, lines}`；`key` = `.krc` 文本 hash，`model` = 对齐模型。
  `lines` 每 mora `(start, end)`。
- 载入：`MainWindow._watch_lyrics` 用 `Lyrics` 还原 raw（再过 `contiguous`）。
- 保存：`save_project` 写 `view.lyric_raw`。
- dirty：歌词编辑经 `notes_changed` 触发 `_mark_dirty`；`_state_data` 含 `lyric_raw`。
- **`.krc` 永不因编辑被写回。**

---

## 10. 回归清单（改代码前逐条核对）

- **R1** `.krc` 不被编辑器写入。
- **R2** 载入/拖动后每行 `raw` 满足 `raw[i].end == raw[i+1].start`（`contiguous`）。
- **R3** `map_sounds` 覆盖每个 NOTE 恰好一次（§2 数量对应）。
- **R4** 灰 ⟺ `zero`；非 `.0` 不灰（§5）。
- **R5** 短但占住自己 NOTE 的 mora 是绿的，不灰。
- **R6** 共享 NOTE 的 mora：`share >= SHARE`（1/4）的**每个**都留下并成组；不足 1/4 的落 `.0`；
  若全不足则只留最大 share 的。两个 sharer 时等价于「边界落在 [1/4, 3/4]」。
  **与能否折成 `.krc` 的 `(...)` 无关（见 R14）。**
- **R16** 判定是「逐个达标」，不是「全体达标」：一个 share 很小的 sharer 只能自己落 `.0`，
  不得让同一 NOTE 上 share 已经 ≥ 1/4 的其他 mora 也落 `.0`。
- **R7** `group` 仅在成组时 ≥ 0；悬浮高亮覆盖同一 `group` 的连续 run。
- **R8** 块 = 映射后的 `Placement.span`（贴 NOTE、与卷帘对齐、覆盖整个 NOTE）；`|`/标签 = raw 起点。
- **R8d** 同一 NOTE 的 group 成员之间不画竖边，两块读成一整块；条带竖线只有「带标签的 `|`」与
  「不同 NOTE 块的边界」两种。
- **R8b** 标签自适应：放不下整条 → ruby 只留读音 → 再放不下就不画。
- **R8c** 零长 mora 的两条 `|` 不重合：它自己的那条左退 `SOUND_GAP_PX`，它后面的 mora 留在真实
  位置（优先非 `.0`）；被抓住的那条在拖动时画回真实位置，拖动按位移计算，不跳。
- **R9** 拖动：共享边、平滑、磁吸到画出的节拍线（`SOUND_MAGNET_PX`）；一次拖动一步撤销。
- **R10** 拖动只改 `lyric_raw`，不改 NOTE；提交重映射 + 置 dirty。
- **R11** 条带与卷帘共列（缩放/滚动/offset 对齐）。
- **R12** `_row` 读 `(...)` 按成员字拆 mora（忽略 `.N`）；读它和读同一串未分组，token 流逐字相同。
- **R13** mora 数由读音/字面决定，`total_mora` 与 `_row` 的 mora 数一致。
- **R14** 显示成组只看 share（近且 `min>=SHARE`），ruby 词也能成组；不得用 `group_sounds`
  把关（它只服务 `.krc` 折叠）。
- **R15** 跨 `]` 的组合（`A[B(C]D)`）非法；显示 group 不必可写成 `.krc`。

---

## 11. 测试对照

| 不变量 | 测试 |
| --- | --- |
| onset 链 | `tests/test_timeline.py::test_contiguous_takes_each_mora_end_from_the_next_start` |
| `.0` = 无 NOTE | `tests/test_timeline.py::test_a_mora_that_covers_no_note_falls_to_no_length_without_doubt` |
| 共享 NOTE 判定 | `tests/test_timeline.py::test_two_sounds_that_share_a_note_equally_group_on_it`、`test_the_smaller_share_of_a_note_falls_to_no_length` |
| 弱 sharer 不拖累强的（R16） | `test_a_weak_sharer_does_not_drag_down_the_others` |
| 2–4 morae 分一个 NOTE | `test_a_note_split_a_quarter_to_three_quarters_still_groups`、`test_a_note_split_past_a_quarter_falls_to_no_length`、`test_four_sounds_each_holding_a_quarter_all_keep_the_note`、`test_four_sounds_under_a_quarter_fall_to_no_length` |
| NOTE 全被覆盖 | `test_a_note_no_mora_reaches_is_given_to_the_one_before_and_doubted` 等 |
| 块 = 映射 NOTE（全覆盖/贴节拍线） | `tests/test_ui.py::test_a_block_is_the_note_the_mora_maps_to` |
| 共享 NOTE 内部无竖边 | `tests/test_ui.py::test_the_pieces_of_a_shared_note_draw_no_edge_between_them` |
| 灰 ⟺ `.0`（app 级） | `tests/test_ui.py::test_a_mora_that_covers_no_note_is_marked_grey`、`test_a_mora_that_loses_a_shared_note_is_marked_grey` |
| group 元数据 | `test_a_grouped_run_is_marked_on_the_strip` |
| 拖动/共享边/撤销 | `test_dragging_a_mora_boundary_moves_the_shared_edge`、`test_a_boundary_drag_stops_at_its_own_mora_end` |
| 平滑+磁吸 | `test_a_lyric_drag_is_smooth_but_magnets_to_the_drawn_grid` |
| 落盘 | `test_a_lyric_drag_is_kept_in_the_project` |
| 高亮 run | `test_hovering_a_shared_note_lights_the_whole_group` |
| `|` 最小间隙 | `tests/test_ui.py::test_two_bars_never_land_on_each_other` |
| 拖动回到真实位置 | `tests/test_ui.py::test_a_grabbed_bar_drops_back_to_its_true_time` |
| 标签自适应 | `tests/test_ui.py::test_a_label_is_dropped_when_it_does_not_fit` |
| `(...)` 按字拆 Sound | `tests/test_timeline.py::test_a_group_reads_back_its_own_sounds` |
| Sound 折回 `.krc`（ruby 内 / 拆旧 group） | `tests/test_timeline.py::test_a_group_folds_inside_one_ruby_part`、`test_a_group_dissolves_a_group_it_starts_inside`、`test_grouping_refuses_a_crossing_run_and_a_lone_sound` |
| 忠实模式映射 | `tests/test_timeline.py::test_faithful_holds_a_word_with_more_notes_than_sounds`、`test_faithful_shares_a_note_between_sounds`、`test_faithful_stops_when_the_notes_run_out` |
| ruby 词也能成组（显示） | `tests/test_timeline.py::test_rubied_sounds_that_share_a_note_group_on_it`、`tests/test_ui.py::test_a_grouped_run_is_marked_on_the_strip` |

---

## 12. 当前偏差 / 待清理（不是行为，改前先处理）

- `ui/roll.py::PianoRollView.lyric_times`（映射后跨度）条带在用；`load_lyrics` 的 `times`
  （映射后）与 `raw`（onset 链）并存，别混用：`times` 只给块，`raw` 给映射输入与 `|`。
- §13 已实现：`_row(line, split_groups=True)` 把 `(...)` 拆成成员 Sound；`map_sounds` 成组只看
  share。`group_sounds` 在 flatten 视图上折叠（§14）。

---

## 13. `.krc` group / Sound 语义

> `split_groups` 是两套视图的开关：编辑器 / `sound_lines` 用 True（`(...)` 拆成成员 Sound），
> `group_sounds` 用 False（`(...)` 当一个 unit）。Sound 是操作中间的形态；导出是未 flatten 的
> `.krc`（§14）。

### 13.1 `.N`、mora 数、Sound 数

- `Unit.mora` = 该 Unit 占的 **NOTE 槽数**：默认 `natural_mora`（读音数），`.N` 覆盖它
  （`Unit.override`）。不是「有几个读音」。
- **Sound 数 = `sum(Unit.natural_mora)`**，不受 `.N` 影响：`あ.2` 仍是 1 个 Sound，
  `(あい).1` 仍是 2 个 Sound。
- `_row`（flatten）按读音拆 Sound（小假名并进前一格）。拉丁 `(...)`（`natural_mora == 1`）算
  1 个 Sound。
- **token 流不变量**：`胡椒[こ,しょう]` 与 `胡椒[こ,(しょう)]` 读出的 token 流必须逐字相同
  （`ko,sho,u`）。
- `.N` 与读音数不一致**不是错误**：`>` 是 held（一个 Sound 跨多个 NOTE），`<` 是 group
  （多个 Sound 共用一个 NOTE），见 §2/§15。

### 13.2 group 是显示概念

- 多个 Sound 共享同一个 NOTE → `map_sounds` 成组（`group >= 0`），条带悬浮高亮整段。
- 成组判定只看 **share**（`share >= SHARE`）；**不得**用 `group_sounds`
  （尝试折成 `(...)`）把关，因为：
  - ruby 词、跨 `]` 的组合可能无法写成 `.krc`，但仍应显示为 group。
- `group_sounds` 只服务 `.krc` 折叠/导出，不参与显示判定。

### 13.3 `.krc` 能表示 / 不能表示

| 想表达的 group | `.krc` | 说明 |
| --- | --- | --- |
| `胡椒[こ,しょう]` 本 | `胡椒[こ,しょう]` | ✅ |
| 同一 ruby part 内嵌套 | `胡椒[こ,(しょう)]` | ✅（`(しょう)` 读回 `しょ`,`う`） |
| 跨 `]`（`A[BC]D` 的 `C` 与 `D`） | `A[B(C]D).1` | ❌ 语法层就报错 |
| 把 kana 字并入 kanji base | `め椒[し,う]` | ❌ `椒` 只剩 1 字却有 2 part |

---

## 14. Sound ↔ `.krc` 转换边界（探索）

Sound 只是方便操作的中间形态；最终导出的是**未 flatten** 的 `.krc`。一个 run 能否写回 `.krc`，
取决于这些 Sound 来自哪个容器。

### 14.1 一个 Sound 的归属（container）

flatten（`_row(split_groups=True)`）后，每个 Sound 来自四类容器之一：

1. 顶层 plain `Unit`；
2. 顶层 `Group` 的成员 `Word`；
3. 某个 ruby part 的 inner `Unit`；
4. 某个 ruby part 内 `Group` 的成员 `Word`。

小假名并进前一个 Sound（写出来时随之带上）。

### 14.2 run 折成 `(...)`——允许

| run 的范围 | 写法 | 例 |
| --- | --- | --- |
| 全在顶层 | `(chars)` | `ワイドショー` 的 [ショ,ー] → `ワイド(ショー)` |
| 全在**同一个 ruby part** | 该 part 内 `(chars)` | `胡椒[こ,しょう]` 的 [しょ,う] → `胡椒[こ,(しょう)]` |

只给已有词重新加括号是 no-op：`(胡椒)[こ,しょう]`、`百合[(ゆり)]` 与不加一样。

### 14.3 不允许

- 跨 ≥2 个 ruby parts（`世界[せ,かい]` 的 せ 与 か）；
- 跨 ruby part 与顶层 word（`胡椒[こ,しょう]は` 的 う 与 は）；
- 任何会改变 token 流的 run（拉丁 run 与假名混折：`helloあ` 折成 `(helloあ)` 会读回 6 个 Sound）；
- 跨两个 ruby unit、跨两行。

### 14.4 不能嵌套，必须重排

`.krc` 的 `(...)` 不能嵌套（`((あい))` 解析失败，`WORD` 不含括号）。所以对一个**已有顶层 group
的子 run** 成组，必须拆掉旧括号再重排：`(あい)(うえ)` 把 い+う 成组 → `(あ)(いう)(え)`
（Sound/token 不变）。

### 14.5 小假名边界

`(ワイドシ)ョー` 与 `(ワイド)ショー` flatten 出同样的 5 个 Sound（ョ 并进 シ）。即括号可以落在
同一个 Sound 的两个字之间而 token 不变；但为可读，导出应把边界落在 Sound 边界上。

### 14.6 `.N` 与 flatten

`.N` 是 `Unit.mora`（NOTE 槽数）的 override，**不影响 Sound 数**（§13.1）。flatten 是否顺从它
取决于模式（§15）：

- **忠实模式**：顺从。`unit.mora` 个 NOTE 槽分给 `natural_mora` 个 Sound（§15.1）。
- **可编辑模式**：忽略 `.N`（和 group），按读音/字面 flatten；`.N` 只由导出时从映射重算。

---

## 15. 两种模式（``.krc` 作持久形态，Sound 作中间形态）

### 15.1 忠实模式（read-only）

- `.krc` 里**没有时间轴**，所以时间只能取 MIDI NOTE 的时间戳：本质是**不需要估计对应关系的**
  `map_sounds` —— `.krc` 已定死了对应关系。
- **尊重** `.krc` 原有的 group 与 `.N`：一个 Unit 的 `mora`（= `.N` 或自然值）就是它占的
  NOTE 槽数；按顺序把这些槽与 MIDI NOTE 一一对应，**一方不够就停**（不重排、不重叠、不 doubt）。
  槽分给 `natural_mora` 个 Sound：`mora > Sound 数` 是 held（一个 Sound 跨多个 NOTE），
  `mora < Sound 数` 是 group（多个 Sound 共用一个 NOTE）。
- 一个 Sound 占多个 NOTE 时，它的 span = 这些 NOTE 从第一个 start 到最后一个 end 的并集
  （`あ.2`：あ 从 NOTE1.start 到 NOTE2.end）。
- timeline **只读**；要改只能手改 `.krc`。
- flatten 服从 `.N`（§14.6）。

### 15.2 可编辑模式

- **忽略**输入的一切 group 和 `.N`，只按读音/字面的自然 mora flatten。
- 用 aligner 的时间 + `map_sounds` 对齐（重叠 / 成组 / doubt 那套）。
- 用户拖 `|`、分/合 Sound group。
- 导出 `.krc`：**Sound 的 group 直接转成 `.krc` 的 group** —— 可能是 ruby 内的 group，也可能是
  word unit 间的 group（§14）；`.N` = 该词占的 NOTE 数。两个 Sound 各占一个 NOTE 的一半就成
  group，写出来就是 `(あい).1`。
