import { Children, isValidElement, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, Contrast, FlipHorizontal2, Moon, MoreHorizontal, Pencil, Plus, RotateCcw, Save, Sun, Trash2, X } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle
} from "../ui/alert-dialog";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "../ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger
} from "../ui/dropdown-menu";
import { Input } from "../ui/input";
import { Slider } from "../ui/slider";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger
} from "../ui/tabs";
import { cn } from "@/ui/utils";
import {
  CUSTOM_THEME_ID,
  DEFAULT_FILL_LIGHT_SETTINGS,
  DEFAULT_RIM_LIGHT_SETTINGS,
  DEFAULT_THEME_ID,
  ENVIRONMENT_PRESETS,
  getThemePresetById,
  MAX_THEME_FILL_COLORS,
  resolveSystemThemePresetId,
  SYSTEM_THEME_ID,
  THEME_COLOR_MODES,
  THEME_PRESETS
} from "cadjs/lib/themeSettings";
import {
  DEFAULT_EXPLODED_VIEW_SETTINGS,
  CAMERA_PROJECTION,
  normalizeDisplaySettings,
  normalizeExplodedViewSettings
} from "cadjs/lib/displaySettings";
import { generateExplodedViewDocument } from "cadjs/lib/viewer/explodedView";
import {
  DISPLAY_MODE_OPTIONS
} from "../viewer/DisplayModeOptions";
import {
  OrthographicProjectionIcon,
  PerspectiveProjectionIcon
} from "../viewer/ProjectionModeIcons";
import {
  buildStepClipPatch,
  clipAxisBounds,
  clipAxisPosition,
  DEFAULT_STEP_CLIP_SETTINGS,
  normalizeStepClipSettings
} from "cadjs/lib/viewer/clipPlane";
import { FILE_SHEET_SECTION_IDS } from "@/workbench/fileSheetSections";
import { ScrollArea } from "../ui/scroll-area";
import FileSheet, {
  FILE_SHEET_COMPACT_BUTTON_CLASSES,
  FILE_SHEET_COMPACT_NUMERIC_INPUT_CLASSES,
  FILE_SHEET_PRECISION_SLIDER_CLASSES,
  FILE_SHEET_ROW_STACK_CLASSES,
  FILE_SHEET_SEGMENTED_TAB_TRIGGER_CLASSES,
  FILE_SHEET_SEGMENTED_TABS_LIST_CLASSES,
  FileSheetBooleanToggle,
  FileSheetButtonRow,
  FileSheetColorPicker,
  FileSheetColorRow,
  FileSheetControlRow,
  FileSheetSegmentedControl,
  FileSheetSelectRow,
  FileSheetSliderField,
  FileSheetStatusText,
  FileSheetSubsection,
  FileSheetToggleRow,
  parseFileSheetNumberInput
} from "./FileSheet";

const BACKGROUND_MODE_OPTIONS = [
  { value: "solid", label: "Solid" },
  { value: "linear", label: "Linear" },
  { value: "radial", label: "Radial" },
  { value: "transparent", label: "Transparent" }
];

const PROJECTION_MODE_OPTIONS = [
  { value: CAMERA_PROJECTION.ORTHOGRAPHIC, label: "Orthographic", title: "Parallel projection for CAD inspection", Icon: OrthographicProjectionIcon },
  { value: CAMERA_PROJECTION.PERSPECTIVE, label: "Perspective", title: "Depth projection with vanishing lines", Icon: PerspectiveProjectionIcon }
];


// Explosion direction: the axis parts travel along as they separate. Tooltips
// (title) carry the detail so the visible labels stay short.
const EXPLODE_DIRECTION_OPTIONS = [
  { value: "auto", label: "Auto (best fit)", title: "Pick the clearest axis automatically" },
  { value: "x", label: "X axis", title: "Separate left to right" },
  { value: "y", label: "Y axis", title: "Separate bottom to top" },
  { value: "z", label: "Z axis", title: "Separate front to back" },
  { value: "radial", label: "Radial", title: "Fan outward from the center" }
];

const EXPLODE_MODE_OPTIONS = [
  { value: "automatic", label: "Automatic" },
  { value: "custom", label: "Custom" }
];

const EXPLODE_ORDER_OPTIONS = [
  { value: "simultaneous", label: "Together", title: "Every part moves as you drag Amount" },
  { value: "sequential", label: "Sequence", title: "Parts separate in order across the Amount slider" }
];


const PRIMARY_LIGHT_OPTIONS = [
  { value: "directional", label: "Key" },
  { value: "fill", label: "Fill" },
  { value: "rim", label: "Rim" },
  { value: "spot", label: "Spot" },
  { value: "point", label: "Point" }
];

// Drafts saved before fill/rim joined the theme schema lack these lights.
const PRIMARY_LIGHT_FALLBACKS = Object.freeze({
  fill: DEFAULT_FILL_LIGHT_SETTINGS,
  rim: DEFAULT_RIM_LIGHT_SETTINGS
});

// Fill and rim colors are not mode-color paths, so they use a plain color field.
const MODE_COLOR_LIGHT_KEYS = Object.freeze(["directional", "spot", "point"]);

const compactButtonClasses = FILE_SHEET_COMPACT_BUTTON_CLASSES;
const precisionSliderClasses = FILE_SHEET_PRECISION_SLIDER_CLASSES;
const SLIDER_COMMIT_DELAY_MS = 120;
const AXIS_OPTIONS = Object.freeze(["x", "y", "z"]);
function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function formatNumber(value, digits = 2) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return "0";
  }
  return numericValue.toFixed(digits);
}

function formatMm(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return "0";
  }
  if (Math.abs(numericValue) >= 100) {
    return numericValue.toFixed(0);
  }
  if (Math.abs(numericValue) >= 10) {
    return numericValue.toFixed(1);
  }
  return numericValue.toFixed(2);
}

function Field({ label, value, trailing, children, className, contentClassName }) {
  return (
    <FileSheetControlRow
      label={label}
      value={value}
      trailing={trailing}
      className={className}
      contentClassName={contentClassName}
    >
      {children}
    </FileSheetControlRow>
  );
}

// A section whose contents are gated by an on/off switch renders that switch
// in the header's trailing slot, on the same right-edge control axis as every
// row switch below it — one vertical line of switches per panel (see
// viewer/docs/settings-ui.md). `toggle` is the on/off for this section;
// `trailing` stays for anything else that belongs at that edge.
function ControlSubsection({
  title,
  toggle = null,
  trailing = null,
  children,
  className,
  hideFirstSeparator = true
}) {
  return (
    <FileSheetSubsection
      title={title}
      trailing={toggle || trailing}
      className={className}
      hideFirstSeparator={hideFirstSeparator}
    >
      {children}
    </FileSheetSubsection>
  );
}

// The switch that gates a ControlSubsection's contents.
function SubsectionToggle({ label, checked, onCheckedChange, disabled = false }) {
  return (
    <FileSheetBooleanToggle
      checked={checked}
      onCheckedChange={onCheckedChange}
      disabled={disabled}
      ariaLabel={label}
    />
  );
}

function getSliderInputProps(children) {
  try {
    const child = Children.only(children);
    return isValidElement(child) && child.type === SliderInput ? child.props : null;
  } catch {
    return null;
  }
}

function SliderField({ label, value, children, onValueCommit, valueInputProps }) {
  const sliderInputProps = getSliderInputProps(children);
  const commitValue = onValueCommit || (
    sliderInputProps?.onChange ? (nextValue) => {
      sliderInputProps.onChange(parseFileSheetNumberInput(nextValue, {
        fallback: sliderInputProps.value,
        min: sliderInputProps.min,
        max: sliderInputProps.max
      }));
    } : null
  );

  return (
    <FileSheetSliderField
      label={label}
      value={value}
      onValueCommit={commitValue}
      valueInputProps={commitValue ? {
        ariaLabel: `${label} value`,
        ...valueInputProps
      } : valueInputProps}
    >
      {children}
    </FileSheetSliderField>
  );
}

function ThemeToggleRow({ label, checked, onChange, disabled = false, description }) {
  return (
    <FileSheetToggleRow
      label={label}
      checked={checked}
      onCheckedChange={onChange}
      disabled={disabled}
      description={description}
    />
  );
}

function SliderInput({ value, min, max, step = 0.01, onChange }) {
  const numericValue = Number.isFinite(Number(value)) ? Number(value) : min;
  const [draftValue, setDraftValue] = useState(numericValue);
  const commitTimerRef = useRef(null);

  useEffect(() => {
    setDraftValue(numericValue);
  }, [numericValue]);

  useEffect(() => () => {
    if (commitTimerRef.current) {
      clearTimeout(commitTimerRef.current);
    }
  }, []);

  const resolveNextValue = (nextValue) => {
    const numericNextValue = Number(nextValue);
    return Number.isFinite(numericNextValue) ? clamp(numericNextValue, min, max) : numericValue;
  };

  const commitValue = (nextValue) => {
    const resolvedNextValue = resolveNextValue(nextValue);
    if (commitTimerRef.current) {
      clearTimeout(commitTimerRef.current);
    }
    if (Math.abs(resolvedNextValue - numericValue) > 1e-9) {
      onChange(resolvedNextValue);
    }
  };

  const scheduleCommitValue = (nextValue) => {
    const resolvedNextValue = resolveNextValue(nextValue);
    setDraftValue(resolvedNextValue);
    if (commitTimerRef.current) {
      clearTimeout(commitTimerRef.current);
    }
    commitTimerRef.current = setTimeout(() => {
      commitValue(resolvedNextValue);
    }, SLIDER_COMMIT_DELAY_MS);
  };

  return (
    <Slider
      value={[draftValue]}
      min={min}
      max={max}
      step={step}
      onValueChange={(nextValue) => scheduleCommitValue(nextValue[0] ?? draftValue)}
      onValueCommit={(nextValue) => commitValue(nextValue[0] ?? draftValue)}
      className={precisionSliderClasses}
    />
  );
}

const ColorInput = FileSheetColorPicker;
const ColorField = FileSheetColorRow;

function getPathValue(source, path) {
  return path.reduce((value, key) => (
    value && typeof value === "object" ? value[key] : undefined
  ), source);
}

function setPathValue(target, path, value) {
  let cursor = target;
  for (let index = 0; index < path.length - 1; index += 1) {
    const key = path[index];
    if (!cursor[key] || typeof cursor[key] !== "object" || Array.isArray(cursor[key])) {
      cursor[key] = {};
    }
    cursor = cursor[key];
  }
  cursor[path[path.length - 1]] = value;
}

function cloneModeColors(modeColors = {}) {
  return {
    light: JSON.parse(JSON.stringify(modeColors.light || {})),
    dark: JSON.parse(JSON.stringify(modeColors.dark || {}))
  };
}

function activeThemeColorMode(themeSettings = {}, resolvedColorSchemeMode = THEME_COLOR_MODES.LIGHT) {
  if (themeSettings.colorMode === THEME_COLOR_MODES.DARK) {
    return THEME_COLOR_MODES.DARK;
  }
  if (themeSettings.colorMode === THEME_COLOR_MODES.LIGHT) {
    return THEME_COLOR_MODES.LIGHT;
  }
  return resolvedColorSchemeMode === THEME_COLOR_MODES.DARK
    ? THEME_COLOR_MODES.DARK
    : THEME_COLOR_MODES.LIGHT;
}

function themeModeColorValue(themeSettings = {}, path = [], mode = THEME_COLOR_MODES.LIGHT) {
  return getPathValue(themeSettings.modeColors?.[mode], path) ||
    getPathValue(themeSettings, path) ||
    "#ffffff";
}

function ColorModeIndicatorLabel({ label, mode }) {
  const isDarkMode = mode === THEME_COLOR_MODES.DARK;
  const ModeIcon = isDarkMode ? Moon : Sun;
  const modeLabel = isDarkMode ? "dark" : "light";
  return (
    <span className="inline-flex max-w-full items-center gap-1 align-bottom" title={`Uses the ${modeLabel} mode color`}>
      <span className="min-w-0 truncate">{label}</span>
      <ModeIcon className="size-2.5 shrink-0 text-muted-foreground/70" strokeWidth={2.25} aria-hidden="true" />
      <span className="sr-only">{`Uses the ${modeLabel} mode color`}</span>
    </span>
  );
}

function ColorModeField({
  label,
  path,
  themeSettings,
  onChange,
  resolvedColorSchemeMode = THEME_COLOR_MODES.LIGHT
}) {
  const colorMode = themeSettings.colorMode || THEME_COLOR_MODES.SYSTEM;
  const mode = activeThemeColorMode(themeSettings, resolvedColorSchemeMode);
  if (colorMode === THEME_COLOR_MODES.SYSTEM) {
    return (
      <ColorField
        label={<ColorModeIndicatorLabel label={label} mode={mode} />}
        value={themeModeColorValue(themeSettings, path, mode)}
        onChange={(nextValue) => onChange(path, nextValue, mode)}
      />
    );
  }

  return (
    <ColorField
      label={label}
      value={themeModeColorValue(themeSettings, path, mode)}
      onChange={(nextValue) => onChange(path, nextValue)}
    />
  );
}

function resolveFillColors(materials = {}) {
  const colors = Array.isArray(materials.fillColors) && materials.fillColors.length
    ? materials.fillColors
    : [materials.defaultColor || "#ffffff"];
  return colors.slice(0, MAX_THEME_FILL_COLORS);
}

function settingsSignature(settings) {
  return JSON.stringify(normalizeThemeSettings(settings));
}

function FillColorEditor({ colors, onChange }) {
  const resolvedColors = colors.length ? colors : ["#ffffff"];
  const commitColors = (nextColors) => {
    const compactColors = nextColors.filter(Boolean).slice(0, MAX_THEME_FILL_COLORS);
    onChange(compactColors.length ? compactColors : [resolvedColors[0] || "#ffffff"]);
  };

  return (
    <div
      className="flex flex-wrap justify-start gap-1.5"
      data-cad-fill-color-grid="true"
    >
      {resolvedColors.map((color, index) => (
        <div
          key={index}
          className="group relative transition-opacity"
        >
          <ColorInput
            value={color}
            swatchClassName="size-3.5"
            onChange={(nextColor) => {
              const nextColors = [...resolvedColors];
              nextColors[index] = nextColor;
              commitColors(nextColors);
            }}
            aria-label={`Fill color ${index + 1}`}
            title={`Fill color ${index + 1}: ${color}`}
          />
          {resolvedColors.length > 1 ? (
            <Button
              type="button"
              variant="outline"
              size="icon-xs"
              className="absolute -right-1.5 -top-1.5 z-10 size-4 rounded-full border-border !bg-[rgb(245_247_250)] p-0 text-muted-foreground shadow-xs hover:!bg-[rgb(245_247_250)] hover:text-foreground dark:!bg-[rgb(12_15_22)] dark:hover:!bg-[rgb(12_15_22)]"
              onClick={() => commitColors(resolvedColors.filter((_, colorIndex) => colorIndex !== index))}
              aria-label={`Remove color ${index + 1}`}
              title={`Remove color ${index + 1}`}
            >
              <X className="h-2.5 w-2.5" strokeWidth={2.25} aria-hidden="true" />
            </Button>
          ) : null}
        </div>
      ))}
      {resolvedColors.length < MAX_THEME_FILL_COLORS ? (
        <Button
          type="button"
          variant="outline"
          size="icon-sm"
          className="size-7 rounded-md p-0 text-muted-foreground hover:text-foreground"
          onClick={() => commitColors([...resolvedColors, resolvedColors[resolvedColors.length - 1] || "#ffffff"])}
          aria-label="Add fill color"
          title="Add fill color"
        >
          <Plus className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
        </Button>
      ) : null}
    </div>
  );
}

const SegmentedControl = FileSheetSegmentedControl;

// Two boxes: the canvas the theme paints behind the model, and the colour parts
// are filled with by default. One box alone can't tell a light theme with dark
// parts from a dark theme with light parts.
function PresetSwatch({ preview = null }) {
  return (
    <span className="inline-flex shrink-0 overflow-hidden rounded-[3px] border border-border/70" aria-hidden="true">
      <span
        className="block size-3.5"
        style={{ background: preview?.background || "var(--muted)" }}
      />
      <span
        className="block size-3.5 border-l border-border/70"
        style={{ background: preview?.modelColor || "var(--muted)" }}
      />
    </span>
  );
}

// The custom slot has no authored preview, so read the same two colours out of
// the settings the user has actually edited.
function customThemePreview(themeSettings) {
  const background = themeSettings?.background || {};
  const fillColors = themeSettings?.materials?.fillColors;
  const backgroundCss = background.type === "radial"
    ? `radial-gradient(circle at 42% 32%, ${background.radialInner} 0%, ${background.radialOuter} 78%)`
    : background.type === "linear"
    ? `linear-gradient(${Number(background.linearAngle) || 135}deg, ${background.linearStart} 0%, ${background.linearEnd} 100%)`
    : background.solidColor;
  return {
    background: backgroundCss || "",
    modelColor: (Array.isArray(fillColors) ? fillColors[0] : "") || ""
  };
}

// Tracks the OS light/dark preference so the System entry can name the preset it
// currently resolves to.
export function useSystemDefaultThemePresetId() {
  const [prefersDark, setPrefersDark] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return undefined;
    }
    let query;
    try {
      query = window.matchMedia("(prefers-color-scheme: dark)");
    } catch {
      return undefined;
    }
    const sync = () => setPrefersDark(query.matches === true);
    sync();
    query.addEventListener?.("change", sync);
    return () => query.removeEventListener?.("change", sync);
  }, []);
  return resolveSystemThemePresetId({ prefersDark });
}

// The theme picker: System, then the built-in presets. Presets are read-only, so
// picking one is both "apply" and "reset" — there is no save, restore, rename, or
// delete. Editing any setting moves the theme into the custom slot, which is not
// a list item: it is only ever the trigger's label, because "Custom" is a state
// you leave by picking a preset, not something you can pick.
function ThemePresetSection({
  themeSettings,
  themeId = DEFAULT_THEME_ID,
  onSelectTheme
}) {
  const systemPresetId = useSystemDefaultThemePresetId();
  const systemPreset = getThemePresetById(systemPresetId);
  const options = useMemo(() => [
    {
      value: SYSTEM_THEME_ID,
      label: "System",
      preview: systemPreset?.preview || null
    },
    ...THEME_PRESETS.map((preset) => ({
      value: preset.id,
      label: preset.label,
      preview: preset.preview || null
    }))
  ], [systemPreset]);

  const isCustom = themeId === CUSTOM_THEME_ID;
  const activeOption = options.find((option) => option.value === themeId) || null;
  const triggerLabel = isCustom ? "Custom" : (activeOption?.label || options[0].label);
  const triggerPreview = isCustom
    ? customThemePreview(themeSettings)
    : (activeOption?.preview || options[0].preview);

  return (
    <ControlSubsection title="Appearance">
      <FileSheetSelectRow
        label="Preset"
        value={isCustom ? "" : (activeOption?.value || options[0].value)}
        onValueChange={(nextValue) => onSelectTheme?.(nextValue)}
        ariaLabel="Theme preset"
        triggerContent={(
          <span className="flex min-w-0 items-center gap-2">
            <PresetSwatch preview={triggerPreview} />
            <span className="truncate">{triggerLabel}</span>
          </span>
        )}
        options={options.map((option) => ({
          value: option.value,
          label: option.label,
          icon: <PresetSwatch preview={option.preview} />
        }))}
      />
    </ControlSubsection>
  );
}

function PositionPad({ value, onChange }) {
  const resolvedX = Number.isFinite(Number(value?.x)) ? Number(value.x) : 0;
  const resolvedZ = Number.isFinite(Number(value?.z)) ? Number(value.z) : 0;
  const [draftPosition, setDraftPosition] = useState({ x: resolvedX, z: resolvedZ });
  const draftPositionRef = useRef(draftPosition);
  const commitTimerRef = useRef(null);
  const x = draftPosition.x;
  const z = draftPosition.z;

  useEffect(() => {
    const nextPosition = { x: resolvedX, z: resolvedZ };
    draftPositionRef.current = nextPosition;
    setDraftPosition(nextPosition);
  }, [resolvedX, resolvedZ]);

  useEffect(() => () => {
    if (commitTimerRef.current) {
      clearTimeout(commitTimerRef.current);
    }
  }, []);

  const extent = useMemo(() => {
    const magnitude = Math.max(Math.abs(x), Math.abs(z), 220);
    return Math.min(5000, Math.ceil((magnitude * 1.2) / 20) * 20);
  }, [x, z]);

  const markerLeft = ((x + extent) / (extent * 2)) * 100;
  const markerTop = ((extent - z) / (extent * 2)) * 100;

  const commitPosition = (nextX, nextZ) => {
    if (commitTimerRef.current) {
      clearTimeout(commitTimerRef.current);
    }
    if (nextX !== resolvedX) {
      onChange("x", nextX);
    }
    if (nextZ !== resolvedZ) {
      onChange("z", nextZ);
    }
  };

  const scheduleCommitPosition = (nextX, nextZ) => {
    const nextPosition = { x: nextX, z: nextZ };
    draftPositionRef.current = nextPosition;
    setDraftPosition(nextPosition);
    if (commitTimerRef.current) {
      clearTimeout(commitTimerRef.current);
    }
    commitTimerRef.current = setTimeout(() => {
      commitPosition(nextX, nextZ);
    }, SLIDER_COMMIT_DELAY_MS);
  };

  const updateFromPointer = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      return;
    }
    const ratioX = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    const ratioY = clamp((event.clientY - rect.top) / rect.height, 0, 1);
    const nextX = Math.round((ratioX * 2 - 1) * extent);
    const nextZ = Math.round((1 - ratioY * 2) * extent);
    scheduleCommitPosition(nextX, nextZ);
  };

  return (
    <div className="space-y-2">
      <div
        className="relative h-28 w-full touch-none overflow-hidden rounded-md border bg-background"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          updateFromPointer(event);
        }}
        onPointerMove={(event) => {
          if (!event.currentTarget.hasPointerCapture(event.pointerId)) {
            return;
          }
          updateFromPointer(event);
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId);
          }
          commitPosition(draftPositionRef.current.x, draftPositionRef.current.z);
        }}
      >
        <div
          className="absolute inset-0 opacity-45"
          style={{
            backgroundImage: "radial-gradient(circle, rgba(154, 169, 188, 0.65) 1.5px, transparent 1.5px)",
            backgroundSize: "22px 22px"
          }}
          aria-hidden="true"
        />
        <div className="absolute inset-x-0 top-1/2 h-px bg-border" aria-hidden="true" />
        <div className="absolute inset-y-0 left-1/2 w-px bg-border" aria-hidden="true" />
        <div
          className="absolute size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border border-primary bg-foreground shadow-xs"
          style={{ left: `${markerLeft}%`, top: `${markerTop}%` }}
          aria-hidden="true"
        />
      </div>
      <div className="flex items-center justify-between text-[10px] text-muted-foreground">
        <span>X {Math.round(x)}</span>
        <span>Z {Math.round(z)}</span>
        <span>range ±{extent}</span>
      </div>
    </div>
  );
}

// Pseudo display-records built from a mesh-data part list, sufficient for the
// pure auto-explode generator (it only reads partId + partBounds).
function explodePseudoRecords(meshData) {
  const parts = Array.isArray(meshData?.parts) ? meshData.parts : [];
  return parts
    .filter((part) => part && (part.bounds || part.sourceBounds))
    .map((part) => ({
      // Match the display-record id precedence (id || occurrenceId) so generated
      // step targets resolve against the runtime records.
      partId: String(part.id || part.occurrenceId || "").trim(),
      partBounds: part.bounds || part.sourceBounds,
      mesh: true
    }))
    .filter((record) => record.partId);
}

// Numeric step magnitude editor with local text state so partial input
// (empty, "-", "1.") is allowed while typing; commits a finite value on blur or
// Enter and reverts to the last valid value otherwise.
function ExplodeStepMagnitudeInput({ value, step, onCommit, ariaLabel, title }) {
  const [text, setText] = useState(String(value));
  useEffect(() => {
    setText(String(value));
  }, [value]);
  const commit = () => {
    const next = Number(text);
    if (text.trim() !== "" && Number.isFinite(next)) {
      onCommit(next);
    } else {
      setText(String(value));
    }
  };
  return (
    <input
      type="number"
      className={cn(FILE_SHEET_COMPACT_NUMERIC_INPUT_CLASSES, "w-16 rounded-md border border-input bg-transparent text-right")}
      value={text}
      step={step}
      onChange={(event) => setText(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.currentTarget.blur();
        }
      }}
      aria-label={ariaLabel}
      title={title}
    />
  );
}

function explodeTargetNameMap(meshData) {
  const map = new Map();
  for (const part of Array.isArray(meshData?.parts) ? meshData.parts : []) {
    const id = String(part?.occurrenceId || part?.id || "").trim();
    if (id) {
      map.set(id, String(part?.name || part?.label || id).trim() || id);
    }
  }
  return map;
}

function explodeStepLabel(step, nameMap) {
  const names = (step.targets || []).map((target) => nameMap.get(target) || target);
  const head = names[0] || "part";
  return names.length > 1 ? `${head} +${names.length - 1}` : head;
}

// Clip: slice the STEP model with an axis-aligned section plane. The X/Y/Z
// sliders move the cut; Flip swaps the kept side. Sliders are disabled until
// model bounds are known. Always visible — there is no separate on/off, because
// an offset of 0 already means "no cut".
function ClipSubsection({
  displaySettings,
  updateDisplaySettings,
  clipBounds = null
}) {
  const clip = useMemo(
    () => normalizeStepClipSettings(displaySettings.clip),
    [displaySettings]
  );
  const setClip = (patch) => {
    updateDisplaySettings?.((current) => {
      const currentSettings = normalizeDisplaySettings(current);
      return { ...currentSettings, clip: buildStepClipPatch(currentSettings.clip, patch) };
    });
  };
  const resetClip = () => {
    updateDisplaySettings?.((current) => ({
      ...normalizeDisplaySettings(current),
      clip: normalizeStepClipSettings(DEFAULT_STEP_CLIP_SETTINGS)
    }));
  };
  const updateClipAxisOffset = (axis, nextOffset) => {
    const numericOffset = Number(nextOffset);
    const resolvedOffset = Number.isFinite(numericOffset) ? numericOffset : 0;
    setClip({
      axis,
      offset: resolvedOffset,
      offsets: { [axis]: resolvedOffset },
      enabled: resolvedOffset > 0
    });
  };

  return (
    <ControlSubsection title="Clip">
      {AXIS_OPTIONS.map((axis) => {
        const axisOffset = clip.offsets?.[axis] ?? DEFAULT_STEP_CLIP_SETTINGS.offsets[axis];
        const axisSettings = {
          ...clip,
          axis,
          offset: axisOffset,
          offsets: { ...clip.offsets, [axis]: axisOffset }
        };
        const boundsForAxis = clipAxisBounds(clipBounds, axis);
        const axisRange = Math.max(boundsForAxis.max - boundsForAxis.min, 0);
        const clipPosition = clipAxisPosition(clipBounds, axisSettings);
        return (
          <FileSheetSliderField
            key={axis}
            label={axis.toUpperCase()}
            value={`${formatMm(clipPosition)} mm`}
            onValueCommit={(nextValue) => {
              const nextPosition = parseFileSheetNumberInput(nextValue, {
                fallback: clipPosition,
                min: boundsForAxis.min,
                max: boundsForAxis.max
              });
              updateClipAxisOffset(
                axis,
                axisRange > 0 ? (nextPosition - boundsForAxis.min) / axisRange : axisOffset
              );
            }}
            valueInputProps={{
              disabled: !axisRange,
              ariaLabel: `Clip ${axis.toUpperCase()} position`
            }}
          >
            <Slider
              className={precisionSliderClasses}
              value={[axisOffset]}
              min={0}
              max={1}
              step={0.001}
              disabled={!axisRange}
              onValueChange={(value) => {
                const nextOffset = Array.isArray(value) ? value[0] : value;
                updateClipAxisOffset(axis, nextOffset);
              }}
              aria-label={`Clip ${axis.toUpperCase()} axis`}
            />
            <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
              <span>{formatMm(boundsForAxis.min)}</span>
              <span>{formatMm(boundsForAxis.max)}</span>
            </div>
          </FileSheetSliderField>
        );
      })}

      <FileSheetButtonRow>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={cn(compactButtonClasses, "justify-center")}
          onClick={() => setClip({ invert: !clip.invert })}
          aria-pressed={clip.invert}
          title="Flip clip side"
        >
          <FlipHorizontal2 className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
          <span>Flip</span>
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={cn(compactButtonClasses, "justify-center")}
          onClick={resetClip}
          title="Reset clip plane"
        >
          <RotateCcw className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
          <span>Reset</span>
        </Button>
      </FileSheetButtonRow>
    </ControlSubsection>
  );
}

// Exploded view. On/off lives in the Amount value (0 = fully assembled), so the
// header switch just moves Amount to/from zero and remembers the last non-zero
// value. Automatic layout covers the common case; the Layout switch — kept above
// the controls it swaps — brings in an editable per-part step list.
// Model: lib/viewer/explodedViewSteps.js.
function ExplodedSubsection({
  displaySettings,
  updateDisplaySettings,
  explodeMeshData = null
}) {
  const exploded = useMemo(
    () => normalizeExplodedViewSettings(displaySettings.exploded),
    [displaySettings]
  );

  const setExploded = (patch) => {
    updateDisplaySettings?.((current) => {
      const currentSettings = normalizeDisplaySettings(current);
      return {
        ...currentSettings,
        exploded: normalizeExplodedViewSettings({ ...currentSettings.exploded, ...patch })
      };
    });
  };
  const setExplodedAuto = (autoPatch) => {
    updateDisplaySettings?.((current) => {
      const currentSettings = normalizeDisplaySettings(current);
      return {
        ...currentSettings,
        exploded: normalizeExplodedViewSettings({
          ...currentSettings.exploded,
          auto: { ...currentSettings.exploded.auto, ...autoPatch }
        })
      };
    });
  };

  const nameMap = useMemo(() => explodeTargetNameMap(explodeMeshData), [explodeMeshData]);
  const partCount = useMemo(() => explodePseudoRecords(explodeMeshData).length, [explodeMeshData]);
  const hasSteps = exploded.steps.length > 0;
  const singlePart = Boolean(explodeMeshData) && partCount <= 1 && !hasSteps;
  const canCustomize = partCount > 1 || hasSteps;
  const active = exploded.enabled && exploded.amount > 0;
  // While disabled the slider reads 0% (assembled), regardless of the stored
  // amount, so on/off and the Amount value always agree.
  const displayAmount = active ? exploded.amount : 0;
  const amountPercent = Math.round(displayAmount * 100);

  // On/off is expressed through Amount: 0 assembles the model. Keep the viewer's
  // `enabled` flag in step so a non-zero amount actually explodes, and remember
  // the last non-zero amount so the switch restores it rather than jumping to 100%.
  const lastAmountRef = useRef(exploded.amount > 0 ? exploded.amount : 1);
  useEffect(() => {
    if (exploded.amount > 0) {
      lastAmountRef.current = exploded.amount;
    }
  }, [exploded.amount]);
  const setAmount = (nextAmount) => {
    const clamped = clamp(nextAmount, 0, 1);
    setExploded({ amount: clamped, enabled: clamped > 0 });
  };

  const convertToSteps = () => {
    const records = explodePseudoRecords(explodeMeshData);
    const generated = generateExplodedViewDocument(
      null,
      records,
      explodeMeshData?.bounds || null,
      exploded.auto,
      {
        enabled: exploded.enabled,
        amount: exploded.amount,
        order: exploded.order,
        trails: exploded.trails
      }
    );
    setExploded({ steps: generated.steps });
  };
  const discardSteps = () => setExploded({ steps: [] });
  const setMode = (mode) => {
    if (mode === "custom" && !hasSteps) {
      convertToSteps();
    } else if (mode === "automatic" && hasSteps) {
      discardSteps();
    }
  };
  const updateStep = (index, patch) => {
    setExploded({
      steps: exploded.steps.map((step, i) => (i === index ? { ...step, ...patch } : step))
    });
  };
  const removeStep = (index) => {
    setExploded({ steps: exploded.steps.filter((_, i) => i !== index) });
  };
  const moveStep = (index, delta) => {
    const steps = [...exploded.steps];
    const target = index + delta;
    if (target < 0 || target >= steps.length) {
      return;
    }
    [steps[index], steps[target]] = [steps[target], steps[index]];
    setExploded({ steps });
  };

  return (
    <ControlSubsection
      title="Exploded"
      toggle={(
        <SubsectionToggle
          label="Exploded view"
          checked={active}
          onCheckedChange={(checked) => setAmount(checked ? (lastAmountRef.current || 1) : 0)}
          disabled={singlePart}
        />
      )}
    >
      {singlePart ? (
        <FileSheetStatusText>Single part — nothing to explode.</FileSheetStatusText>
      ) : active ? (
        <>
          <FileSheetSliderField
            label="Amount"
            value={`${amountPercent}%`}
            onValueCommit={(nextValue) => {
              setAmount(parseFileSheetNumberInput(nextValue, {
                fallback: amountPercent,
                min: 0,
                max: 100
              }) / 100);
            }}
          >
            <Slider
              className={precisionSliderClasses}
              value={[displayAmount]}
              min={0}
              max={1}
              step={0.01}
              onValueChange={(value) => setAmount(Array.isArray(value) ? value[0] : value)}
              aria-label="Explode amount"
            />
          </FileSheetSliderField>

          {canCustomize ? (
            <Field label="Layout">
              <SegmentedControl
                value={hasSteps ? "custom" : "automatic"}
                options={EXPLODE_MODE_OPTIONS}
                onChange={setMode}
              />
            </Field>
          ) : null}

          {hasSteps ? (
            <div className="flex flex-col gap-1 px-2">
              {exploded.steps.map((step, index) => {
                const isRotate = step.type === "rotate";
                const typeLabel = step.type === "rotate" ? "Rotate" : step.type === "radial" ? "Radial" : "Move";
                const unit = isRotate ? "°" : "mm";
                const magnitude = isRotate ? step.angleDeg : step.distance;
                const label = explodeStepLabel(step, nameMap);
                return (
                  <div key={step.id || index} className="flex items-center gap-1.5 text-[11px]">
                    <span className="w-4 shrink-0 text-right tabular-nums text-muted-foreground">{index + 1}</span>
                    <span className="min-w-0 flex-1 truncate" title={`${label} — ${typeLabel}`}>{label}</span>
                    <ExplodeStepMagnitudeInput
                      value={Number.isFinite(magnitude) ? magnitude : 0}
                      step={isRotate ? 5 : 1}
                      onCommit={(next) => updateStep(index, isRotate ? { angleDeg: next } : { distance: next })}
                      ariaLabel={`Step ${index + 1} ${isRotate ? "angle" : "distance"}`}
                      title={isRotate ? "Angle (degrees)" : "Distance (mm)"}
                    />
                    <span className="w-4 shrink-0 text-[10px] text-muted-foreground">{unit}</span>
                    <button
                      type="button"
                      className="p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                      disabled={index === 0}
                      onClick={() => moveStep(index, -1)}
                      aria-label={`Move step ${index + 1} earlier`}
                      title="Move earlier"
                    >
                      <ChevronUp className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                      disabled={index === exploded.steps.length - 1}
                      onClick={() => moveStep(index, 1)}
                      aria-label={`Move step ${index + 1} later`}
                      title="Move later"
                    >
                      <ChevronDown className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="p-0.5 text-muted-foreground hover:text-destructive"
                      onClick={() => removeStep(index)}
                      aria-label={`Delete step ${index + 1}`}
                      title="Delete step"
                    >
                      <Trash2 className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                    </button>
                  </div>
                );
              })}
            </div>
          ) : (
            <>
              <FileSheetSelectRow
                label="Direction"
                value={exploded.auto.mode}
                onValueChange={(nextValue) => setExplodedAuto({ mode: nextValue })}
                ariaLabel="Explode direction"
                options={EXPLODE_DIRECTION_OPTIONS}
              />
              <FileSheetToggleRow
                label="Reverse"
                checked={exploded.auto.direction === "negative"}
                onCheckedChange={(checked) => setExplodedAuto({ direction: checked ? "negative" : "positive" })}
              />
              <FileSheetSliderField
                label="Spread"
                value={`${exploded.auto.gapScale.toFixed(2)}×`}
                onValueCommit={(nextValue) => {
                  setExplodedAuto({
                    gapScale: parseFileSheetNumberInput(nextValue, {
                      fallback: exploded.auto.gapScale,
                      min: 0.25,
                      max: 4
                    })
                  });
                }}
              >
                <Slider
                  className={precisionSliderClasses}
                  value={[exploded.auto.gapScale]}
                  min={0.25}
                  max={4}
                  step={0.05}
                  onValueChange={(value) => setExplodedAuto({ gapScale: Array.isArray(value) ? value[0] : value })}
                  aria-label="Explode spread"
                />
              </FileSheetSliderField>
              <FileSheetSliderField
                label="Detail"
                value={`${exploded.auto.depth}`}
                onValueCommit={(nextValue) => {
                  setExplodedAuto({
                    depth: parseFileSheetNumberInput(nextValue, {
                      fallback: exploded.auto.depth,
                      min: 1,
                      max: 8,
                      integer: true
                    })
                  });
                }}
              >
                <Slider
                  className={precisionSliderClasses}
                  value={[exploded.auto.depth]}
                  min={1}
                  max={8}
                  step={1}
                  onValueChange={(value) => setExplodedAuto({ depth: Array.isArray(value) ? value[0] : value })}
                  aria-label="Explode detail"
                />
              </FileSheetSliderField>
            </>
          )}

          <Field label="Order">
            <SegmentedControl
              value={exploded.order}
              options={EXPLODE_ORDER_OPTIONS}
              onChange={(nextValue) => setExploded({ order: nextValue })}
            />
          </Field>
          <FileSheetToggleRow
            label="Explode lines"
            checked={exploded.trails}
            onCheckedChange={(checked) => setExploded({ trails: checked })}
          />

          <FileSheetButtonRow>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className={cn(compactButtonClasses, "justify-center")}
              onClick={() => setExploded({ ...DEFAULT_EXPLODED_VIEW_SETTINGS, amount: 0, enabled: false })}
              title="Reset the exploded view to defaults"
            >
              <RotateCcw className="h-3 w-3" strokeWidth={2} aria-hidden="true" />
              <span>Reset</span>
            </Button>
          </FileSheetButtonRow>
        </>
      ) : null}
    </ControlSubsection>
  );
}

// The single "Display" tab: how the model is drawn, then the two optional view
// transforms. Style always applies, so it sits at the top with no group header
// of its own; Section and Exploded are off most of the time, so each is a titled
// group whose switch gates its controls. The tab therefore rests at three rows
// and only grows for the transform actually in use.
export function DisplaySettingsSection({
  displaySettings,
  updateDisplaySettings,
  clipBounds = null,
  explodeMeshData = null
}) {
  const normalizedDisplaySettings = useMemo(
    () => normalizeDisplaySettings(displaySettings),
    [displaySettings]
  );
  const setDisplay = (patch) => {
    updateDisplaySettings?.((current) => ({
      ...normalizeDisplaySettings(current),
      ...patch
    }));
  };
  return (
    <div className="py-2" data-cad-display-settings-section="true">
      <ControlSubsection title="Model">
        <FileSheetSelectRow
          label="Mode"
          value={normalizedDisplaySettings.mode}
          onValueChange={(nextValue) => setDisplay({ mode: nextValue })}
          ariaLabel="Display mode"
          options={DISPLAY_MODE_OPTIONS}
        />
      </ControlSubsection>

      <ClipSubsection
        displaySettings={normalizedDisplaySettings}
        updateDisplaySettings={updateDisplaySettings}
        clipBounds={clipBounds}
      />
      <ExplodedSubsection
        displaySettings={normalizedDisplaySettings}
        updateDisplaySettings={updateDisplaySettings}
        explodeMeshData={explodeMeshData}
      />
    </div>
  );
}

// Build the "Display" tab descriptor: display mode plus the section-plane and
// exploded-view transforms, all of which are per-file state (unlike theme settings,
// which is persistent theme config and lives in the navbar editor).
export function buildDisplaySettingsTab(props) {
  return {
    id: FILE_SHEET_SECTION_IDS.THEME_DISPLAY,
    title: "Display",
    content: <DisplaySettingsSection {...props} />
  };
}

function ThemeSettingsContent({
  themeSettings,
  themeId = DEFAULT_THEME_ID,
  resolvedColorSchemeMode = THEME_COLOR_MODES.LIGHT,
  onSelectTheme,
  updateThemeSettings
}) {
  const [activePrimaryLight, setActivePrimaryLight] = useState("directional");
  // The Lights heading switch acts on whichever light the tab strip below it has
  // selected, so it needs that light's option record and current settings.
  const activePrimaryLightOption = PRIMARY_LIGHT_OPTIONS.find(
    (option) => option.value === activePrimaryLight
  ) || PRIMARY_LIGHT_OPTIONS[0];
  const activePrimaryLightSettings = themeSettings.lighting[activePrimaryLight] ||
    PRIMARY_LIGHT_FALLBACKS[activePrimaryLight] ||
    { enabled: false };

  const setMaterials = (patch) => {
    updateThemeSettings((current) => ({
      ...current,
      materials: {
        ...current.materials,
        ...patch
      }
    }));
  };

  const setBackground = (patch) => {
    updateThemeSettings((current) => ({
      ...current,
      background: {
        ...current.background,
        ...patch
      }
    }));
  };

  const setFloor = (patch) => {
    updateThemeSettings((current) => ({
      ...current,
      floor: {
        ...current.floor,
        ...patch
      }
    }));
  };
  const setFloorAxis = (patch) => {
    updateThemeSettings((current) => {
      const currentFloor = current.floor || {};
      return {
        ...current,
        floor: {
          ...currentFloor,
          axis: {
            ...(currentFloor.axis || {}),
            ...patch
          }
        }
      };
    });
  };

  const setFloorGrid = (patch) => {
    updateThemeSettings((current) => {
      const currentFloor = current.floor || {};
      return {
        ...current,
        floor: {
          ...currentFloor,
          grid: {
            ...(currentFloor.grid || {}),
            ...patch
          }
        }
      };
    });
  };

  const setEnvironment = (patch) => {
    updateThemeSettings((current) => ({
      ...current,
      environment: {
        ...current.environment,
        ...patch
      }
    }));
  };

  const setLighting = (patch) => {
    updateThemeSettings((current) => ({
      ...current,
      lighting: {
        ...current.lighting,
        ...patch
      }
    }));
  };

  const setThemeColor = (path, nextValue, mode = "") => {
    updateThemeSettings((current) => {
      const normalized = normalizeThemeSettings(current);
      const modeColors = cloneModeColors(normalized.modeColors);
      const next = {
        ...normalized,
        modeColors
      };
      if (mode === THEME_COLOR_MODES.LIGHT || mode === THEME_COLOR_MODES.DARK) {
        setPathValue(modeColors[mode], path, nextValue);
        return next;
      }
      const activeMode = activeThemeColorMode(normalized, resolvedColorSchemeMode);
      setPathValue(next, path, nextValue);
      setPathValue(modeColors[activeMode], path, nextValue);
      return next;
    });
  };
  const themeColorFieldProps = {
    themeSettings,
    resolvedColorSchemeMode,
    onChange: setThemeColor
  };

  const setLightConfig = (lightKey, patch) => {
    updateThemeSettings((current) => ({
      ...current,
      lighting: {
        ...current.lighting,
        [lightKey]: {
          ...(current.lighting[lightKey] || PRIMARY_LIGHT_FALLBACKS[lightKey]),
          ...patch
        }
      }
    }));
  };

  const setLightPosition = (lightKey, axis, nextValue) => {
    updateThemeSettings((current) => {
      const currentLight = current.lighting[lightKey] || PRIMARY_LIGHT_FALLBACKS[lightKey] || {};
      return {
        ...current,
        lighting: {
          ...current.lighting,
          [lightKey]: {
            ...currentLight,
            position: {
              ...currentLight.position,
              [axis]: nextValue
            }
          }
        }
      };
    });
  };

  return (
    <div className="py-2" data-cad-theme-settings-section="true">
      <ThemePresetSection
        themeSettings={themeSettings}
        themeId={themeId}
        onSelectTheme={onSelectTheme}
      />

      {/* Scene-wide output: how the camera projects and how bright the result
          is graded, as opposed to the per-material settings below. */}
      <ControlSubsection title="Render">
        <Field label="Projection">
          <SegmentedControl
            value={themeSettings.projection}
            onChange={(nextValue) => updateThemeSettings((current) => ({
              ...current,
              projection: nextValue
            }))}
            options={PROJECTION_MODE_OPTIONS}
          />
        </Field>
        <SliderField label="Tone mapping" value={formatNumber(themeSettings.lighting.toneMappingExposure)}>
          <SliderInput
            value={themeSettings.lighting.toneMappingExposure}
            min={0.05}
            max={6}
            step={0.01}
            onChange={(nextValue) => setLighting({ toneMappingExposure: nextValue })}
          />
        </SliderField>
      </ControlSubsection>

      <ControlSubsection title="Surface">
        {themeSettings.materials.cycleColors === true ? (
          <Field label="Colors" value={`${resolveFillColors(themeSettings.materials).length}/${MAX_THEME_FILL_COLORS}`}>
            <FillColorEditor
              colors={resolveFillColors(themeSettings.materials)}
              onChange={(nextColors) => setMaterials({
                defaultColor: nextColors[0],
                fillColors: nextColors
              })}
            />
          </Field>
        ) : (
          <ColorField
            label="Color"
            value={resolveFillColors(themeSettings.materials)[0]}
            onChange={(nextColor) => {
              const current = resolveFillColors(themeSettings.materials);
              setMaterials({
                defaultColor: nextColor,
                fillColors: [nextColor, ...current.slice(1)]
              });
            }}
          />
        )}

        <ThemeToggleRow
          label="Cycle colors"
          checked={themeSettings.materials.cycleColors === true}
          onChange={(nextValue) => setMaterials({ cycleColors: nextValue })}
        />

        <ThemeToggleRow
          label="Override colors"
          checked={themeSettings.materials.overrideSourceColors === true}
          onChange={(nextValue) => setMaterials({ overrideSourceColors: nextValue })}
        />

        <SliderField label="Roughness" value={formatNumber(themeSettings.materials.roughness)}>
          <SliderInput
            value={themeSettings.materials.roughness}
            min={0}
            max={1}
            step={0.01}
            onChange={(nextValue) => setMaterials({ roughness: nextValue })}
          />
        </SliderField>
        <SliderField label="Metalness" value={formatNumber(themeSettings.materials.metalness)}>
          <SliderInput
            value={themeSettings.materials.metalness}
            min={0}
            max={1}
            step={0.01}
            onChange={(nextValue) => setMaterials({ metalness: nextValue })}
          />
        </SliderField>
        <SliderField label="Clearcoat" value={formatNumber(themeSettings.materials.clearcoat)}>
          <SliderInput
            value={themeSettings.materials.clearcoat}
            min={0}
            max={1}
            step={0.01}
            onChange={(nextValue) => setMaterials({ clearcoat: nextValue })}
          />
        </SliderField>
        <SliderField label="Reflections" value={formatNumber(themeSettings.materials.envMapIntensity)}>
          <SliderInput
            value={themeSettings.materials.envMapIntensity}
            min={0}
            max={4}
            step={0.01}
            onChange={(nextValue) => setMaterials({ envMapIntensity: nextValue })}
          />
        </SliderField>
      </ControlSubsection>

      {/* Post-adjustments to whatever colour the surface resolved to, as opposed
          to the material's own physical properties above. */}
      <ControlSubsection title="Color grading">
        <SliderField label="Saturation" value={formatNumber(themeSettings.materials.saturation)}>
          <SliderInput
            value={themeSettings.materials.saturation}
            min={0}
            max={2.5}
            step={0.01}
            onChange={(nextValue) => setMaterials({ saturation: nextValue })}
          />
        </SliderField>
        <SliderField label="Contrast" value={formatNumber(themeSettings.materials.contrast)}>
          <SliderInput
            value={themeSettings.materials.contrast}
            min={0}
            max={2.5}
            step={0.01}
            onChange={(nextValue) => setMaterials({ contrast: nextValue })}
          />
        </SliderField>
        <SliderField label="Brightness" value={formatNumber(themeSettings.materials.brightness)}>
          <SliderInput
            value={themeSettings.materials.brightness}
            min={0}
            max={2}
            step={0.01}
            onChange={(nextValue) => setMaterials({ brightness: nextValue })}
          />
        </SliderField>
      </ControlSubsection>

      <ControlSubsection title="Backdrop">
        <Field label="Type">
          <SegmentedControl
            value={themeSettings.background.type}
            onChange={(nextValue) => setBackground({ type: nextValue })}
            options={BACKGROUND_MODE_OPTIONS}
          />
        </Field>

        {themeSettings.background.type === "solid" ? (
          <ColorModeField
            label="Color"
            path={["background", "solidColor"]}
            {...themeColorFieldProps}
          />
        ) : null}

        {themeSettings.background.type === "linear" ? (
          <>
            <ColorModeField
              label="Start color"
              path={["background", "linearStart"]}
              {...themeColorFieldProps}
            />
            <ColorModeField
              label="End color"
              path={["background", "linearEnd"]}
              {...themeColorFieldProps}
            />
            <SliderField label="Angle" value={`${formatNumber(themeSettings.background.linearAngle, 0)}°`}>
              <SliderInput
                value={themeSettings.background.linearAngle}
                min={-360}
                max={360}
                step={1}
                onChange={(nextValue) => setBackground({ linearAngle: nextValue })}
              />
            </SliderField>
          </>
        ) : null}

        {themeSettings.background.type === "radial" ? (
          <>
            <ColorModeField
              label="Inner color"
              path={["background", "radialInner"]}
              {...themeColorFieldProps}
            />
            <ColorModeField
              label="Outer color"
              path={["background", "radialOuter"]}
              {...themeColorFieldProps}
            />
          </>
        ) : null}
      </ControlSubsection>

      <ControlSubsection
        title="Floor"
        toggle={(
          <SubsectionToggle
            label="Enable floor"
            checked={themeSettings.floor?.enabled === true}
            onCheckedChange={(nextValue) => setFloor({ enabled: nextValue })}
          />
        )}
      >
        {themeSettings.floor?.enabled === true ? (
          <>
            <ThemeToggleRow
              label="Follow model"
              checked={themeSettings.floor?.followModel !== false}
              onChange={(nextValue) => setFloor({ followModel: nextValue })}
            />
            <ColorModeField
              label="Color"
              path={["floor", "color"]}
              {...themeColorFieldProps}
            />
            <SliderField label="Roughness" value={formatNumber(themeSettings.floor?.roughness ?? 0.72)}>
              <SliderInput
                value={themeSettings.floor?.roughness ?? 0.72}
                min={0}
                max={1}
                step={0.01}
                onChange={(nextValue) => setFloor({ roughness: nextValue })}
              />
            </SliderField>
            <SliderField label="Reflectivity" value={formatNumber(themeSettings.floor?.reflectivity ?? 0.12)}>
              <SliderInput
                value={themeSettings.floor?.reflectivity ?? 0.12}
                min={0}
                max={1}
                step={0.01}
                onChange={(nextValue) => setFloor({ reflectivity: nextValue })}
              />
            </SliderField>
            <SliderField label="Shadow" value={formatNumber(themeSettings.floor?.shadowOpacity ?? 0.45)}>
              <SliderInput
                value={themeSettings.floor?.shadowOpacity ?? 0.45}
                min={0}
                max={1}
                step={0.01}
                onChange={(nextValue) => setFloor({ shadowOpacity: nextValue })}
              />
            </SliderField>
            <SliderField label="Backdrop blend" value={formatNumber(themeSettings.floor?.horizonBlend ?? 0)}>
              <SliderInput
                value={themeSettings.floor?.horizonBlend ?? 0}
                min={0}
                max={1}
                step={0.01}
                onChange={(nextValue) => setFloor({ horizonBlend: nextValue })}
              />
            </SliderField>
          </>
        ) : null}
      </ControlSubsection>

      <ControlSubsection
        title="Grid"
        toggle={(
          <SubsectionToggle
            label="Enable grid"
            checked={themeSettings.floor?.grid?.enabled === true}
            onCheckedChange={(nextValue) => setFloorGrid({ enabled: nextValue })}
          />
        )}
      >
        {themeSettings.floor?.grid?.enabled === true ? (
          <>
            {/* No floor colour here: it is the same ["floor","color"] the Floor
                section owns, and the grid only falls back to it when the line
                colours below are unset (see renderOptions applyFloor). Two
                controls writing one value silently moved each other. */}
            <ColorModeField
              label="Center line"
              path={["floor", "grid", "centerColor"]}
              {...themeColorFieldProps}
            />
            <ColorModeField
              label="Cell line"
              path={["floor", "grid", "cellColor"]}
              {...themeColorFieldProps}
            />
            <SliderField label="Line opacity" value={formatNumber(themeSettings.floor?.grid?.opacity ?? 0.18)}>
              <SliderInput
                value={themeSettings.floor?.grid?.opacity ?? 0.18}
                min={0}
                max={1}
                step={0.01}
                onChange={(nextValue) => setFloorGrid({ opacity: nextValue })}
              />
            </SliderField>
            <SliderField label="Density" value={formatNumber(themeSettings.floor?.grid?.density ?? 1)}>
              <SliderInput
                value={themeSettings.floor?.grid?.density ?? 1}
                min={0.25}
                max={4}
                step={0.05}
                onChange={(nextValue) => setFloorGrid({ density: nextValue })}
              />
            </SliderField>
          </>
        ) : null}

        {/* The origin axis lives here because it is the other ground reference,
            but it is deliberately outside the grid-enabled branch above: gating
            it on the grid would hide its switch whenever the grid is off and
            leave a rendering axis with no way to turn it off. */}
        <ThemeToggleRow
          label="Origin axis"
          checked={themeSettings.floor?.axis?.enabled === true}
          onChange={(nextValue) => setFloorAxis({ enabled: nextValue })}
        />
        {themeSettings.floor?.axis?.enabled === true ? (
          <>
            <ColorModeField
              label="Axis color"
              path={["floor", "axis", "color"]}
              {...themeColorFieldProps}
            />
            <SliderField label="Axis opacity" value={formatNumber(themeSettings.floor?.axis?.opacity ?? 0.5)}>
              <SliderInput
                value={themeSettings.floor?.axis?.opacity ?? 0.5}
                min={0}
                max={1}
                step={0.01}
                onChange={(nextValue) => setFloorAxis({ opacity: nextValue })}
              />
            </SliderField>
          </>
        ) : null}
      </ControlSubsection>


      {/* Lighting used to be one section holding all of this — 18 rows and every
          nested sub-subsection in the file, four levels deep at the light tabs.
          It is now four flat siblings, each with its enable switch in the header
          like Floor and Grid, so a light you are not using costs one row. */}
      <ControlSubsection
        title="Environment"
        toggle={(
          <SubsectionToggle
            label="Enable environment light"
            checked={themeSettings.environment.enabled}
            onCheckedChange={(nextValue) => setEnvironment({ enabled: nextValue })}
          />
        )}
      >
        {themeSettings.environment.enabled ? (
          <>
            <FileSheetSelectRow
              label="Map"
              value={themeSettings.environment.presetId}
              onValueChange={(nextValue) => setEnvironment({ presetId: nextValue })}
              ariaLabel="Environment map"
              options={ENVIRONMENT_PRESETS.map((option) => ({
                value: option.id,
                label: option.label
              }))}
            />
            <SliderField label="Intensity" value={formatNumber(themeSettings.environment.intensity)}>
              <SliderInput
                value={themeSettings.environment.intensity}
                min={0}
                max={4}
                step={0.01}
                onChange={(nextValue) => setEnvironment({ intensity: nextValue })}
              />
            </SliderField>
            {/* Stored in radians; shown and typed in degrees like every other
                angle, so the commit path converts explicitly. */}
            <SliderField
              label="Rotation"
              value={`${formatNumber((themeSettings.environment.rotationY * 180) / Math.PI, 0)}°`}
              onValueCommit={(nextValue) => {
                const nextDegrees = parseFileSheetNumberInput(nextValue, {
                  fallback: (themeSettings.environment.rotationY * 180) / Math.PI,
                  min: -180,
                  max: 180
                });
                setEnvironment({ rotationY: (nextDegrees * Math.PI) / 180 });
              }}
            >
              <SliderInput
                value={themeSettings.environment.rotationY}
                min={-Math.PI}
                max={Math.PI}
                step={0.01}
                onChange={(nextValue) => setEnvironment({ rotationY: nextValue })}
              />
            </SliderField>
            <ThemeToggleRow
              label="Use as backdrop"
              checked={themeSettings.environment.useAsBackground}
              onChange={(nextValue) => setEnvironment({ useAsBackground: nextValue })}
            />
          </>
        ) : null}
      </ControlSubsection>

      <ControlSubsection
        title="Lights"
        toggle={(
          <SubsectionToggle
            label={`Enable ${activePrimaryLightOption.label.toLowerCase()} light`}
            checked={activePrimaryLightSettings.enabled}
            onCheckedChange={(nextValue) => setLightConfig(activePrimaryLight, { enabled: nextValue })}
          />
        )}
      >
        <Tabs value={activePrimaryLight} onValueChange={setActivePrimaryLight} className="gap-0">
            <FileSheetControlRow label="Light">
              <TabsList className={cn("grid grid-cols-5", FILE_SHEET_SEGMENTED_TABS_LIST_CLASSES)}>
                {PRIMARY_LIGHT_OPTIONS.map((option) => (
                  <TabsTrigger
                    key={option.value}
                    value={option.value}
                    className={FILE_SHEET_SEGMENTED_TAB_TRIGGER_CLASSES}
                  >
                    {option.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </FileSheetControlRow>

            {PRIMARY_LIGHT_OPTIONS.map((option) => {
              const light = themeSettings.lighting[option.value] || PRIMARY_LIGHT_FALLBACKS[option.value];
              const supportsDistance = option.value === "spot" || option.value === "point";
              const supportsModeColors = MODE_COLOR_LIGHT_KEYS.includes(option.value);
              return (
                <TabsContent
                  key={option.value}
                  value={option.value}
                  className={cn("mt-3", FILE_SHEET_ROW_STACK_CLASSES)}
                  data-file-sheet-row-stack=""
                >
                  {light.enabled ? (
                    <>
                      {supportsModeColors ? (
                      <ColorModeField
                        label="Color"
                        path={["lighting", option.value, "color"]}
                        {...themeColorFieldProps}
                      />
                    ) : (
                      <ColorField
                        label="Color"
                        value={light.color}
                        onChange={(nextValue) => setLightConfig(option.value, { color: nextValue })}
                      />
                    )}
                    <SliderField label="Intensity" value={formatNumber(light.intensity)}>
                      <SliderInput
                        value={light.intensity}
                        min={0}
                        max={20}
                        step={0.01}
                        onChange={(nextValue) => setLightConfig(option.value, { intensity: nextValue })}
                      />
                    </SliderField>
                    {option.value === "spot" ? (
                      // Stored in radians; shown and typed in degrees.
                      <SliderField
                        label="Angle"
                        value={`${formatNumber((light.angle * 180) / Math.PI, 0)}°`}
                        onValueCommit={(nextValue) => {
                          const nextDegrees = parseFileSheetNumberInput(nextValue, {
                            fallback: (light.angle * 180) / Math.PI
                          });
                          setLightConfig(option.value, {
                            angle: clamp((nextDegrees * Math.PI) / 180, 0.01, 1.57)
                          });
                        }}
                      >
                        <SliderInput
                          value={light.angle}
                          min={0.01}
                          max={1.57}
                          step={0.01}
                          onChange={(nextValue) => setLightConfig(option.value, { angle: nextValue })}
                        />
                      </SliderField>
                    ) : null}
                    {supportsDistance ? (
                      <SliderField label="Distance" value={formatNumber(light.distance, 0)}>
                        <SliderInput
                          value={light.distance}
                          min={0}
                          max={5000}
                          step={1}
                          onChange={(nextValue) => setLightConfig(option.value, { distance: nextValue })}
                        />
                      </SliderField>
                    ) : null}
                    <Field label="Position (X/Z)">
                      <PositionPad
                        value={light.position}
                        onChange={(axis, nextValue) => setLightPosition(option.value, axis, nextValue)}
                      />
                    </Field>
                    <SliderField label="Height (Y)" value={formatNumber(light.position.y, 0)}>
                      <SliderInput
                        value={light.position.y}
                        min={-5000}
                        max={5000}
                        step={1}
                        onChange={(nextValue) => setLightPosition(option.value, "y", nextValue)}
                      />
                    </SliderField>
                    </>
                  ) : null}
                </TabsContent>
              );
            })}
        </Tabs>
      </ControlSubsection>

      <ControlSubsection
        title="Ambient"
        toggle={(
          <SubsectionToggle
            label="Enable ambient light"
            checked={themeSettings.lighting.ambient.enabled}
            onCheckedChange={(nextValue) => setLightConfig("ambient", { enabled: nextValue })}
          />
        )}
      >
        {themeSettings.lighting.ambient.enabled ? (
          <>
            <ColorModeField
              label="Color"
              path={["lighting", "ambient", "color"]}
              {...themeColorFieldProps}
            />
            <SliderField label="Intensity" value={formatNumber(themeSettings.lighting.ambient.intensity)}>
              <SliderInput
                value={themeSettings.lighting.ambient.intensity}
                min={0}
                max={20}
                step={0.01}
                onChange={(nextValue) => setLightConfig("ambient", { intensity: nextValue })}
              />
            </SliderField>
          </>
        ) : null}
      </ControlSubsection>

      <ControlSubsection
        title="Hemisphere"
        toggle={(
          <SubsectionToggle
            label="Enable hemisphere light"
            checked={themeSettings.lighting.hemisphere.enabled}
            onCheckedChange={(nextValue) => setLightConfig("hemisphere", { enabled: nextValue })}
          />
        )}
      >
        {themeSettings.lighting.hemisphere.enabled ? (
          <>
            <ColorModeField
              label="Sky color"
              path={["lighting", "hemisphere", "skyColor"]}
              {...themeColorFieldProps}
            />
            <ColorModeField
              label="Ground color"
              path={["lighting", "hemisphere", "groundColor"]}
              {...themeColorFieldProps}
            />
            <SliderField label="Intensity" value={formatNumber(themeSettings.lighting.hemisphere.intensity)}>
              <SliderInput
                value={themeSettings.lighting.hemisphere.intensity}
                min={0}
                max={20}
                step={0.01}
                onChange={(nextValue) => setLightConfig("hemisphere", { intensity: nextValue })}
              />
            </SliderField>
          </>
        ) : null}
      </ControlSubsection>
    </div>
  );
}

// Full-sidebar theme editor (global theme settings). Mutually exclusive with the
// per-file sheet; opened from the navbar theme button. Reuses the FileSheet
// aside frame (width + resize) and the ThemeSettingsContent editor body, which
// already holds the preset select.
export function ThemeEditorPanel({
  open,
  isDesktop,
  width,
  onClose,
  onStartResize,
  themeSettings,
  themeId = DEFAULT_THEME_ID,
  resolvedColorSchemeMode = THEME_COLOR_MODES.LIGHT,
  onSelectTheme,
  updateThemeSettings
}) {
  return (
    <FileSheet
      open={open}
      title="Theme"
      isDesktop={isDesktop}
      width={width}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) {
          onClose?.();
        }
      }}
      onStartResize={onStartResize}
      scrollBody={false}
    >
      <div className="flex h-8 shrink-0 items-center justify-between gap-2 border-b border-sidebar-border/70 px-2">
        <span className="text-[11px] font-medium text-sidebar-foreground">Theme</span>
        <button
          type="button"
          onClick={() => onClose?.()}
          aria-label="Close theme editor"
          title="Close"
          className="flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <X className="size-3.5" strokeWidth={2} aria-hidden="true" />
        </button>
      </div>
      <ScrollArea className="min-h-0 flex-1" viewportClassName="h-full">
        <ThemeSettingsContent
          themeSettings={themeSettings}
          themeId={themeId}
          resolvedColorSchemeMode={resolvedColorSchemeMode}
          onSelectTheme={onSelectTheme}
          updateThemeSettings={updateThemeSettings}
        />
      </ScrollArea>
    </FileSheet>
  );
}
