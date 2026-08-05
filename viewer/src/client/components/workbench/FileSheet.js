import { Children, useEffect, useRef, useState } from "react";
import { cn } from "@/ui/utils";
import {
  AccordionContent,
  AccordionItem,
  AccordionTrigger
} from "../ui/accordion";
import { ColorPicker } from "../ui/color-picker";
import { ScrollArea } from "../ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from "../ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle
} from "../ui/sheet";
import { Switch } from "../ui/switch";
import { ToggleGroup, ToggleGroupItem } from "../ui/toggle-group";

const DEFAULT_FILE_SHEET_WIDTH = 365;
const DESKTOP_FILE_SHEET_MIN_WIDTH = 240;
const DESKTOP_FILE_SHEET_MAX_WIDTH = "min(28rem, calc(100vw - 0.75rem))";
const MOBILE_FILE_SHEET_WIDTH = "min(24rem, calc(100vw - 0.75rem))";
const FILE_SHEET_CONTROL_TEXT_CLASSES = [
  "[&_[data-slot=input]]:!text-[11px]",
  "[&_[data-slot=select-trigger]]:!text-[11px]",
  "[&_[data-slot=color-picker-trigger]]:!text-[11px]"
].join(" ");

export const FILE_SHEET_SECTION_TRIGGER_CLASSES = "px-2 py-2 text-sm font-normal text-sidebar-foreground/90";
export const FILE_SHEET_SECTION_CONTENT_CLASSES = "py-2";
export const FILE_SHEET_CONTROL_ROW_CLASSES = "space-y-1 px-2";
export const FILE_SHEET_ROW_STACK_CLASSES = "space-y-2";
export const FILE_SHEET_SECTION_BODY_CLASSES = `${FILE_SHEET_ROW_STACK_CLASSES} py-2`;
export const FILE_SHEET_SLIDER_FIELD_CLASSES = "space-y-1 px-2";
export const FILE_SHEET_INLINE_CONTROL_ROW_CLASSES = "px-2";
// Section headers are uppercase micro-labels so they can never be confused
// with row labels (11px sentence-case, one step below).
export const FILE_SHEET_SECTION_TITLE_CLASSES = "text-[10px] font-medium uppercase tracking-wider text-muted-foreground";
export const FILE_SHEET_FIELD_LABEL_CLASSES = "block min-w-0 truncate text-[11px] font-medium leading-4 text-muted-foreground";
export const FILE_SHEET_STATUS_TEXT_CLASSES = "px-2 text-[11px] leading-4 text-muted-foreground";
export const FILE_SHEET_UNIT_SUFFIX_CLASSES = "pointer-events-none absolute inset-y-0 right-2 flex items-center text-[10px] text-muted-foreground";
export const FILE_SHEET_SELECT_TRIGGER_CLASSES = "h-7 w-full !text-[11px]";
export const FILE_SHEET_VALUE_BADGE_CLASSES = "shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] font-medium leading-none tabular-nums text-muted-foreground";
export const FILE_SHEET_VALUE_BADGE_INPUT_CLASSES = [
  "h-7 w-20 shrink-0 rounded-md border border-input bg-transparent px-2 py-1 text-right text-[11px] font-medium leading-none tabular-nums text-foreground shadow-xs outline-none",
  "m-0 box-border min-w-0 appearance-none transition-[color,box-shadow,border-color] placeholder:text-muted-foreground",
  "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30"
].join(" ");
export const FILE_SHEET_COMPACT_BUTTON_CLASSES = "h-7 px-2 text-[11px] text-muted-foreground hover:text-foreground";
export const FILE_SHEET_COMPACT_ICON_BUTTON_CLASSES = "size-7 text-muted-foreground hover:text-foreground";
export const FILE_SHEET_COMPACT_INPUT_CLASSES = "!h-7 px-2 text-[11px] !text-foreground";
export const FILE_SHEET_COMPACT_NUMERIC_INPUT_CLASSES = "h-7 px-2 !text-[11px] font-medium leading-none tabular-nums text-foreground";
export const FILE_SHEET_COMPACT_JOINT_INPUT_CLASSES = "h-7 px-2 !text-[11px] font-medium leading-none tabular-nums text-foreground";
export const FILE_SHEET_BOOLEAN_SWITCH_CLASSES = [
  "h-4 w-7 border shadow-none",
  "data-[state=checked]:border-primary/75 data-[state=checked]:bg-primary",
  "data-[state=unchecked]:!border-[rgb(115_125_140_/_0.52)] data-[state=unchecked]:!bg-[rgb(115_125_140_/_0.22)]",
  "dark:data-[state=unchecked]:!border-[rgb(148_163_184_/_0.44)] dark:data-[state=unchecked]:!bg-[rgb(148_163_184_/_0.18)]"
].join(" ");
export const FILE_SHEET_BOOLEAN_SWITCH_THUMB_CLASSES = [
  "!size-3 shadow-sm",
  "data-[state=checked]:!translate-x-3 data-[state=checked]:!bg-background",
  "data-[state=unchecked]:!translate-x-0 data-[state=unchecked]:!bg-[rgb(115_125_140)]",
  "dark:data-[state=unchecked]:!bg-[rgb(148_163_184)]"
].join(" ");
export const FILE_SHEET_SEGMENTED_ITEM_CLASSES = [
  "text-muted-foreground hover:text-foreground",
  "data-[state=on]:!bg-accent data-[state=on]:!text-foreground data-[state=on]:font-semibold",
  "data-[state=on]:ring-1 data-[state=on]:ring-inset data-[state=on]:ring-border/80",
  "data-[state=on]:hover:!bg-accent data-[state=on]:hover:!text-foreground"
].join(" ");
export const FILE_SHEET_PRECISION_SLIDER_CLASSES = [
  "h-4",
  "[&_[data-slot=slider-track]]:h-px",
  "[&_[data-slot=slider-track]]:rounded-none",
  "[&_[data-slot=slider-track]]:bg-border",
  "[&_[data-slot=slider-range]]:bg-primary",
  "[&_[data-slot=slider-thumb]]:h-2.5",
  "[&_[data-slot=slider-thumb]]:w-1.5",
  "[&_[data-slot=slider-thumb]]:rounded-[1px]",
  "[&_[data-slot=slider-thumb]]:border-primary",
  "[&_[data-slot=slider-thumb]]:bg-background",
  "[&_[data-slot=slider-thumb]]:shadow-none",
  "[&_[data-slot=slider-thumb]]:ring-0",
  "[&_[data-slot=slider-thumb]]:hover:border-primary/85",
  "[&_[data-slot=slider-thumb]]:hover:ring-1",
  "[&_[data-slot=slider-thumb]]:hover:ring-ring/25",
  "[&_[data-slot=slider-thumb]]:focus-visible:ring-2",
  "[&_[data-slot=slider-thumb]]:focus-visible:ring-ring/30"
].join(" ");

export function FileSheetSection({
  value,
  title,
  children,
  className,
  contentClassName,
  triggerClassName,
  triggerProps,
  ...props
}) {
  return (
    <AccordionItem value={value} className={cn("border-border", className)} {...props}>
      <AccordionTrigger
        className={cn(FILE_SHEET_SECTION_TRIGGER_CLASSES, triggerClassName)}
        {...triggerProps}
      >
        {title}
      </AccordionTrigger>
      <AccordionContent className={cn(FILE_SHEET_SECTION_CONTENT_CLASSES, contentClassName)}>
        {children}
      </AccordionContent>
    </AccordionItem>
  );
}

export function FileSheetSubsection({
  title,
  trailing = null,
  children,
  className,
  contentClassName,
  hideFirstSeparator = true
}) {
  return (
    <div
      className={cn(
        "py-2.5",
        hideFirstSeparator && "first:pt-2 first:[&_.cad-sheet-subsection-separator]:hidden",
        className
      )}
    >
      <div className="cad-sheet-subsection-separator mx-2 mb-2 h-px bg-border/60" />
      {/* Titleless subsections are a rule plus rows: for a couple of settings
          that belong to the sheet as a whole rather than to any named group, and
          would otherwise need a heading invented for them. */}
      {title ? (
        <div className="flex min-h-5 min-w-0 items-center justify-between gap-2 px-2 pb-1">
          <span className={cn("min-w-0 truncate leading-4", FILE_SHEET_SECTION_TITLE_CLASSES)}>{title}</span>
          {/* Trailing holds the section's control — most often its gate switch,
              kept on the shared right-edge control axis like every other row. */}
          {trailing ? <span className="flex shrink-0 items-center">{trailing}</span> : null}
        </div>
      ) : null}
      <div
        className={cn(FILE_SHEET_ROW_STACK_CLASSES, contentClassName)}
        data-file-sheet-row-stack=""
      >
        {children}
      </div>
    </div>
  );
}

export function FileSheetSectionBody({
  children,
  className
}) {
  return (
    <div
      className={cn(FILE_SHEET_SECTION_BODY_CLASSES, className)}
      data-file-sheet-section-body=""
      data-file-sheet-row-stack=""
    >
      {children}
    </div>
  );
}

export function FileSheetControlRow({
  label,
  value,
  trailing,
  children,
  className,
  contentClassName,
  labelClassName,
  rowKind = "control"
}) {
  return (
    <div
      className={cn(FILE_SHEET_CONTROL_ROW_CLASSES, className)}
      data-file-sheet-control-row=""
      data-file-sheet-row-kind={rowKind}
    >
      {label != null || value != null || trailing != null ? (
        <div className="flex min-h-4 items-center justify-between gap-2">
          {label != null ? (
            <span className={cn(FILE_SHEET_FIELD_LABEL_CLASSES, labelClassName)}>{label}</span>
          ) : <span />}
          {trailing != null ? trailing : value != null ? (
            <span className={FILE_SHEET_VALUE_BADGE_CLASSES}>{value}</span>
          ) : null}
        </div>
      ) : null}
      <div className={cn("min-w-0", contentClassName)}>{children}</div>
    </div>
  );
}

export function parseFileSheetNumberInput(value, {
  fallback = 0,
  min = -Infinity,
  max = Infinity,
  integer = false
} = {}) {
  const text = String(value ?? "").trim();
  const match = text.match(/[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?/i);
  const fallbackValue = Number.isFinite(Number(fallback)) ? Number(fallback) : 0;
  const numericValue = match ? Number(match[0]) : NaN;
  const lowerBound = Number.isFinite(Number(min)) ? Number(min) : -Infinity;
  const upperBound = Number.isFinite(Number(max)) ? Number(max) : Infinity;
  const resolvedValue = Number.isFinite(numericValue) ? numericValue : fallbackValue;
  const clampedValue = Math.min(Math.max(resolvedValue, lowerBound), Math.max(lowerBound, upperBound));
  return integer ? Math.round(clampedValue) : clampedValue;
}

export function FileSheetValueInput({
  value,
  onValueCommit,
  disabled = false,
  ariaLabel,
  inputMode = "decimal",
  className,
  style
}) {
  const inputRef = useRef(null);
  const displayValue = String(value ?? "");
  const [draftValue, setDraftValue] = useState(displayValue);
  const [editing, setEditing] = useState(false);
  const skipCommitRef = useRef(false);
  const selectOnEditRef = useRef(false);
  const visibleValue = editing ? draftValue : displayValue;

  useEffect(() => {
    if (!editing) {
      setDraftValue(displayValue);
    }
  }, [displayValue, editing]);

  useEffect(() => {
    if (!editing || !selectOnEditRef.current) {
      return;
    }
    selectOnEditRef.current = false;
    inputRef.current?.select?.();
  }, [editing, visibleValue]);

  const selectInputValue = (input) => {
    input?.select?.();
    if (typeof window !== "undefined") {
      window.requestAnimationFrame?.(() => input?.select?.());
    }
  };

  return (
    <input
      ref={inputRef}
      type="text"
      inputMode={inputMode}
      value={visibleValue}
      disabled={disabled}
      data-editing={editing ? "true" : "false"}
      onChange={(event) => {
        setDraftValue(event.target.value);
      }}
      onFocus={(event) => {
        selectOnEditRef.current = true;
        setEditing(true);
        selectInputValue(event.currentTarget);
      }}
      onClick={(event) => {
        selectOnEditRef.current = true;
        setEditing(true);
        selectInputValue(event.currentTarget);
      }}
      onMouseUp={(event) => {
        event.preventDefault();
      }}
      onBlur={() => {
        setEditing(false);
        if (skipCommitRef.current) {
          skipCommitRef.current = false;
          return;
        }
        onValueCommit?.(draftValue);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.currentTarget.blur();
        }
        if (event.key === "Escape") {
          skipCommitRef.current = true;
          setDraftValue(displayValue);
          event.currentTarget.blur();
        }
      }}
      className={cn(
        FILE_SHEET_VALUE_BADGE_INPUT_CLASSES,
        className
      )}
      style={{
        borderColor: editing ? "var(--ring)" : undefined,
        ...style
      }}
      aria-label={ariaLabel}
    />
  );
}

export function FileSheetSliderField({
  label,
  value,
  trailing,
  onValueCommit,
  valueInputProps,
  children,
  className,
  contentClassName,
  labelClassName
}) {
  const valueTrailing = trailing ?? (onValueCommit ? (
    <FileSheetValueInput
      value={value}
      onValueCommit={onValueCommit}
      {...valueInputProps}
    />
  ) : null);

  return (
    <FileSheetControlRow
      label={valueTrailing ? null : label}
      value={null}
      className={cn(FILE_SHEET_SLIDER_FIELD_CLASSES, className)}
      contentClassName={cn(valueTrailing ? "space-y-0" : "space-y-1", contentClassName)}
      labelClassName={labelClassName}
      rowKind="slider"
    >
      {valueTrailing ? (
        <div
          className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2"
          data-file-sheet-slider-input-row=""
        >
          <div className="min-w-0 pr-3">
            {label != null ? (
              <span
                className={cn(
                  FILE_SHEET_FIELD_LABEL_CLASSES,
                  "block h-3 leading-3",
                  labelClassName
                )}
              >
                {label}
              </span>
            ) : null}
            <div className={cn("min-w-0", label != null && "-mt-0.5")}>
              {children}
            </div>
          </div>
          {valueTrailing}
        </div>
      ) : children}
    </FileSheetControlRow>
  );
}

export function FileSheetInlineControlRow({
  label,
  description,
  children,
  className,
  labelClassName
}) {
  return (
    <div
      className={cn(FILE_SHEET_INLINE_CONTROL_ROW_CLASSES, className)}
      data-file-sheet-control-row=""
      data-file-sheet-row-kind="inline"
    >
      <div className="flex min-h-7 max-w-full items-center justify-between gap-2">
        <span className={cn(FILE_SHEET_FIELD_LABEL_CLASSES, labelClassName)}>{label}</span>
        <span className="shrink-0">{children}</span>
      </div>
      {description ? (
        <p className="mt-0.5 max-w-[28rem] text-[11px] leading-4 text-muted-foreground">{description}</p>
      ) : null}
    </div>
  );
}

export function FileSheetBooleanToggle({
  checked,
  onCheckedChange,
  disabled = false,
  ariaLabel,
  className
}) {
  return (
    <Switch
      checked={checked}
      disabled={disabled}
      onCheckedChange={onCheckedChange}
      className={cn(FILE_SHEET_BOOLEAN_SWITCH_CLASSES, className)}
      thumbClassName={FILE_SHEET_BOOLEAN_SWITCH_THUMB_CLASSES}
      aria-label={ariaLabel}
    />
  );
}

export function FileSheetToggleRow({
  label,
  checked,
  onCheckedChange,
  disabled = false,
  description,
  className,
  labelClassName,
  ariaLabel
}) {
  return (
    <FileSheetInlineControlRow
      label={label}
      description={description}
      className={className}
      labelClassName={labelClassName}
    >
      <FileSheetBooleanToggle
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        ariaLabel={ariaLabel || label}
      />
    </FileSheetInlineControlRow>
  );
}

// Loading / empty / info line, or an error with tone="error". The one style
// for every in-sheet message.
export function FileSheetStatusText({ children, tone = "muted", className }) {
  return (
    <p
      className={cn(
        FILE_SHEET_STATUS_TEXT_CLASSES,
        tone === "error" && "whitespace-pre-line text-destructive",
        className
      )}
    >
      {children}
    </p>
  );
}

// Micro-labelled cell for a FileSheetFieldGrid: 11px label above a 28px control.
// The one sanctioned label-above arrangement — reserved for tightly coupled
// tuples (coordinates, solver numerics); independent settings use rows.
export function FileSheetField({ label, children, className }) {
  return (
    <label className={cn("block min-w-0", className)}>
      <span className={FILE_SHEET_FIELD_LABEL_CLASSES}>{label}</span>
      <div className="mt-1 min-w-0">{children}</div>
    </label>
  );
}

// Read-only fact in a field grid: same silhouette as an input, muted fill so it
// reads as data, not an editable control.
export function FileSheetValueField({ label, value, mono = false }) {
  const displayValue = String(value ?? "");
  return (
    <div className="block min-w-0">
      <span className={FILE_SHEET_FIELD_LABEL_CLASSES}>{label}</span>
      <div
        className={cn(
          "mt-1 min-h-7 truncate rounded-md border border-border/70 bg-muted/25 px-2 py-1 text-[11px] font-medium leading-4 text-foreground",
          mono && "font-mono tabular-nums"
        )}
        title={displayValue}
      >
        {displayValue}
      </div>
    </div>
  );
}

export function FileSheetFieldGrid({ columns = 2, children, className }) {
  return (
    <div
      className={cn("grid gap-2 px-2", className)}
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
      data-file-sheet-field-grid=""
    >
      {children}
    </div>
  );
}

// Sibling actions as equal-width columns; a single child renders full width
// (the Reset convention: outline + RotateCcw, last row of what it resets).
export function FileSheetButtonRow({ children, columns, className }) {
  const columnCount = Math.max(1, columns || Children.count(children));
  return (
    <div
      className={cn("grid gap-1.5 px-2", className)}
      style={{ gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))` }}
      data-file-sheet-button-row=""
    >
      {children}
    </div>
  );
}

// Mutually exclusive modes with 2-5 short options; 6+ options use a select.
export function FileSheetSegmentedControl({ value, onChange, options, ariaLabel }) {
  const columnCount = Math.max(1, Math.min(options.length, options.length > 4 ? 3 : 4));
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      value={value}
      onValueChange={(nextValue) => {
        if (!nextValue) {
          return;
        }
        onChange(nextValue);
      }}
      className="grid min-h-7 w-full min-w-0 auto-rows-[1.75rem]"
      style={{ gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))` }}
      aria-label={ariaLabel}
    >
      {options.map((option) => {
        const Icon = option.Icon;
        return (
          <ToggleGroupItem
            key={option.value}
            value={option.value}
            disabled={option.disabled === true}
            className={cn("min-w-0 gap-1.5 !h-7 px-1.5 text-[11px]", FILE_SHEET_SEGMENTED_ITEM_CLASSES)}
            title={option.title || option.label}
            aria-label={option.label}
          >
            {Icon ? <Icon className="size-3" strokeWidth={2} aria-hidden="true" /> : null}
            <span className="truncate">{option.label}</span>
          </ToggleGroupItem>
        );
      })}
    </ToggleGroup>
  );
}

// The standard select: block row with a full-width 28px trigger. Pass
// triggerContent to replace the plain SelectValue (e.g. a swatch + label).
export function FileSheetSelectRow({
  label,
  value,
  onValueChange,
  options,
  ariaLabel,
  disabled = false,
  placeholder,
  triggerContent,
  className
}) {
  return (
    <FileSheetControlRow label={label} className={className}>
      <Select value={value} onValueChange={onValueChange} disabled={disabled}>
        <SelectTrigger
          size="sm"
          className={FILE_SHEET_SELECT_TRIGGER_CLASSES}
          aria-label={ariaLabel || (typeof label === "string" ? label : undefined)}
        >
          {triggerContent ?? <SelectValue placeholder={placeholder} />}
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem
              key={option.value}
              value={option.value}
              className="text-xs"
              title={option.title}
              icon={option.icon}
            >
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </FileSheetControlRow>
  );
}

// Compact color picker trigger for inline rows and parameter rows.
export function FileSheetColorPicker({
  value,
  onChange,
  className,
  swatchClassName,
  ...props
}) {
  return (
    <ColorPicker
      value={value}
      onChange={onChange}
      className={cn(
        FILE_SHEET_COMPACT_INPUT_CLASSES,
        "w-fit justify-start gap-1.5 px-1.5",
        className
      )}
      swatchClassName={cn("size-3.5", swatchClassName)}
      popoverAlign="end"
      {...props}
    />
  );
}

// Label left, color picker on the control axis.
export function FileSheetColorRow({ label, value, onChange, disabled = false, className, labelClassName }) {
  return (
    <FileSheetControlRow
      label={label}
      trailing={(
        <FileSheetColorPicker
          value={value}
          onChange={onChange}
          disabled={disabled}
          aria-label={typeof label === "string" ? label : undefined}
        />
      )}
      className={className}
      labelClassName={labelClassName}
    />
  );
}

// Radix Tabs strips inside a sheet (e.g. the Lights target selector) share the
// segmented-control silhouette so a sheet has exactly one row-of-buttons look.
export const FILE_SHEET_SEGMENTED_TABS_LIST_CLASSES = "grid h-7 w-full rounded-md bg-transparent p-0 shadow-xs";
export const FILE_SHEET_SEGMENTED_TAB_TRIGGER_CLASSES = [
  "h-7 min-w-0 rounded-none border border-l-0 border-input px-1.5 text-[11px] font-medium shadow-none",
  "first:rounded-l-md first:border-l last:rounded-r-md focus-visible:z-10",
  "text-muted-foreground hover:text-foreground",
  "data-[state=active]:!bg-accent data-[state=active]:!text-foreground data-[state=active]:font-semibold",
  "data-[state=active]:ring-1 data-[state=active]:ring-inset data-[state=active]:ring-border/80 data-[state=active]:shadow-none"
].join(" ");

function normalizeFileSheetWidth(width) {
  const numericWidth = Number(width);
  if (!Number.isFinite(numericWidth) || numericWidth <= 0) {
    return DEFAULT_FILE_SHEET_WIDTH;
  }
  return Math.max(DESKTOP_FILE_SHEET_MIN_WIDTH, numericWidth);
}

export default function FileSheet({
  open,
  title,
  isDesktop,
  width,
  onOpenChange,
  onStartResize,
  bodyClassName,
  scrollBody = true,
  children
}) {
  const desktopWidth = `min(${normalizeFileSheetWidth(width)}px, ${DESKTOP_FILE_SHEET_MAX_WIDTH})`;
  const sheetStyle = isDesktop
    ? {
      width: desktopWidth,
      flexBasis: desktopWidth,
      minWidth: `min(${DESKTOP_FILE_SHEET_MIN_WIDTH}px, ${DESKTOP_FILE_SHEET_MAX_WIDTH})`,
      maxWidth: DESKTOP_FILE_SHEET_MAX_WIDTH
    }
    : {
      width: MOBILE_FILE_SHEET_WIDTH,
      maxWidth: DESKTOP_FILE_SHEET_MAX_WIDTH
    };
  const sheetBody = scrollBody ? (
    <ScrollArea
      className={cn("min-h-0 flex-1", FILE_SHEET_CONTROL_TEXT_CLASSES, bodyClassName)}
      type="auto"
      viewportClassName="h-full"
    >
      {children}
    </ScrollArea>
  ) : (
    <div className={cn("flex min-h-0 flex-1 flex-col", FILE_SHEET_CONTROL_TEXT_CLASSES, bodyClassName)}>
      {children}
    </div>
  );

  if (!isDesktop) {
    return (
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          side="right"
          showCloseButton={false}
          className="cad-glass-surface gap-0 p-0 text-sidebar-foreground"
          style={sheetStyle}
          aria-label={title}
        >
          <SheetHeader className="sr-only">
            <SheetTitle>{title}</SheetTitle>
            <SheetDescription>Inspect and adjust the selected file.</SheetDescription>
          </SheetHeader>
          {sheetBody}
        </SheetContent>
      </Sheet>
    );
  }

  if (!open) {
    return null;
  }

  return (
    <aside
      className={cn(
        "cad-glass-surface pointer-events-auto z-30 flex h-full max-w-[calc(100vw_-_0.75rem)] flex-col border-l border-sidebar-border text-sidebar-foreground",
        isDesktop
          ? "relative shrink-0"
          : "absolute inset-y-0 right-0 shadow-xl"
      )}
      style={sheetStyle}
      aria-label={title}
    >
      {isDesktop && typeof onStartResize === "function" ? (
        <button
          type="button"
          aria-label={`Resize ${title} sidebar`}
          title="Resize sidebar"
          onPointerDown={onStartResize}
          className="group/file-sheet-resize absolute inset-y-0 -left-1.5 z-30 flex h-auto w-3 cursor-col-resize touch-none items-stretch justify-center rounded-none px-0 py-0 hover:bg-transparent"
        >
          <span className="w-px bg-transparent transition-colors group-hover/file-sheet-resize:bg-ring group-focus-visible/file-sheet-resize:bg-ring" />
        </button>
      ) : null}
      {sheetBody}
    </aside>
  );
}
