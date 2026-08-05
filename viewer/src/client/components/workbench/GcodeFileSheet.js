import FileSheet, {
  FILE_SHEET_PRECISION_SLIDER_CLASSES,
  FileSheetFieldGrid,
  FileSheetSectionBody,
  FileSheetSliderField,
  FileSheetStatusText,
  FileSheetSubsection,
  FileSheetToggleRow,
  FileSheetValueField,
  parseFileSheetNumberInput
} from "./FileSheet";
import FileSheetTabbedSurface from "./FileSheetTabbedSurface";
import { Badge } from "../ui/badge";
import { Slider } from "../ui/slider";
import {
  DEFAULT_GCODE_PREVIEW_DETAIL_LEVEL,
  GCODE_PREVIEW_DETAIL_MAX,
  GCODE_PREVIEW_DETAIL_MIN
} from "cadjs/lib/gcode/buildPreviewMesh";
import { buildFileStatusTab } from "./FileStatusSection";
const compactNumberBadgeClasses = "h-4 rounded-sm px-1.5 py-0 text-[10px] font-medium leading-none";

function formatNumber(value, digits = 2) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue.toFixed(digits) : (0).toFixed(digits);
}

function formatCount(value) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? String(Math.round(numericValue)) : "0";
}

function formatOrdinal(value) {
  const numericValue = Math.round(Number(value) || 0);
  const absoluteValue = Math.abs(numericValue);
  const tensRemainder = absoluteValue % 100;
  if (tensRemainder >= 11 && tensRemainder <= 13) {
    return `${numericValue}th`;
  }
  switch (absoluteValue % 10) {
    case 1:
      return `${numericValue}st`;
    case 2:
      return `${numericValue}nd`;
    case 3:
      return `${numericValue}rd`;
    default:
      return `${numericValue}th`;
  }
}

function formatDistance(value, digits = 1) {
  return `${formatNumber(value, digits)} mm`;
}

function formatCommandCount(value) {
  const count = formatCount(value);
  return `${count} cmd${count === "1" ? "" : "s"}`;
}

function formatFeatureCategory(value) {
  const normalized = String(value || "").trim();
  return normalized ? `${normalized[0].toUpperCase()}${normalized.slice(1)}` : "Other";
}

function previewDetailLabel(value) {
  switch (Math.round(Number(value) || DEFAULT_GCODE_PREVIEW_DETAIL_LEVEL)) {
    case 1:
      return "Light";
    case 2:
      return "Lean";
    case 4:
      return "Dense";
    case 5:
      return "High";
    case 6:
      return "Very high";
    case 7:
      return "Max adaptive";
    case 3:
    default:
      return "Standard";
  }
}

function normalizedBounds(bounds) {
  const min = Array.isArray(bounds?.min) ? bounds.min : [0, 0, 0];
  const max = Array.isArray(bounds?.max) ? bounds.max : [0, 0, 0];
  return { min, max };
}

function boundsAxisText(bounds, axis, digits = 1) {
  const { min, max } = normalizedBounds(bounds);
  return `${formatNumber(min[axis], digits)} to ${formatNumber(max[axis], digits)} mm`;
}

function visibleLayerText(layerCount, maxLayer) {
  if (layerCount < 1) {
    return "0 / 0";
  }
  const visibleCount = maxLayer + 1;
  return visibleCount > 1
    ? `1-${visibleCount} / ${layerCount}`
    : `1 / ${layerCount}`;
}

function parseVisibleLayerInput(value, fallbackVisibleCount, layerCount) {
  if (layerCount < 1) {
    return 0;
  }
  const text = String(value ?? "").trim();
  const rangeMatch = text.match(/\b\d+\s*-\s*(\d+)\b/);
  const parsedValue = rangeMatch?.[1] ?? value;
  return parseFileSheetNumberInput(parsedValue, {
    fallback: fallbackVisibleCount,
    min: 1,
    max: Math.max(layerCount, 0),
    integer: true
  }) - 1;
}

const GcodeValueField = FileSheetValueField;

export default function GcodeFileSheet({
  open,
  isDesktop,
  width,
  selectedEntry = null,
  onOpenChange,
  onStartResize,
  gcodeData = null,
  previewMetadata = null,
  maxLayer = 0,
  showTravel = false,
  fullDetail = false,
  previewDetailLevel = DEFAULT_GCODE_PREVIEW_DETAIL_LEVEL,
  onMaxLayerChange,
  onShowTravelChange,
  onFullDetailChange,
  onPreviewDetailLevelChange,
  fileDownloadAvailable = false,
  viewerServerInfo = null,
  localFileOpenAvailable = false,
  fileAccessBusyKey = "",
  onOpenFileAsset,
  suppressDynamicMetadataStatus = false,
  statusItems = [],
  themeTabs = [],
  openSectionIds = [],
  onOpenSectionIdsChange
}) {
  const layers = Array.isArray(gcodeData?.layers) ? gcodeData.layers : [];
  const stats = gcodeData?.stats || {};
  const features = Array.isArray(gcodeData?.features) ? gcodeData.features : [];
  const supportFeatures = features.filter((feature) => feature?.category === "support");
  const hasSupports = supportFeatures.some((feature) => (
    Number(feature?.extrusionMoves) > 0 ||
    Number(feature?.markerCount) > 0
  ));
  const featureMarkerCount = features.reduce((total, feature) => total + (Number(feature?.markerCount) || 0), 0);
  const renderedSegments = Number(previewMetadata?.renderedSegments);
  const visibleSegments = Number(previewMetadata?.visibleSegments);
  const layerStride = Number(previewMetadata?.layerStride);
  const renderedLayers = Number(previewMetadata?.renderedLayers);
  const visiblePreviewLayers = Number(previewMetadata?.visibleLayers);
  const decimatedPreview = Boolean(previewMetadata?.decimated);
  const renderedPathText = Number.isFinite(renderedSegments) && Number.isFinite(visibleSegments)
    ? `${formatCount(renderedSegments)} / ${formatCount(visibleSegments)}`
    : "";
  const renderedLayerText = Number.isFinite(renderedLayers) && Number.isFinite(visiblePreviewLayers)
    ? `${formatCount(renderedLayers)} / ${formatCount(visiblePreviewLayers)}`
    : "";
  const layerCount = layers.length;
  const safePreviewDetailLevel = Math.min(
    Math.max(Math.round(Number(previewDetailLevel) || DEFAULT_GCODE_PREVIEW_DETAIL_LEVEL), GCODE_PREVIEW_DETAIL_MIN),
    GCODE_PREVIEW_DETAIL_MAX
  );
  const safeMaxLayer = layerCount > 0
    ? Math.min(Math.max(Math.trunc(Number(maxLayer) || 0), 0), layerCount - 1)
    : 0;
  const hasGcodeData = Boolean(gcodeData);
  const visibleLayers = visibleLayerText(layerCount, safeMaxLayer);

  const sections = [
    buildFileStatusTab(statusItems),
    {
      id: "toolpath",
      title: "Toolpath",
      content: (
            <div>
              {!hasGcodeData ? (
                <FileSheetStatusText className="py-1">Loading G-code...</FileSheetStatusText>
              ) : null}

              <FileSheetSubsection title="Summary">
                <FileSheetFieldGrid columns={3}>
                  <FileSheetValueField label="Layers" value={formatCount(layerCount)} />
                  <FileSheetValueField label="Paths" value={formatCount(stats.extrusionMoves)} />
                  <FileSheetValueField label="Supports" value={hasSupports ? "Yes" : "No"} />
                  {decimatedPreview ? (
                    <FileSheetValueField label="Preview" value="Layer sampled" />
                  ) : null}
                </FileSheetFieldGrid>
              </FileSheetSubsection>

              <FileSheetSubsection title="Layers">
                <FileSheetSliderField
                  label="Visible layers"
                  value={visibleLayers}
                  onValueCommit={(nextValue) => {
                    onMaxLayerChange?.(parseVisibleLayerInput(nextValue, safeMaxLayer + 1, layerCount));
                  }}
                  valueInputProps={{
                    disabled: layerCount <= 1,
                    ariaLabel: "Visible G-code layers value",
                    inputMode: "numeric"
                  }}
                >
                <Slider
                  value={[safeMaxLayer]}
                  min={0}
                  max={Math.max(layerCount - 1, 0)}
                  step={1}
                  disabled={layerCount <= 1}
                  onValueChange={(nextValue) => {
                    onMaxLayerChange?.(Number(nextValue?.[0] ?? 0));
                  }}
                  className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
                  aria-label="Visible G-code layers"
                />
                </FileSheetSliderField>
              </FileSheetSubsection>

              <FileSheetSubsection
                title="Preview"
              >
                <FileSheetSliderField
                  label="Adaptive detail"
                  value={`${previewDetailLabel(safePreviewDetailLevel)} (${safePreviewDetailLevel}/${GCODE_PREVIEW_DETAIL_MAX})`}
                  onValueCommit={(nextValue) => {
                    onPreviewDetailLevelChange?.(parseFileSheetNumberInput(nextValue, {
                      fallback: safePreviewDetailLevel,
                      min: GCODE_PREVIEW_DETAIL_MIN,
                      max: GCODE_PREVIEW_DETAIL_MAX,
                      integer: true
                    }));
                  }}
                  valueInputProps={{
                    disabled: fullDetail,
                    className: "w-28",
                    ariaLabel: "G-code preview detail value",
                    inputMode: "numeric"
                  }}
                >
                <Slider
                  value={[safePreviewDetailLevel]}
                  min={GCODE_PREVIEW_DETAIL_MIN}
                  max={GCODE_PREVIEW_DETAIL_MAX}
                  step={1}
                  disabled={fullDetail}
                  onValueChange={(nextValue) => {
                    onPreviewDetailLevelChange?.(Number(nextValue?.[0] ?? DEFAULT_GCODE_PREVIEW_DETAIL_LEVEL));
                  }}
                  className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
                  aria-label="G-code preview detail"
                />
                </FileSheetSliderField>
              </FileSheetSubsection>

              <FileSheetSubsection title="Rendering">
                <FileSheetToggleRow
                  label="Travel moves"
                  description={`${formatCount(stats.travelMoves)} move${formatCount(stats.travelMoves) === "1" ? "" : "s"}`}
                  checked={showTravel}
                  onCheckedChange={(checked) => onShowTravelChange?.(checked)}
                  ariaLabel="Show G-code travel moves"
                />

                <FileSheetToggleRow
                  label="Full detail"
                  description={fullDetail
                    ? "Exact: render every visible path."
                    : renderedPathText
                      ? `${renderedPathText} rendered path${renderedSegments === 1 ? "" : "s"}${decimatedPreview && Number.isFinite(layerStride) ? ` across ${renderedLayerText || "sampled"} layer${renderedLayers === 1 ? "" : "s"}, every ${formatOrdinal(layerStride)}` : ""}`
                      : "Use adaptive layer sampling."}
                  checked={fullDetail}
                  onCheckedChange={(checked) => onFullDetailChange?.(checked)}
                  ariaLabel="Render full G-code detail"
                />
              </FileSheetSubsection>
            </div>
      )
    },
    {
      id: "features",
      title: (
            <span className="flex min-w-0 items-center gap-1.5">
              <span>Features</span>
              {features.length ? (
                <Badge variant="outline" className={compactNumberBadgeClasses}>{features.length}</Badge>
              ) : null}
              {hasSupports ? (
                <Badge variant="outline" className="font-normal">Supports</Badge>
              ) : null}
            </span>
      ),
      content: (
            <div className="space-y-2 px-2 py-2">
              {!features.length ? (
                <FileSheetStatusText className="px-0">
                  No slicer feature annotations found. The preview can still show layers, extrusion paths, and travel moves.
                </FileSheetStatusText>
              ) : (
                <>
                  <div className="flex flex-wrap gap-1.5 text-[11px]">
                    <Badge variant={hasSupports ? "secondary" : "outline"} className="font-normal">
                      Supports {hasSupports ? "included" : "not detected"}
                    </Badge>
                    <Badge variant="outline" className={compactNumberBadgeClasses}>
                      {formatCount(featureMarkerCount)} markers
                    </Badge>
                  </div>
                  <ul className="divide-y divide-border/60 overflow-hidden rounded-md border border-border/60 bg-muted/15">
                    {features.map((feature) => {
                      const extrusionMoves = formatCount(feature?.extrusionMoves);
                      const markerCount = formatCount(feature?.markerCount);
                      const detailParts = [
                        `${extrusionMoves} path${extrusionMoves === "1" ? "" : "s"}`,
                        formatDistance(feature?.pathMm, 1),
                        feature?.layerRangeText || "",
                        markerCount !== "0" ? `${markerCount} marker${markerCount === "1" ? "" : "s"}` : ""
                      ].filter(Boolean);
                      const rawLabels = Array.isArray(feature?.rawLabels) && feature.rawLabels.length
                        ? feature.rawLabels.join(", ")
                        : "";
                      return (
                        <li
                          key={feature?.id || feature?.label}
                          className="min-w-0 px-2 py-1"
                          title={rawLabels}
                        >
                          <div className="flex items-center gap-2">
                            <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
                              {feature?.label || "Other"}
                            </span>
                            <span className="shrink-0 text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                              {formatFeatureCategory(feature?.category)}
                            </span>
                          </div>
                          <p className="truncate text-[11px] leading-5 text-muted-foreground">
                            {detailParts.join(" | ")}
                          </p>
                        </li>
                      );
                    })}
                  </ul>
                </>
              )}
            </div>
      )
    },
    {
      id: "stats",
      title: "Stats",
      content: (
            <FileSheetSectionBody>
              <FileSheetFieldGrid columns={2}>
                <GcodeValueField label="Extrusion" value={formatDistance(stats.extrusionMm, 1)} />
                <GcodeValueField label="Path length" value={formatDistance(stats.pathMm, 1)} />
                <GcodeValueField label="Retracts" value={formatCount(stats.retractMoves)} />
                <GcodeValueField label="Temperature" value={formatCommandCount(stats.temperatureCommands)} />
                <GcodeValueField label="Move commands" value={formatCommandCount(stats.movementCommands)} />
                <GcodeValueField label="Prime moves" value={formatCount(stats.primeMoves)} />
              </FileSheetFieldGrid>
            </FileSheetSectionBody>
      )
    },
    {
      id: "bounds",
      title: "Bounds",
      content: (
            <FileSheetSectionBody>
              <FileSheetFieldGrid columns={1}>
                <GcodeValueField label="X" value={boundsAxisText(gcodeData?.bounds, 0, 1)} mono />
                <GcodeValueField label="Y" value={boundsAxisText(gcodeData?.bounds, 1, 1)} mono />
                <GcodeValueField label="Z" value={boundsAxisText(gcodeData?.bounds, 2, 2)} mono />
              </FileSheetFieldGrid>
            </FileSheetSectionBody>
      )
    },
    ...themeTabs
  ];

  return (
    <FileSheet
      open={open}
      title="G-code"
      isDesktop={isDesktop}
      width={width}
      onOpenChange={onOpenChange}
      onStartResize={onStartResize}
      scrollBody={false}
    >
      <FileSheetTabbedSurface
        kind="gcode"
        sections={sections}
        openSectionIds={openSectionIds}
        onOpenSectionIdsChange={onOpenSectionIdsChange}
      />
    </FileSheet>
  );
}
