import { useEffect, useMemo, useRef } from "react";
import { ChevronRight, ClipboardPaste, Copy, Crosshair, Eye, EyeOff, Package, Pause, Play, RotateCcw } from "lucide-react";
import { cn } from "@/ui/utils";
import {
  flattenVisibleStepTreeRows,
  stepTreeNodeChildren
} from "cadjs/lib/step/stepTree";
import { resolveStepModuleNumberControlStep } from "@/workbench/stepModuleParameterControls";
import { useStepAnimationElapsed } from "@/workbench/stepAnimationStore";
import {
  Accordion
} from "../ui/accordion";
import { Button } from "../ui/button";
import { ColorPicker } from "../ui/color-picker";
import {
  CAD_DISPLAY_MODE,
  normalizeDisplaySettings
} from "cadjs/lib/displaySettings";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from "../ui/select";
import { Slider } from "../ui/slider";
import FileSheet, {
  FILE_SHEET_COMPACT_BUTTON_CLASSES,
  FILE_SHEET_COMPACT_INPUT_CLASSES,
  FILE_SHEET_PRECISION_SLIDER_CLASSES,
  FileSheetBooleanToggle,
  FileSheetControlRow,
  FileSheetSection,
  FileSheetSectionBody,
  FileSheetSliderField,
  FileSheetSubsection,
  FileSheetToggleRow,
  parseFileSheetNumberInput
} from "./FileSheet";
import FileMetadataSection from "./FileMetadataSection";
import FileStatusSection from "./FileStatusSection";
import {
  ClipSettingsControls,
  DisplaySettingsControls
} from "./ThemeSettingsPopover";

const compactButtonClasses = FILE_SHEET_COMPACT_BUTTON_CLASSES;
const compactInputClasses = FILE_SHEET_COMPACT_INPUT_CLASSES;
const compactIconButtonClasses = "size-6 text-muted-foreground hover:text-foreground";
const treeRowButtonClasses = "h-7 min-w-0 rounded-md px-1.5 text-xs font-normal text-sidebar-foreground shadow-none hover:bg-sidebar-accent hover:text-sidebar-accent-foreground";
const treeSectionId = "tree";
const displaySectionId = "display";
const collisionsSectionId = "collisions";
const treeRevealScrollPaddingTopPx = 120;
export const STEP_TREE_ROOT_ITEM_LIMIT = 15;
const STEP_MODULE_ANIMATION_SPEED_MIN = 0.1;
const STEP_MODULE_ANIMATION_SPEED_MAX = 3;
const STEP_ANALYSIS_PAIR_STATUS_RANK = Object.freeze({
  collision: 0,
  contact: 1,
  clearance: 2,
  separated: 3
});
const STEP_ANALYSIS_STATUS_STYLES = Object.freeze({
  collision: "border-red-400/55 bg-red-500/14 text-red-200",
  contact: "border-emerald-400/50 bg-emerald-500/14 text-emerald-200",
  clearance: "border-cyan-400/45 bg-cyan-500/12 text-cyan-100",
  separated: "border-slate-400/40 bg-slate-500/12 text-slate-200"
});
const STEP_ANALYSIS_TOOL_LABELS = Object.freeze({
  surfaces: "Surfaces",
  witnesses: "Witnesses",
  volumes: "Volumes",
  bounds: "Bounds",
  collisions: "Collisions",
  contacts: "Contacts",
  clearances: "Clearances"
});
const STEP_DISPLAY_MODE_OPTIONS = Object.freeze([
  { value: CAD_DISPLAY_MODE.SOLID, label: "Solid" },
  { value: CAD_DISPLAY_MODE.TRANSPARENT, label: "Transparent" },
  { value: CAD_DISPLAY_MODE.COLLISION, label: "Collision" },
  { value: CAD_DISPLAY_MODE.WIREFRAME, label: "Wire" }
]);
const DEFAULT_COLLISION_RUN_SETTINGS = Object.freeze({
  bodyDepth: 2,
  maxPairs: 1000,
  clearanceMm: 0,
  contactToleranceMm: 0.0001,
  collisionVolumeToleranceMm3: 0.000000001,
  timeBudgetMs: 0,
  includeContact: true,
  includeClearance: false,
  includeSeparated: false,
  includeAllowed: false,
  listBodies: false,
  noCache: false,
  setA: [],
  setB: [],
  pairs: [],
  allowPairs: [],
  exclude: [],
  collapse: []
});

function formatControlNumber(value) {
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

function formatSeconds(value) {
  const numericValue = Math.max(Number(value) || 0, 0);
  return `${numericValue.toFixed(numericValue >= 10 ? 1 : 2)}s`;
}

function parseAnimationSpeedInput(value, fallbackValue = 1) {
  return parseFileSheetNumberInput(value, {
    fallback: fallbackValue,
    min: STEP_MODULE_ANIMATION_SPEED_MIN,
    max: STEP_MODULE_ANIMATION_SPEED_MAX
  });
}

function parseCollisionIntegerInput(value, fallbackValue, { min = 1, max = 100000 } = {}) {
  const parsedValue = Number.parseInt(String(value ?? ""), 10);
  const numericValue = Number.isFinite(parsedValue) ? parsedValue : fallbackValue;
  return Math.min(Math.max(Math.round(Number(numericValue) || fallbackValue), min), max);
}

function parseCollisionNumberInput(value, fallbackValue, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  const parsedValue = Number(value);
  const numericValue = Number.isFinite(parsedValue) ? parsedValue : fallbackValue;
  return Math.min(Math.max(numericValue, min), max);
}

function collisionStringListValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || "").trim()).filter(Boolean).join(", ");
  }
  return String(value || "").trim();
}

function parseCollisionStringListInput(value) {
  return String(value || "")
    .split(/[\n,]+/u)
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatAnalysisCount(value) {
  const count = Math.max(Math.round(Number(value) || 0), 0);
  return count.toLocaleString();
}

function formatAnalysisNumber(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) {
    return "";
  }
  const absoluteValue = Math.abs(numericValue);
  const fixedValue = absoluteValue >= 1000
    ? numericValue.toFixed(0)
    : absoluteValue >= 100
      ? numericValue.toFixed(1)
      : absoluteValue >= 1
        ? numericValue.toFixed(2)
        : numericValue.toFixed(3);
  return fixedValue.replace(/\.?0+$/, "");
}

function formatAnalysisMeasurement(value, unit) {
  const formattedValue = formatAnalysisNumber(value);
  return formattedValue ? `${formattedValue} ${unit}` : "";
}

function normalizeAnalysisPairStatus(status) {
  const normalizedStatus = String(status || "").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(STEP_ANALYSIS_PAIR_STATUS_RANK, normalizedStatus)
    ? normalizedStatus
    : "separated";
}

function analysisPairSortRank(status) {
  const normalizedStatus = normalizeAnalysisPairStatus(status);
  return STEP_ANALYSIS_PAIR_STATUS_RANK[normalizedStatus] ?? STEP_ANALYSIS_PAIR_STATUS_RANK.separated;
}

function analysisStatusLabel(status) {
  const normalizedStatus = normalizeAnalysisPairStatus(status);
  return normalizedStatus.charAt(0).toUpperCase() + normalizedStatus.slice(1);
}

function analysisOccurrenceName(value) {
  const normalizedValue = String(value || "").trim();
  if (!normalizedValue) {
    return "";
  }
  return normalizedValue.startsWith("component:")
    ? normalizedValue.slice("component:".length)
    : normalizedValue;
}

function analysisPairMetric(pair, status) {
  if (status === "collision") {
    return {
      label: "Intersection volume",
      value: formatAnalysisMeasurement(pair?.intersectionVolumeMm3, "mm3")
    };
  }
  return {
    label: "Minimum distance",
    value: formatAnalysisMeasurement(pair?.minDistanceMm ?? pair?.distanceMm, "mm")
  };
}

function analysisStatusStyle(status) {
  return STEP_ANALYSIS_STATUS_STYLES[normalizeAnalysisPairStatus(status)] || STEP_ANALYSIS_STATUS_STYLES.separated;
}

function AnalysisCountPill({ label, value, status }) {
  return (
    <span
      className={cn(
        "inline-flex min-w-0 items-center justify-between gap-1.5 rounded-md border px-1.5 py-1 text-[10px] leading-none",
        analysisStatusStyle(status)
      )}
      title={`${label}: ${value}`}
    >
      <span className="truncate">{label}</span>
      <span className="font-mono font-semibold tabular-nums">{value}</span>
    </span>
  );
}

function CompactAnalysisToggle({ label, checked, onCheckedChange, disabled = false, ariaLabel }) {
  return (
    <label
      className={cn(
        "flex h-7 min-w-0 items-center justify-between gap-2 rounded-md border border-border/55 bg-sidebar-accent/20 px-2 text-[11px] text-sidebar-foreground/80",
        disabled && "opacity-55"
      )}
      title={label}
    >
      <span className="min-w-0 truncate">{label}</span>
      <FileSheetBooleanToggle
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        ariaLabel={ariaLabel || label}
      />
    </label>
  );
}

function CollisionNumberField({
  label,
  value,
  min,
  max,
  step,
  disabled,
  onChange,
  ariaLabel,
  title
}) {
  return (
    <label
      className="min-w-0 space-y-1 text-[10px] font-medium uppercase tracking-wide text-[var(--ui-text-muted)]"
      title={title || label}
    >
      <span>{label}</span>
      <input
        className={cn(compactInputClasses, "w-full font-mono tabular-nums")}
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange?.(event.target.value)}
        aria-label={ariaLabel || label}
      />
    </label>
  );
}

function CollisionTextField({
  label,
  value,
  disabled,
  onChange,
  ariaLabel,
  title
}) {
  return (
    <label
      className="min-w-0 space-y-1 text-[10px] font-medium uppercase tracking-wide text-[var(--ui-text-muted)]"
      title={title || label}
    >
      <span>{label}</span>
      <textarea
        className={cn(
          "min-h-12 w-full resize-y rounded-md border border-input bg-transparent px-2 py-1 text-[11px] font-mono leading-4 tabular-nums text-foreground shadow-xs outline-none transition-[color,box-shadow,border-color] placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30"
        )}
        rows={2}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange?.(event.target.value)}
        aria-label={ariaLabel || label}
      />
    </label>
  );
}

function leafIdsHidden(leafPartIds, hiddenPartIds) {
  const leafIds = Array.isArray(leafPartIds)
    ? leafPartIds.map((id) => String(id || "").trim()).filter(Boolean)
    : [];
  if (!leafIds.length) {
    return false;
  }
  const hidden = new Set(Array.isArray(hiddenPartIds) ? hiddenPartIds : []);
  return leafIds.every((id) => hidden.has(id));
}

function scrollTreeNodeIntoView(target) {
  if (!target) {
    return;
  }

  const viewport = target.closest("[data-slot='scroll-area-viewport']");
  if (!viewport) {
    target.scrollIntoView?.({
      block: "nearest",
      behavior: "instant"
    });
    return;
  }

  const targetRect = target.getBoundingClientRect();
  const viewportRect = viewport.getBoundingClientRect();
  const paddedTop = viewportRect.top + treeRevealScrollPaddingTopPx;

  if (targetRect.top < paddedTop) {
    viewport.scrollTop += targetRect.top - paddedTop;
    return;
  }

  if (targetRect.bottom > viewportRect.bottom) {
    viewport.scrollTop += targetRect.bottom - viewportRect.bottom;
  }
}

function StepModuleAnimationTimeControl({
  animationState,
  duration,
  enabled,
  onScrub
}) {
  const liveElapsedSec = useStepAnimationElapsed();
  const rawElapsedSec = animationState?.playing
    ? liveElapsedSec
    : Number(animationState?.elapsedSec) || 0;
  const elapsedSec = Math.min(Math.max(rawElapsedSec, 0), duration);

  return (
    <FileSheetSliderField
      label="Time"
      value={formatSeconds(elapsedSec)}
      onValueCommit={(nextValue) => {
        onScrub?.(parseFileSheetNumberInput(nextValue, {
          fallback: elapsedSec,
          min: 0,
          max: duration
        }));
      }}
      valueInputProps={{
        disabled: !enabled,
        ariaLabel: "STEP animation time value"
      }}
    >
      <Slider
        className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
        value={[elapsedSec]}
        min={0}
        max={duration}
        step={0.01}
        onValueChange={(nextValue) => onScrub?.(nextValue?.[0] ?? 0)}
        disabled={!enabled}
        aria-label="STEP animation time"
      />
    </FileSheetSliderField>
  );
}

export default function StepFileSheet({
  open,
  isDesktop,
  width,
  onOpenChange,
  onStartResize,
  selectedEntry,
  viewerLoading,
  isAssemblyView = false,
  stepTreeRoot,
  expandedTreeNodeIds,
  stepTreeRootShowMore = false,
  onStepTreeRootShowMoreChange,
  selectedPartIds,
  inspectedNodeId = "",
  selectableNodeIds = null,
  activeTreeNodeId: activeTreeNodeIdProp = "",
  hoveredPartId,
  hiddenPartIds,
  onSelectTreeNode,
  onToggleTreeNode,
  onInspectTreeNode,
  onClearSelection,
  onHoverTreeNode,
  treeSelectionDisabled = false,
  treeSelectionDisabledReason = "",
  onTogglePartVisibility,
  hideSelectedParts,
  showAllHiddenParts,
  stepModule = null,
  collisions = null,
  display = null,
  fileDownloadAvailable = false,
  viewerServerInfo = null,
  localFileOpenAvailable = false,
  fileAccessBusyKey = "",
  onOpenFileAsset,
  suppressDynamicMetadataStatus = false,
  statusItems = [],
  themeSections = null,
  openSectionIds = [],
  onOpenSectionIdsChange
}) {
  const rowRefs = useRef(new Map());
  const treeSelectClickTimerRef = useRef(null);
  const selectedIds = Array.isArray(selectedPartIds) ? selectedPartIds : [];
  const hiddenIds = Array.isArray(hiddenPartIds) ? hiddenPartIds : [];
  const normalizedInspectedNodeId = String(inspectedNodeId || "").trim();
  const selectableNodeIdSet = useMemo(() => {
    if (!Array.isArray(selectableNodeIds)) {
      return null;
    }
    return new Set(selectableNodeIds.map((id) => String(id || "").trim()).filter(Boolean));
  }, [selectableNodeIds]);
  const elideRootAssemblyRow = isAssemblyView && stepTreeNodeChildren(stepTreeRoot).length > 0;
  const rootTreeItemCount = elideRootAssemblyRow ? stepTreeNodeChildren(stepTreeRoot).length : 0;
  const rootTreeHasOverflow = rootTreeItemCount > STEP_TREE_ROOT_ITEM_LIMIT;
  const showAllRootTreeItems = !rootTreeHasOverflow || stepTreeRootShowMore === true;
  const hiddenRootTreeItemCount = Math.max(rootTreeItemCount - STEP_TREE_ROOT_ITEM_LIMIT, 0);
  const visibleRows = useMemo(
    () => flattenVisibleStepTreeRows(stepTreeRoot, expandedTreeNodeIds, {
      omitRoot: elideRootAssemblyRow,
      rootChildLimit: STEP_TREE_ROOT_ITEM_LIMIT,
      showAllRootChildren: showAllRootTreeItems
    }),
    [elideRootAssemblyRow, expandedTreeNodeIds, showAllRootTreeItems, stepTreeRoot]
  );
  const visibleRowIdsSignature = useMemo(
    () => visibleRows.map((row) => String(row?.id || "")).join("\n"),
    [visibleRows]
  );
  const hasAssemblyTree = isAssemblyView ? visibleRows.length > 0 : visibleRows.some((row) => row?.hasChildren);
  const activeTreeNodeId = String(activeTreeNodeIdProp || selectedIds[selectedIds.length - 1] || "").trim();
  const selectedPartCount = selectedIds.length;
  const hiddenPartCount = hiddenIds.length;
  const showTreeVisibilityControls = isAssemblyView === true;
  const treeSectionOpen = Array.isArray(openSectionIds) && openSectionIds.includes(treeSectionId);
  const treeSelectionTitle = treeSelectionDisabled
    ? String(treeSelectionDisabledReason || "Tree selection is disabled in the current parameter state.").trim()
    : "";
  const stepModuleDefinition = stepModule?.definition || null;
  const stepModuleParameters = Array.isArray(stepModuleDefinition?.parameters) ? stepModuleDefinition.parameters : [];
  const stepModuleAnimations = Array.isArray(stepModuleDefinition?.animations) ? stepModuleDefinition.animations : [];
  const stepModuleStatus = String(stepModule?.status || "").trim();
  const stepModuleError = String(stepModule?.error || "").trim();
  const stepModuleValues = stepModule?.parameterValues || {};
  const stepModuleAnimationState = stepModule?.animationState || {};
  const stepModuleAnimationDuration = Math.max(Number(stepModuleAnimationState.duration) || 1, 0.001);
  const stepModuleEnabled = stepModule?.enabled !== false;
  const displaySettings = useMemo(
    () => normalizeDisplaySettings(display?.settings),
    [display?.settings]
  );
  const displayMode = displaySettings.mode;
  const displayModeIsCollision = displayMode === CAD_DISPLAY_MODE.COLLISION;
  const collisionReportAvailable = collisions?.available === true;
  const collisionCanRun = collisions?.canRun === true;
  const displayModeOptions = useMemo(
    () => STEP_DISPLAY_MODE_OPTIONS.map((option) => (
      option.value === CAD_DISPLAY_MODE.COLLISION && !collisionReportAvailable
        ? {
            ...option,
            disabled: true,
            title: collisionCanRun ? "Run collisions first" : "Collision generation unavailable"
          }
        : option
    )),
    [collisionCanRun, collisionReportAvailable]
  );
  const collisionStatus = String(collisions?.status || "").trim();
  const collisionRunning = collisionStatus === "loading";
  const collisionError = String(collisions?.error || "").trim();
  const collisionSettings = collisions?.settings && typeof collisions.settings === "object" ? collisions.settings : {};
  const collisionBodyDepth = parseCollisionIntegerInput(collisionSettings.bodyDepth, 2, { min: 1, max: 12 });
  const collisionMaxPairs = parseCollisionIntegerInput(collisionSettings.maxPairs, 1000, { min: 1, max: 100000 });
  const collisionClearanceMm = parseCollisionNumberInput(collisionSettings.clearanceMm, 0, { min: 0, max: 100000 });
  const collisionContactToleranceMm = parseCollisionNumberInput(collisionSettings.contactToleranceMm, 0.0001, { min: 0, max: 1000 });
  const collisionVolumeToleranceMm3 = parseCollisionNumberInput(
    collisionSettings.collisionVolumeToleranceMm3,
    0.000000001,
    { min: 0, max: 1000000000 }
  );
  const collisionTimeBudgetMs = parseCollisionNumberInput(collisionSettings.timeBudgetMs, 0, { min: 0, max: 3600000 });
  const collisionIncludeContact = collisionSettings.includeContact !== false;
  const collisionIncludeClearance = collisionSettings.includeClearance === true;
  const collisionIncludeSeparated = collisionSettings.includeSeparated === true;
  const collisionIncludeAllowed = collisionSettings.includeAllowed === true;
  const collisionListBodies = collisionSettings.listBodies === true;
  const collisionNoCache = collisionSettings.noCache === true;
  const collisionSetA = collisionStringListValue(collisionSettings.setA);
  const collisionSetB = collisionStringListValue(collisionSettings.setB);
  const collisionPairsFilter = collisionStringListValue(collisionSettings.pairs);
  const collisionAllowPairs = collisionStringListValue(collisionSettings.allowPairs);
  const collisionExclude = collisionStringListValue(collisionSettings.exclude);
  const collisionCollapse = collisionStringListValue(collisionSettings.collapse);
  const collisionSummary = collisions?.summary && typeof collisions.summary === "object" ? collisions.summary : {};
  const collisionOccurrenceCount = formatAnalysisCount(collisionSummary.occurrenceCount);
  const collisionPairCount = formatAnalysisCount(collisionSummary.reportedPairCount ?? collisionSummary.analyzedPairCount);
  const collisionCollisionCount = formatAnalysisCount(collisionSummary.collisionCount);
  const collisionContactCount = formatAnalysisCount(collisionSummary.contactCount);
  const collisionClearanceCount = formatAnalysisCount(collisionSummary.clearanceCount);
  const collisionPairs = useMemo(() => {
    const occurrenceNameById = new Map(
      (Array.isArray(collisions?.occurrences) ? collisions.occurrences : [])
        .map((occurrence) => {
          const id = String(occurrence?.id || "").trim();
          const name = analysisOccurrenceName(occurrence?.name) || id;
          return [id, name];
        })
        .filter(([id]) => id)
    );
    return (Array.isArray(collisions?.pairs) ? collisions.pairs : [])
      .map((pair) => {
        const aId = String(pair?.a || "").trim();
        const bId = String(pair?.b || "").trim();
        const id = String(pair?.id || (aId && bId ? `${aId}:${bId}` : "")).trim();
        if (!id) {
          return null;
        }
        const status = normalizeAnalysisPairStatus(pair?.status);
        const statusLabel = analysisStatusLabel(status);
        const aName = occurrenceNameById.get(aId) || aId || "A";
        const bName = occurrenceNameById.get(bId) || bId || "B";
        const metric = analysisPairMetric(pair, status);
        return {
          id,
          status,
          statusLabel,
          aName,
          bName,
          label: `${statusLabel}: ${aName} / ${bName}`,
          metricLabel: metric.label,
          metricValue: metric.value
        };
      })
      .filter(Boolean)
      .sort((left, right) => (
        analysisPairSortRank(left.status) - analysisPairSortRank(right.status)
        || left.label.localeCompare(right.label)
      ));
  }, [collisions?.occurrences, collisions?.pairs]);
  const selectedCollisionPairId = String(collisions?.selectedPairId || "").trim();
  const collisionControlsDisabled = collisionRunning || collisionStatus === "error";
  const updateCollisionSetting = (patch) => {
    collisions?.onSettingsChange?.(patch);
  };
  const focusCollisionPair = (pairId = "") => {
    if (!collisionReportAvailable) {
      return;
    }
    display?.onModeChange?.(CAD_DISPLAY_MODE.COLLISION);
    collisions?.onSelectedPairChange?.(pairId);
  };
  const handleDisplayModeChange = (nextMode) => {
    if (nextMode === CAD_DISPLAY_MODE.COLLISION && !collisionReportAvailable) {
      return;
    }
    display?.onModeChange?.(nextMode);
  };

  useEffect(() => {
    if (displayModeIsCollision && !collisionReportAvailable) {
      display?.onModeChange?.(CAD_DISPLAY_MODE.TRANSPARENT);
    }
  }, [collisionReportAvailable, display, displayModeIsCollision]);

  useEffect(() => {
    if (!activeTreeNodeId || !treeSectionOpen) {
      return;
    }
    const scrollToActiveTreeNode = () => {
      scrollTreeNodeIntoView(rowRefs.current.get(activeTreeNodeId));
    };
    if (typeof window === "undefined") {
      scrollToActiveTreeNode();
      return;
    }
    const frameId = window.requestAnimationFrame(scrollToActiveTreeNode);
    return () => {
      window.cancelAnimationFrame(frameId);
    };
  }, [activeTreeNodeId, treeSectionOpen, visibleRowIdsSignature]);

  useEffect(() => () => {
    if (treeSelectClickTimerRef.current) {
      window.clearTimeout(treeSelectClickTimerRef.current);
      treeSelectClickTimerRef.current = null;
    }
  }, []);

  if (!selectedEntry) {
    return null;
  }

  return (
    <FileSheet
      open={open}
      title="STEP"
      isDesktop={isDesktop}
      width={width}
      onOpenChange={onOpenChange}
      onStartResize={onStartResize}
    >
      <Accordion
        type="multiple"
        value={openSectionIds}
        onValueChange={onOpenSectionIdsChange}
      >
        <FileStatusSection items={statusItems} />

        <FileSheetSection
          value={treeSectionId}
          title="Tree"
          triggerProps={{ title: treeSelectionTitle || undefined }}
        >
            {showTreeVisibilityControls ? (
              <div className="space-y-1.5 px-3 py-1.5">
                <div className="flex flex-wrap gap-1.5">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className={compactButtonClasses}
                    onClick={hideSelectedParts}
                    disabled={treeSelectionDisabled || selectedPartCount < 2}
                    title={treeSelectionDisabled ? treeSelectionTitle : selectedPartCount > 1 ? `Hide ${selectedPartCount} selected nodes` : "Select multiple nodes to hide them together"}
                  >
                    <EyeOff className="size-3" strokeWidth={2} aria-hidden="true" />
                    <span>Hide all</span>
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className={compactButtonClasses}
                    onClick={showAllHiddenParts}
                    disabled={hiddenPartCount < 1}
                    title={hiddenPartCount > 0 ? `Show ${hiddenPartCount} hidden ${hiddenPartCount === 1 ? "part" : "parts"}` : "No hidden parts to show"}
                  >
                    <Eye className="size-3" strokeWidth={2} aria-hidden="true" />
                    <span>Show all</span>
                  </Button>
                </div>
              </div>
            ) : null}

            <div className="max-w-full overflow-hidden px-1.5 pb-2">
              <div
                className="space-y-px"
                role="tree"
                aria-multiselectable="true"
                aria-disabled={treeSelectionDisabled}
                title={treeSelectionTitle || undefined}
                onClick={(event) => {
                  if (treeSelectionDisabled) {
                    return;
                  }
                  if (event.target === event.currentTarget) {
                    onClearSelection?.();
                  }
                }}
              >
              {viewerLoading && !visibleRows.length ? (
                <p className="px-1.5 py-2 text-xs text-[var(--ui-text-muted)]">
                  Loading STEP tree...
                </p>
              ) : null}

              {hasAssemblyTree
                ? visibleRows.map((row) => {
                  const selected = selectedIds.includes(row.id);
                  const inspected = normalizedInspectedNodeId === row.id;
                  const selectable = !selectableNodeIdSet || selectableNodeIdSet.has(row.id) || selected;
                  const rowSelectionDisabled = treeSelectionDisabled || !selectable;
                  const showSelectedRowState = selected;
                  const hovered = hoveredPartId === row.id;
                  const hidden = leafIdsHidden(row.leafPartIds, hiddenIds);
                  const VisibilityIcon = hidden ? EyeOff : Eye;
                  const visibilityLabel = hidden ? "Show" : "Hide";
                  const inspectLabel = inspected ? `Exit inspection for ${row.label}` : `Inspect ${row.label}`;
                  const rowTitle = treeSelectionTitle ||
                    (selectable ? row.label : inspected ? `Inspecting ${row.label}` : "Inspect this node to select its children");
                  const rowDepthPx = Math.min(Math.max(row.depth, 0) * 24, 144);
                  return (
                    <div
                      key={row.id}
                      ref={(node) => {
                        if (node) {
                          rowRefs.current.set(row.id, node);
                          return;
                        }
                        rowRefs.current.delete(row.id);
                      }}
                      role="treeitem"
                      aria-expanded={row.hasChildren ? row.expanded : undefined}
                      aria-selected={selected}
                      aria-current={inspected ? "true" : undefined}
                      data-selection-disabled={rowSelectionDisabled ? "true" : undefined}
                      className={cn("min-w-0 max-w-full rounded-md", hidden && "opacity-45")}
                      title={rowTitle}
                    >
                      <div className="flex h-7 min-w-0 max-w-full items-center gap-0.5">
                        <div className="flex min-w-0 flex-1 items-center gap-0 overflow-hidden" style={{ paddingLeft: rowDepthPx }}>
                          {row.hasChildren ? (
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-sm"
                              className="size-6 shrink-0 rounded-md hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                              onClick={(event) => {
                                event.stopPropagation();
                                onToggleTreeNode?.(row.id);
                              }}
                              aria-label={row.expanded ? `Collapse ${row.label}` : `Expand ${row.label}`}
                              title={row.expanded ? "Collapse" : "Expand"}
                            >
                              <ChevronRight
                                className={cn("size-3.5 transition-transform", row.expanded && "rotate-90")}
                                strokeWidth={2}
                                aria-hidden="true"
                              />
                            </Button>
                          ) : null}
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className={cn(
                              treeRowButtonClasses,
                              "w-0 flex-1 touch-manipulation justify-start overflow-hidden text-left",
                              !row.hasChildren && "gap-2 !px-2",
                              rowSelectionDisabled && "text-sidebar-foreground/55",
                              showSelectedRowState
                                ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                                : hovered && "bg-sidebar-accent text-sidebar-accent-foreground"
                            )}
                            title={rowTitle}
                            tabIndex={rowSelectionDisabled ? -1 : undefined}
                            onClick={(event) => {
                              if (rowSelectionDisabled) {
                                return;
                              }
                              const multiSelect = event.shiftKey;
                              if (treeSelectClickTimerRef.current) {
                                window.clearTimeout(treeSelectClickTimerRef.current);
                              }
                              treeSelectClickTimerRef.current = window.setTimeout(() => {
                                treeSelectClickTimerRef.current = null;
                                onSelectTreeNode?.(row.id, { multiSelect });
                              }, 180);
                            }}
                            onDoubleClick={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                              if (treeSelectClickTimerRef.current) {
                                window.clearTimeout(treeSelectClickTimerRef.current);
                                treeSelectClickTimerRef.current = null;
                              }
                              onInspectTreeNode?.(row.id);
                            }}
                            onMouseEnter={() => {
                              if (!rowSelectionDisabled) {
                                onHoverTreeNode?.(row.id);
                              }
                            }}
                            onMouseLeave={() => {
                              if (!rowSelectionDisabled) {
                                onHoverTreeNode?.("");
                              }
                            }}
                          >
                            {!row.hasChildren ? (
                              <Package className="size-3.5 shrink-0 text-sidebar-foreground/55" strokeWidth={1.8} aria-hidden="true" />
                            ) : null}
                            <span className="min-w-0 flex-1 overflow-hidden">
                              <span className="block truncate text-xs font-medium leading-4">
                                {row.label}
                              </span>
                            </span>
                          </Button>
                        </div>

                        {showTreeVisibilityControls ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            className={cn(
                              compactIconButtonClasses,
                              "rounded-md hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                              inspected && "text-sidebar-foreground ring-1 ring-sidebar-border"
                            )}
                            onClick={(event) => {
                              event.stopPropagation();
                              onInspectTreeNode?.(row.id);
                            }}
                            aria-label={inspectLabel}
                            title={inspectLabel}
                          >
                            <Crosshair className="size-2.5" strokeWidth={2} aria-hidden="true" />
                          </Button>
                        ) : null}

                        {showTreeVisibilityControls ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            className={cn(
                              compactIconButtonClasses,
                              "rounded-md hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                              hidden && "bg-sidebar-accent text-sidebar-accent-foreground"
                            )}
                            onClick={(event) => {
                              event.stopPropagation();
                              onTogglePartVisibility?.(row.id);
                            }}
                            aria-label={`${visibilityLabel} ${row.label}`}
                            title={visibilityLabel}
                          >
                            <VisibilityIcon className="size-2.5" strokeWidth={2} aria-hidden="true" />
                          </Button>
                        ) : null}
                      </div>
                    </div>
                  );
                })
                : null}

              {!hasAssemblyTree && !viewerLoading ? (
                <p className="px-1.5 py-2 text-xs text-[var(--ui-text-muted)]">
                  No assembly tree
                </p>
              ) : null}
              </div>

              {rootTreeHasOverflow ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className={cn(
                    compactButtonClasses,
                    "mt-1 h-7 w-full justify-start rounded-md px-2 text-[11px] text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                  )}
                  onClick={() => {
                    onStepTreeRootShowMoreChange?.(!showAllRootTreeItems);
                  }}
                  aria-expanded={showAllRootTreeItems}
                  title={showAllRootTreeItems
                    ? `Show first ${STEP_TREE_ROOT_ITEM_LIMIT} root items`
                    : `Show ${hiddenRootTreeItemCount} more root ${hiddenRootTreeItemCount === 1 ? "item" : "items"}`}
                >
                  <span>{showAllRootTreeItems ? "Show less" : "Show more"}</span>
                </Button>
              ) : null}
            </div>
        </FileSheetSection>

        {stepModuleDefinition || stepModuleStatus === "loading" || stepModuleError ? (
          <FileSheetSection value="parameters" title="Parameters">
              <FileSheetSectionBody>
                {stepModuleDefinition ? (
                  <FileSheetToggleRow
                    label="Enable"
                    checked={stepModuleEnabled}
                    onCheckedChange={(checked) => stepModule?.onEnabledChange?.(checked)}
                    ariaLabel="Enable STEP module"
                  />
                ) : null}

                {stepModuleStatus === "loading" ? (
                  <p className="px-3 py-2 text-xs text-[var(--ui-text-muted)]">Loading STEP module...</p>
                ) : null}
                {stepModuleError ? (
                  <p className="whitespace-pre-line px-3 py-2 text-xs text-destructive">{stepModuleError}</p>
                ) : null}

                {stepModuleDefinition && stepModuleAnimations.length ? (
                  <>
                    {stepModuleAnimations.length > 1 ? (
                      <FileSheetControlRow label="Animation">
                        <Select
                          value={String(stepModuleAnimationState.activeId || stepModuleAnimations[0]?.id || "")}
                          onValueChange={(nextValue) => stepModule?.onAnimationSelect?.(nextValue)}
                          disabled={!stepModuleEnabled}
                        >
                          <SelectTrigger size="sm" className="h-7 !text-[11px]" aria-label="STEP animation">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {stepModuleAnimations.map((animation) => (
                              <SelectItem key={animation.id} value={animation.id}>
                                {animation.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </FileSheetControlRow>
                    ) : null}
                    <FileSheetControlRow>
                      <div className="grid grid-cols-2 gap-2">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className={cn(compactButtonClasses, "justify-center")}
                          onClick={() => stepModule?.onAnimationPlayToggle?.()}
                          disabled={!stepModuleEnabled}
                        >
                          {stepModuleAnimationState.playing ? (
                            <Pause className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                          ) : (
                            <Play className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                          )}
                          <span>{stepModuleAnimationState.playing ? "Pause" : "Play"}</span>
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className={cn(compactButtonClasses, "justify-center")}
                          onClick={() => stepModule?.onAnimationReset?.()}
                          disabled={!stepModuleEnabled}
                          aria-label="Restart STEP animation"
                          title="Restart"
                        >
                          <RotateCcw className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                          <span>Reset</span>
                        </Button>
                      </div>
                    </FileSheetControlRow>
                    <StepModuleAnimationTimeControl
                      animationState={stepModuleAnimationState}
                      duration={stepModuleAnimationDuration}
                      enabled={stepModuleEnabled}
                      onScrub={stepModule?.onAnimationScrub}
                    />
                    <FileSheetSliderField
                      label="Speed"
                      value={`${formatControlNumber(stepModuleAnimationState.speed || 1)}x`}
                      onValueCommit={(nextValue) => {
                        stepModule?.onAnimationSpeedChange?.(
                          parseAnimationSpeedInput(nextValue, stepModuleAnimationState.speed || 1)
                        );
                      }}
                      valueInputProps={{
                        disabled: !stepModuleEnabled,
                        ariaLabel: "STEP animation speed value"
                      }}
                    >
                      <Slider
                        className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
                        value={[Number(stepModuleAnimationState.speed) || 1]}
                        min={STEP_MODULE_ANIMATION_SPEED_MIN}
                        max={STEP_MODULE_ANIMATION_SPEED_MAX}
                        step={0.1}
                        onValueChange={(nextValue) => stepModule?.onAnimationSpeedChange?.(nextValue?.[0] ?? 1)}
                        disabled={!stepModuleEnabled}
                        aria-label="STEP animation speed"
                      />
                    </FileSheetSliderField>
                  </>
                ) : null}

                {stepModuleDefinition && !stepModuleParameters.length ? (
                  <p className="px-3 py-2 text-xs text-[var(--ui-text-muted)]">No module parameters.</p>
                ) : null}
                {stepModuleParameters.map((parameter) => {
                  const value = stepModuleValues?.[parameter.id] ?? parameter.defaultValue;
                  const controlStep = resolveStepModuleNumberControlStep(parameter);
                  if (parameter.type === "boolean") {
                    return (
                      <FileSheetToggleRow
                        key={parameter.id}
                        label={parameter.label}
                        checked={value === true}
                        onCheckedChange={(checked) => stepModule?.onParameterChange?.(parameter.id, checked)}
                        disabled={!stepModuleEnabled}
                        ariaLabel={parameter.label}
                      />
                    );
                  }
                  if (parameter.type === "enum") {
                    return (
                      <FileSheetControlRow key={parameter.id} label={parameter.label}>
                        <Select
                          value={String(value ?? "")}
                          onValueChange={(nextValue) => stepModule?.onParameterChange?.(parameter.id, nextValue)}
                          disabled={!stepModuleEnabled}
                        >
                          <SelectTrigger size="sm" className="h-7 !text-[11px]" aria-label={parameter.label}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {parameter.options.map((option) => (
                              <SelectItem key={option.value} value={option.value}>
                                {option.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </FileSheetControlRow>
                    );
                  }
                  if (parameter.type === "color") {
                    return (
                      <FileSheetControlRow
                        key={parameter.id}
                        label={parameter.label}
                        trailing={(
                          <ColorPicker
                            value={String(value || "#ffffff")}
                            onChange={(nextValue) => stepModule?.onParameterChange?.(parameter.id, nextValue)}
                            disabled={!stepModuleEnabled}
                            className={cn(compactInputClasses, "w-fit justify-start gap-1.5 px-1.5")}
                            swatchClassName="size-3.5"
                            popoverAlign="end"
                            aria-label={parameter.label}
                          />
                        )}
                      />
                    );
                  }
                  if (parameter.type === "button") {
                    return (
                      <FileSheetControlRow key={parameter.id}>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className={cn(compactButtonClasses, "w-full justify-center")}
                          onClick={() => stepModule?.onParameterChange?.(parameter.id, Number(value || 0) + 1)}
                          disabled={!stepModuleEnabled}
                        >
                          {parameter.label}
                        </Button>
                      </FileSheetControlRow>
                    );
                  }
                  return (
                    <FileSheetSliderField
                      key={parameter.id}
                      label={parameter.label}
                      value={`${formatControlNumber(value)}${parameter.unit ? ` ${parameter.unit}` : ""}`}
                      onValueCommit={(nextValue) => {
                        stepModule?.onParameterChange?.(parameter.id, parseFileSheetNumberInput(nextValue, {
                          fallback: value,
                          min: parameter.min,
                          max: parameter.max
                        }));
                      }}
                      valueInputProps={{
                        disabled: !stepModuleEnabled,
                        ariaLabel: `${parameter.label} slider value`
                      }}
                    >
                      <Slider
                        className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
                        value={[Number(value) || 0]}
                        min={parameter.min}
                        max={parameter.max}
                        step={controlStep}
                        onValueChange={(nextValue) => stepModule?.onParameterChange?.(parameter.id, nextValue?.[0] ?? value)}
                        disabled={!stepModuleEnabled}
                        aria-label={parameter.label}
                      />
                    </FileSheetSliderField>
                  );
                })}
                {stepModuleDefinition && stepModuleParameters.length ? (
                  <FileSheetControlRow className="pt-2">
                    <div className="grid grid-cols-2 gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className={cn(compactButtonClasses, "justify-center")}
                        onClick={() => {
                          void stepModule?.onCopyParams?.();
                        }}
                        title="Copy STEP parameter JSON"
                      >
                        <Copy className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                        <span>Copy parameters</span>
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className={cn(compactButtonClasses, "justify-center")}
                        onClick={() => {
                          void stepModule?.onPasteParams?.();
                        }}
                        title="Paste STEP parameter JSON"
                      >
                        <ClipboardPaste className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                        <span>Paste parameters</span>
                      </Button>
                    </div>
                  </FileSheetControlRow>
                ) : null}
              </FileSheetSectionBody>
          </FileSheetSection>
        ) : null}

        <FileSheetSection value={displaySectionId} title="Display">
          <FileSheetSectionBody className="py-2">
            <DisplaySettingsControls
              displaySettings={displaySettings}
              updateDisplaySettings={display?.updateSettings}
              onDisplayModeChange={handleDisplayModeChange}
              modeOptions={displayModeOptions}
            />
            <FileSheetSubsection title="Clip" contentClassName="px-3">
              <ClipSettingsControls
                displaySettings={displaySettings}
                updateDisplaySettings={display?.updateSettings}
                clipBounds={display?.clipBounds || null}
              />
            </FileSheetSubsection>
          </FileSheetSectionBody>
        </FileSheetSection>

        <FileSheetSection value={collisionsSectionId} title="Collisions">
          <FileSheetSectionBody className="py-2">
            <div className="space-y-2 px-3">
              <div className="flex flex-wrap items-center gap-1.5">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className={compactButtonClasses}
                  onClick={() => collisions?.onRun?.()}
                  disabled={!collisionCanRun || collisionRunning}
                  title={collisionCanRun ? "Run collision detection" : "Collision generation is unavailable"}
                >
                  <Play className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                  <span>{collisionRunning ? "Running" : collisionReportAvailable ? "Run again" : "Run"}</span>
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className={compactButtonClasses}
                  onClick={() => collisions?.onSettingsChange?.(DEFAULT_COLLISION_RUN_SETTINGS)}
                  disabled={collisionRunning}
                  title="Reset collision run parameters"
                >
                  <RotateCcw className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                  <span>Defaults</span>
                </Button>
                <span className="min-w-0 flex-1 truncate text-[10px] leading-4 text-[var(--ui-text-muted)]">
                  {collisionRunning ? (
                    collisions?.loadStage || "Running collisions..."
                  ) : collisionStatus === "error" ? (
                    <span className="text-destructive">{collisionError || "Collisions unavailable."}</span>
                  ) : collisionReportAvailable ? (
                    `${collisionPairCount} pairs across ${collisionOccurrenceCount} bodies`
                  ) : collisionCanRun ? (
                    "No collision report"
                  ) : (
                    "Unavailable"
                  )}
                </span>
              </div>
            </div>

            <FileSheetSubsection title="Run Parameters" contentClassName="px-3">
              <div className="grid grid-cols-2 gap-2">
                <CollisionNumberField
                  label="Depth"
                  min={1}
                  max={12}
                  step={1}
                  value={collisionBodyDepth}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({
                    bodyDepth: parseCollisionIntegerInput(value, collisionBodyDepth, { min: 1, max: 12 })
                  })}
                  ariaLabel="Collision body depth"
                  title="Occurrence depth used to group bodies"
                />
                <CollisionNumberField
                  label="Pairs"
                  min={1}
                  max={100000}
                  step={1}
                  value={collisionMaxPairs}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({
                    maxPairs: parseCollisionIntegerInput(value, collisionMaxPairs, { min: 1, max: 100000 })
                  })}
                  ariaLabel="Collision pair limit"
                  title="Maximum candidate pairs to solve"
                />
                <CollisionNumberField
                  label="Clearance mm"
                  min={0}
                  max={100000}
                  step={0.01}
                  value={collisionClearanceMm}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({
                    clearanceMm: parseCollisionNumberInput(value, collisionClearanceMm, { min: 0, max: 100000 })
                  })}
                  ariaLabel="Collision clearance distance in millimeters"
                  title="Near-clearance distance in millimeters"
                />
                <CollisionNumberField
                  label="Budget ms"
                  min={0}
                  max={3600000}
                  step={100}
                  value={collisionTimeBudgetMs}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({
                    timeBudgetMs: parseCollisionNumberInput(value, collisionTimeBudgetMs, { min: 0, max: 3600000 })
                  })}
                  ariaLabel="Collision time budget in milliseconds"
                  title="Maximum elapsed milliseconds before stopping new pair solves"
                />
                <CollisionNumberField
                  label="Contact tol"
                  min={0}
                  max={1000}
                  step={0.0001}
                  value={collisionContactToleranceMm}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({
                    contactToleranceMm: parseCollisionNumberInput(value, collisionContactToleranceMm, { min: 0, max: 1000 })
                  })}
                  ariaLabel="Collision contact tolerance in millimeters"
                  title="Contact tolerance in millimeters"
                />
                <CollisionNumberField
                  label="Volume tol"
                  min={0}
                  max={1000000000}
                  step={0.000000001}
                  value={collisionVolumeToleranceMm3}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({
                    collisionVolumeToleranceMm3: parseCollisionNumberInput(value, collisionVolumeToleranceMm3, {
                      min: 0,
                      max: 1000000000
                    })
                  })}
                  ariaLabel="Collision volume tolerance in cubic millimeters"
                  title="Minimum common volume classified as collision"
                />
              </div>
            </FileSheetSubsection>

            <FileSheetSubsection title="Report" contentClassName="px-3">
              <div className="grid grid-cols-2 gap-1.5">
                <CompactAnalysisToggle
                  label="Contacts"
                  checked={collisionIncludeContact}
                  onCheckedChange={(checked) => updateCollisionSetting({ includeContact: checked === true })}
                  disabled={collisionRunning}
                  ariaLabel="Report contact pairs"
                />
                <CompactAnalysisToggle
                  label="Clearances"
                  checked={collisionIncludeClearance}
                  onCheckedChange={(checked) => updateCollisionSetting({ includeClearance: checked === true })}
                  disabled={collisionRunning}
                  ariaLabel="Report clearance pairs"
                />
                <CompactAnalysisToggle
                  label="Separated"
                  checked={collisionIncludeSeparated}
                  onCheckedChange={(checked) => updateCollisionSetting({ includeSeparated: checked === true })}
                  disabled={collisionRunning}
                  ariaLabel="Report separated pairs"
                />
                <CompactAnalysisToggle
                  label="Allowed"
                  checked={collisionIncludeAllowed}
                  onCheckedChange={(checked) => updateCollisionSetting({ includeAllowed: checked === true })}
                  disabled={collisionRunning}
                  ariaLabel="Report allowed pairs"
                />
                <CompactAnalysisToggle
                  label="Bodies only"
                  checked={collisionListBodies}
                  onCheckedChange={(checked) => updateCollisionSetting({ listBodies: checked === true })}
                  disabled={collisionRunning}
                  ariaLabel="Only list collision bodies"
                />
                <CompactAnalysisToggle
                  label="No cache"
                  checked={collisionNoCache}
                  onCheckedChange={(checked) => updateCollisionSetting({ noCache: checked === true })}
                  disabled={collisionRunning}
                  ariaLabel="Run collisions without cache"
                />
              </div>
            </FileSheetSubsection>

            <FileSheetSubsection title="Selectors" contentClassName="px-3">
              <div className="grid grid-cols-2 gap-2">
                <CollisionTextField
                  label="Set A"
                  value={collisionSetA}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({ setA: parseCollisionStringListInput(value) })}
                  ariaLabel="Collision set A selectors"
                  title="Body selector or name patterns for set A"
                />
                <CollisionTextField
                  label="Set B"
                  value={collisionSetB}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({ setB: parseCollisionStringListInput(value) })}
                  ariaLabel="Collision set B selectors"
                  title="Body selector or name patterns for set B"
                />
                <CollisionTextField
                  label="Pairs"
                  value={collisionPairsFilter}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({ pairs: parseCollisionStringListInput(value) })}
                  ariaLabel="Explicit collision pair selectors"
                  title="Explicit left:right pair selectors"
                />
                <CollisionTextField
                  label="Allow"
                  value={collisionAllowPairs}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({ allowPairs: parseCollisionStringListInput(value) })}
                  ariaLabel="Allowed collision pair selectors"
                  title="left:right pair selectors treated as allowed"
                />
                <CollisionTextField
                  label="Exclude"
                  value={collisionExclude}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({ exclude: parseCollisionStringListInput(value) })}
                  ariaLabel="Excluded collision selectors"
                  title="Body selector or name patterns to omit"
                />
                <CollisionTextField
                  label="Collapse"
                  value={collisionCollapse}
                  disabled={collisionRunning}
                  onChange={(value) => updateCollisionSetting({ collapse: parseCollisionStringListInput(value) })}
                  ariaLabel="Collapsed collision selectors"
                  title="Occurrence selector or name patterns to collapse into one body"
                />
              </div>
            </FileSheetSubsection>

            {collisionReportAvailable ? (
              <div className="grid grid-cols-3 gap-1 px-3">
                <AnalysisCountPill label="Collisions" value={collisionCollisionCount} status="collision" />
                <AnalysisCountPill label="Contacts" value={collisionContactCount} status="contact" />
                <AnalysisCountPill label="Clearances" value={collisionClearanceCount} status="clearance" />
              </div>
            ) : null}

            {collisionReportAvailable ? (
              <FileSheetSubsection title="Pairs" contentClassName="px-1.5">
                {collisionRunning ? (
                  <p className="px-1.5 py-1 text-xs text-[var(--ui-text-muted)]">Running collisions...</p>
                ) : collisionStatus === "error" ? (
                  <p className="whitespace-pre-line px-1.5 py-1 text-xs text-destructive">{collisionError || "Collisions unavailable."}</p>
                ) : (
                  <div className="space-y-px" role="list" aria-label="Collision pairs">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className={cn(
                        treeRowButtonClasses,
                        "h-8 w-full justify-start gap-2 px-2 text-left",
                        displayModeIsCollision && !selectedCollisionPairId && "bg-sidebar-accent text-sidebar-accent-foreground"
                      )}
                      onClick={() => focusCollisionPair("")}
                      disabled={collisionControlsDisabled || !collisionPairs.length}
                      aria-pressed={displayModeIsCollision && !selectedCollisionPairId}
                      title="Show all collision pairs"
                    >
                      <span className="min-w-0 flex-1 truncate">All pairs</span>
                      <span className="font-mono text-[10px] text-[var(--ui-text-muted)]">{collisionPairCount}</span>
                    </Button>
                    {collisionPairs.map((pair) => {
                      const selected = displayModeIsCollision && selectedCollisionPairId === pair.id;
                      return (
                        <Button
                          key={pair.id}
                          type="button"
                          variant="ghost"
                          size="sm"
                          className={cn(
                            treeRowButtonClasses,
                            "h-10 w-full justify-start gap-2 px-2 text-left",
                            selected && "bg-sidebar-accent text-sidebar-accent-foreground"
                          )}
                          onClick={() => focusCollisionPair(pair.id)}
                          disabled={collisionControlsDisabled}
                          aria-pressed={selected}
                          title={pair.label}
                        >
                          <span
                            className={cn(
                              "h-2 w-2 shrink-0 rounded-full border",
                              analysisStatusStyle(pair.status)
                            )}
                            aria-hidden="true"
                          />
                          <span className="min-w-0 flex-1 overflow-hidden">
                            <span className="block truncate text-[11px] font-medium leading-4">
                              {pair.aName} / {pair.bName}
                            </span>
                            <span className="block truncate text-[10px] leading-3 text-[var(--ui-text-muted)]">
                              {pair.metricValue || pair.statusLabel}
                            </span>
                          </span>
                          <span
                            className={cn(
                              "shrink-0 rounded border px-1 py-0.5 text-[9px] font-medium leading-none",
                              analysisStatusStyle(pair.status)
                            )}
                          >
                            {pair.statusLabel}
                          </span>
                        </Button>
                      );
                    })}
                    {!collisionPairs.length ? (
                      <p className="px-1.5 py-1 text-[11px] leading-4 text-[var(--ui-text-muted)]">
                        No reportable pairs.
                      </p>
                    ) : null}
                  </div>
                )}
              </FileSheetSubsection>
            ) : null}

            {collisionReportAvailable && displayModeIsCollision ? (
              <FileSheetSubsection title="Collision Tools" contentClassName="px-3">
                <div className="grid grid-cols-2 gap-1.5">
                  <CompactAnalysisToggle
                    label={STEP_ANALYSIS_TOOL_LABELS.surfaces}
                    checked={collisions?.showSurfaceHighlights !== false}
                    onCheckedChange={(checked) => collisions?.onShowSurfaceHighlightsChange?.(checked)}
                    disabled={collisionControlsDisabled}
                    ariaLabel="Show collision and contact surfaces"
                  />
                  <CompactAnalysisToggle
                    label={STEP_ANALYSIS_TOOL_LABELS.witnesses}
                    checked={collisions?.showWitnesses !== false}
                    onCheckedChange={(checked) => collisions?.onShowWitnessesChange?.(checked)}
                    disabled={collisionControlsDisabled}
                    ariaLabel="Show collision witnesses"
                  />
                  <CompactAnalysisToggle
                    label={STEP_ANALYSIS_TOOL_LABELS.volumes}
                    checked={collisions?.showInterferenceVolumes !== false}
                    onCheckedChange={(checked) => collisions?.onShowInterferenceVolumesChange?.(checked)}
                    disabled={collisionControlsDisabled}
                    ariaLabel="Show interference volumes"
                  />
                  <CompactAnalysisToggle
                    label={STEP_ANALYSIS_TOOL_LABELS.bounds}
                    checked={collisions?.showBounds === true}
                    onCheckedChange={(checked) => collisions?.onShowBoundsChange?.(checked)}
                    disabled={collisionControlsDisabled}
                    ariaLabel="Show collision part bounds"
                  />
                  <CompactAnalysisToggle
                    label={STEP_ANALYSIS_TOOL_LABELS.collisions}
                    checked={collisions?.showCollisions !== false}
                    onCheckedChange={(checked) => collisions?.onShowCollisionsChange?.(checked)}
                    disabled={collisionControlsDisabled}
                    ariaLabel="Show collision pairs"
                  />
                  <CompactAnalysisToggle
                    label={STEP_ANALYSIS_TOOL_LABELS.contacts}
                    checked={collisions?.showContacts !== false}
                    onCheckedChange={(checked) => collisions?.onShowContactsChange?.(checked)}
                    disabled={collisionControlsDisabled}
                    ariaLabel="Show contact pairs"
                  />
                  <CompactAnalysisToggle
                    label={STEP_ANALYSIS_TOOL_LABELS.clearances}
                    checked={collisions?.showClearances !== false}
                    onCheckedChange={(checked) => collisions?.onShowClearancesChange?.(checked)}
                    disabled={collisionControlsDisabled}
                    ariaLabel="Show clearance pairs"
                  />
                </div>
              </FileSheetSubsection>
            ) : null}
          </FileSheetSectionBody>
        </FileSheetSection>

        {themeSections}
        <FileMetadataSection
          entry={selectedEntry}
          fileDownloadAvailable={fileDownloadAvailable}
          viewerServerInfo={viewerServerInfo}
          localFileOpenAvailable={localFileOpenAvailable}
          fileAccessBusyKey={fileAccessBusyKey}
          onOpenFileAsset={onOpenFileAsset}
          suppressDynamicStatus={suppressDynamicMetadataStatus}
        />
      </Accordion>
    </FileSheet>
  );
}
