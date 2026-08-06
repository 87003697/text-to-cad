# Settings UI Guidelines

The contract for every settings surface rendered inside a file sheet tab or the
theme editor panel: the Theme editor, the per-file Display tab, and the DXF,
G-code, STEP, URDF/SDF, mesh, and implicit sheets. The tab strip, navbar, and
sheet frame are out of scope — this document governs the *contents* of a tab.

Every pattern here has a primitive in
`src/client/components/workbench/FileSheet.js`. Build settings UI from those
primitives; do not hand-roll rows, labels, inputs, or switches inside a sheet.
If a new control shape is genuinely needed, add the primitive to `FileSheet.js`
first, then use it — never inline a one-off.

## Anatomy

```text
Tab body                    px-0, vertical stack of sections
└─ Section                  FileSheetSubsection: hairline rule + header + rows
   ├─ Header row            title (+ optional trailing control, e.g. gate switch)
   └─ Row stack             rows, 8px apart
      └─ Row                one setting: inline | slider | block | field grid
```

- A tab body is a flat list of sections. Sections never nest.
- **Every section carries a heading, and every row carries a label** — including
  a section that holds a single row, which shows both (`Material` / `Thickness`,
  `Appearance` / `Preset`, `Model` / `Mode`). A heading never stands in for a row's label: a labelless
  row reads as an orphaned control, and a row whose only name is the heading
  above it cannot be scanned in a list. Name the group and the control
  differently; if the only honest name for both is the same word, the group is
  wrong, not the label.
- Action rows are the one exception: a button says what it does, so a row of
  buttons (Reset, Flip, Play) takes no label.
- Everyday settings stay visible. Progressive disclosure is allowed only when a
  gate switch turns a whole feature off (Floor, Grid, Environment, a light):
  the switch stays, the dependent rows unmount.

## Spacing and sizing tokens

All values sit on a 4px grid. The panel gutter is 8px (`px-2`) on both sides;
every row aligns its label to the left gutter and its control to the right
gutter — one label axis, one control axis, no exceptions.

| Token | Value | Where |
| --- | --- | --- |
| Row height (inline) | `min-h-7` (28px) | switch, color, value, select-trailing rows |
| Control height | `h-7` (28px) | every input, select, button, stepper, picker |
| Dropdown width | `w-fit`, `min-w-20`, `max-w-44` | inline select trigger |
| Gap between rows | 8px (`space-y-2` stack) | within a section |
| Label → control, stacked | 4px (`space-y-1`) | inside one block row |
| Rule → heading | 16px (`mb-4` on the rule) | `FileSheetSubsection` owns it |
| Heading → first row | 12px (`pb-3`) | `FileSheetSubsection` owns it |
| Last row → next rule | 16px (`pb-4`) | `FileSheetSubsection` owns it |
| Row gutter | `px-2` | every row, list, and message |
| Grid gap (field grids, button rows) | `gap-2` / `gap-1.5` | see Field grids, Buttons |

A section's dividing rule belongs to its own top edge, so the 16px above a
heading and the 16px below the previous section's last row are the same
measurement seen twice: **the space on either side of every rule is equal.**
This holds when a gated section collapses to its heading alone: the heading's
bottom gap exists only to clear the first row, so with no rows it is dropped
and the collapsed section stays 16px on both sides.

Inside a section, rows sit 8px apart and the heading takes 12px to clear the
group it names. Four spacings for the whole panel, each a step on the 4px
grid: 4px binds a label to its control, 8px binds rows into a group, 12px sets
a heading off from its rows, 16px holds groups apart.

**Every control is exactly 28px tall** — input, select, colour picker, stepper,
button, segmented item — so a column of them shares one rhythm and one right
edge. Selects need `!h-7`, not `h-7`: the shadcn trigger carries
`data-[size=sm]:h-8`, and an attribute selector outranks a plain utility class,
which is how every dropdown in the panel silently stood 32px tall.

Never add ad-hoc `py-*`/`mt-*` spacing inside a tab; spacing belongs to the
stack and section primitives so rhythm cannot drift per surface.

## Type scale

| Role | Style |
| --- | --- |
| Section header | 11px, medium, full-strength `sidebar-foreground` |
| Row label | 11px, medium, muted (`FILE_SHEET_FIELD_LABEL_CLASSES`) |
| Control text / values | 11px, medium; numerics `tabular-nums`; hex/coords mono |
| Secondary line, units, meta | 10px, muted |
| Status / empty / loading text | 11px, muted, `px-2` |

Headers and row labels are the same size and case; they separate by *color
alone* — a header is full-strength, a label is muted. One type size across the
panel, two roles. Never reach for uppercase, tracking, or a smaller size to
mark a header.

- Muted text is always `text-muted-foreground`. `var(--ui-text-muted)` is a
  legacy alias; do not introduce new uses.
- Labels are sentence case, 1–3 words, leading with the distinguishing word
  ("Motion resolution", not "Resolution for motion"). No trailing colons.
- Boolean labels name the thing, not the action: "Floor", not "Enable floor".
  ARIA labels may keep the verb ("Enable floor") for screen readers.
- No helper sentences or added tooltips to explain a label; if a label needs a
  paragraph, the label is wrong. Existing `title` hints on options may stay.

## Row kinds

There are exactly four row kinds. Every setting uses one of them.

### 1. Inline row — `FileSheetInlineControlRow` / `FileSheetToggleRow`

Label left, control right, single 28px line. **This is the default row.** For:
switches, color pickers, read-only values, short numeric/text inputs, steppers,
and — the point most easily got wrong — selects and segmented controls.

- **Switches are always right-aligned at the control axis.** This includes
  section gate switches, which sit in the section header's trailing slot —
  never beside the title text. One vertical line of switches per panel.
- Switches apply instantly; a switch never needs a confirm/save step.
- The optional `description` line (10px, muted) is reserved for live counts or
  state readouts (e.g. travel-move count) — not prose.

### 2. Slider row — `FileSheetSliderField`

Label at top-left above the track, editable value box (`FileSheetValueInput`,
`w-20 h-7`, right-aligned, tabular) at the right control axis. The track fills
the remaining width. Every slider uses `FILE_SHEET_PRECISION_SLIDER_CLASSES`
and every slider shows its value; a slider without a numeric readout is not
allowed.

- Units live inside the value string: `52.0 mm`, `1.00x`, `45°`, `78%`, `1.2s`.
- Degrees are always `°`, never `deg`.
- Optional min/max micro-labels under the track (10px muted) are allowed when
  bounds are model-dependent (e.g. clip position); omit them for fixed 0–1
  ranges.

### 3. Block row — `FileSheetControlRow`

Label line on top (label left, optional value/trailing right), full-width
control underneath. For controls that genuinely need the whole gutter width:
editors (fill-color grid, position pad, explode-step list) and the one select
per surface that earns it (see below).

The label and its control are **one item**: the label line stays compact
(16px) and sits 4px above the control, tighter than the 8px between rows, so a
stacked pair reads as a unit rather than as two rows. Only a row whose control
lives in the trailing slot takes the full 28px line, matching the switch rows
beside it — `FileSheetControlRow` picks the right one from whether it was given
block content.

## Choosing a mode control

A control that picks one of several values is an **inline row like any other**:
label left, control right, on the shared control axis. A strip stretched across
the full width is not a settings row — it reads as a toolbar, and a column of
them turns the panel into a stack of unrelated widgets.

**A dropdown is the default.** `FileSheetSelectRow` handles every mode control
unless the options are short enough that a button group costs no more width
than the dropdown would — in practice a two-option pair of single short words
(a DXF bend's `Up`/`Down`). Two words as long as `Orthographic` and
`Perspective` are already too wide: that is a dropdown.

- Dropdowns: `Projection`, `Light` (5), `Backdrop` `Type` (4), explode
  `Direction` (5), `Layout`, `Order`, `Map`, animation pickers, every enum
  parameter.
- Segmented (`FileSheetSegmentedControl` with `fit`, sized to content, never
  stretched): DXF bend direction, and nothing else today.
- An inline trigger hugs its value between two bounds — never narrower than the
  standard 80px control, never wider than 176px — and truncates past that. Use
  a fixed max width, not a percentage: the row wrapper is shrink-to-fit, so a
  percentage resolves against a width the trigger itself sets, and the value
  overflows its own border instead of ellipsizing.
- Never use a Radix `Tabs` strip to switch an edit target inside a sheet — that
  was how the five-light selector ended up as a full-width row of tabs.

**The stacked exception.** A select is stacked full-width only when it is the
surface's *primary* control: the first row of the first section, whose value
reframes everything under it. There are exactly three — Theme › `Preset`,
Display › `Mode`, Joints › `Group state`. Pass `stacked` for those and for
nothing else; a second stacked select on one surface means one of them is not
primary.

### 4. Field grid — `FileSheetFieldGrid` + `FileSheetField`

A 2–3 column grid of micro-labelled fields, used **only** for tightly coupled
tuples that are read together: coordinates (X/Y/Z), solver numerics, document
facts. Label (11px muted) sits above its field; fields are 28px. This is the
one sanctioned label-above pattern; independent settings never use it.

- Editable cells: `Input`/`Select` at 28px, numerics right-aligned.
- Read-only cells: `FileSheetValueField` (bordered, muted fill, truncating).
  All read-only facts use it — never disabled `<Input>`s, never bespoke boxes.

## Buttons and actions

- All buttons in a sheet are compact: `size="sm"`, 28px, 11px text
  (`FILE_SHEET_COMPACT_BUTTON_CLASSES`), `variant="outline"` unless it is the
  single primary action of the tab.
- Sibling actions form a button row: equal-width columns
  (`grid grid-cols-N gap-1.5` inside a `FileSheetControlRow`), icon + label,
  centered. No ragged `flex-wrap` clusters.
- Reset is an outline button with the `RotateCcw` icon, full row width, placed
  as the last row of the section or tab it resets. Its label names the scope:
  "Reset", "Reset parameters", "Reset graphics".

## States

- Empty / loading / info: one pattern — `text-[11px] text-muted-foreground`
  in the row gutter (`px-2`), sentence case ("No movable joints.",
  "Loading STEP module...").
- Errors: same pattern in `text-destructive`.
- Disabled controls keep their row; hide rows only behind a section gate
  switch. Disabled state is the control's own (`disabled`), no extra styling.
- Non-obvious disabled reasons may use the existing `title` hint; do not add
  explanatory rows.

## Dark and light

Primitives only use theme tokens (`border`, `muted`, `accent`, `primary`,
`sidebar-*`). Never hard-code a palette color in a sheet; if a primitive needs
a fixed color pair (e.g. the switch track), it is defined once in
`FileSheet.js` with its dark variant beside it.

## Checklist for a new settings row

1. Pick the row kind (inline / slider / block / field grid) from the tables
   above — the control type decides, not taste.
2. Use the `FileSheet.js` primitive; pass `aria-label` for unlabeled controls.
3. Selects and segmented controls go inline on the right; `stacked` is only for
   a surface's primary control.
4. Label the row *and* its section, even when the section holds only this row.
5. Label: sentence case, 1–3 words, no verb prefix, no colon.
6. Value strings carry their unit; degrees are `°`.
7. No ad-hoc spacing, font sizes, or colors — tokens only.
