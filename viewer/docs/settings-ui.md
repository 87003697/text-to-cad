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
- A section without a meaningful name renders headerless (rule + rows only) —
  invented headings like "General" or "Settings" are noise.
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
| Gap between rows | 8px (`space-y-2` stack) | within a section |
| Gap between sections | ~20px (`py-2.5` + rule) | `FileSheetSubsection` owns it |
| Header → first row | 4px (`pb-1`) | `FileSheetSubsection` owns it |
| Row gutter | `px-2` | every row, list, and message |
| Grid gap (field grids, button rows) | `gap-2` / `gap-1.5` | see Field grids, Buttons |

Never add ad-hoc `py-*`/`mt-*` spacing inside a tab; spacing belongs to the
stack and section primitives so rhythm cannot drift per surface.

## Type scale

| Role | Style |
| --- | --- |
| Section header | 10px, medium, uppercase, `tracking-wider`, muted |
| Row label | 11px, medium, muted (`FILE_SHEET_FIELD_LABEL_CLASSES`) |
| Control text / values | 11px, medium; numerics `tabular-nums`; hex/coords mono |
| Secondary line, units, meta | 10px, muted |
| Status / empty / loading text | 11px, muted, `px-2` |

Section headers are visually distinct from row labels by *case and size*, not
by guesswork. If a header and a label look interchangeable, the header is
styled wrong.

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

Label left, control right, single 28px line. For: switches, color pickers,
read-only values, short numeric/text inputs.

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
control underneath. For controls that need the whole gutter width: selects,
segmented controls, editors (fill-color grid, position pad, step list).

- **Selects** always render as a block row with a full-width trigger:
  `SelectTrigger size="sm"`, 28px, 11px text; items 12px. Always pass an
  `aria-label`. No inline right-hand selects.
- **Segmented controls** (`ToggleGroup`, 2–5 short options) are the choice for
  mutually exclusive modes; 6+ options or long labels use a select. One
  segmented style (`FILE_SHEET_SEGMENTED_ITEM_CLASSES`, 28px) everywhere —
  including strips that switch an edit target (e.g. the Lights selector).

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
3. Label: sentence case, 1–3 words, no verb prefix, no colon.
4. Value strings carry their unit; degrees are `°`.
5. No ad-hoc spacing, font sizes, or colors — tokens only.
