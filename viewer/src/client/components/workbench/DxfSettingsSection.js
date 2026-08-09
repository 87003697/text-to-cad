import { ArrowDown, ArrowUp, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";

import {
  FILE_SHEET_COMPACT_BUTTON_CLASSES,
  FILE_SHEET_PRECISION_SLIDER_CLASSES,
  FileSheetControlRow,
  FileSheetSectionBody,
  FileSheetSegmentedControl,
  FileSheetSelectRow,
  FileSheetSliderField,
  FileSheetSubsection,
  FileSheetValueInput
} from "./FileSheet";
import { FILE_SHEET_SECTION_IDS } from "@/workbench/fileSheetSections";

/**
 * A drawing's settings, per viewer/docs/settings-ui.md: ONE surface, Material on top and
 * Bends below it when the drawing has any. They were separate tabs once; a tab you have to
 * switch to hides half of a two-group panel behind a click for no layout win.
 *
 * Everything here is a RENDER-TIME parameter. Thickness and sharp bends reshape the cached
 * prism; curved bends re-mesh live from the package's cached contours (geometry.json).
 * Nothing set here can invalidate a package.
 */

export const DXF_THICKNESS_MIN_MM = 0;
export const DXF_THICKNESS_MAX_MM = 25;
export const DXF_THICKNESS_STEP_MM = 0.1;
export const DXF_DEFAULT_THICKNESS_MM = 0;

export const DXF_BEND_ANGLE_MIN_DEG = 0;
export const DXF_BEND_ANGLE_MAX_DEG = 180;
export const DXF_BEND_ANGLE_STEP_DEG = 1;
/** Zero: a flat pattern IS flat, and the dashed bend lines already say where it can fold. */
export const DXF_DEFAULT_BEND_ANGLE_DEG = 0;

export const DXF_BEND_DIRECTIONS = Object.freeze(["up", "down"]);

/** Boxed is the mitered fold — crisp square corners, the schematic default; Curved wraps
 *  the surface around each bend like real sheet metal. */
export const DXF_BEND_STYLES = Object.freeze(["boxed", "curved"]);
export const DXF_DEFAULT_BEND_STYLE = "boxed";

export function normalizeDxfThicknessMm(value, fallback = DXF_DEFAULT_THICKNESS_MM) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < 0) {
    return fallback;
  }
  return Math.min(DXF_THICKNESS_MAX_MM, Math.max(DXF_THICKNESS_MIN_MM, numeric));
}

export function normalizeDxfBendAngleDeg(value, fallback = DXF_DEFAULT_BEND_ANGLE_DEG) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return fallback;
  }
  return Math.min(DXF_BEND_ANGLE_MAX_DEG, Math.max(DXF_BEND_ANGLE_MIN_DEG, numeric));
}

export function normalizeDxfBendDirection(value, fallback = "up") {
  const text = String(value || "").trim().toLowerCase();
  return DXF_BEND_DIRECTIONS.includes(text) ? text : fallback;
}

export function normalizeDxfBendStyle(value, fallback = DXF_DEFAULT_BEND_STYLE) {
  const text = String(value || "").trim().toLowerCase();
  return DXF_BEND_STYLES.includes(text) ? text : fallback;
}

/** Inside bend radius in mm; 0 means "auto" (the mesher's visual default, 0.6x thickness). */
export const DXF_BEND_RADIUS_MAX_MM = 20;
export const DXF_DEFAULT_BEND_RADIUS_MM = 0;

/** Where the neutral axis sits within the thickness. 0.44 is the common air-bend value;
 *  0.5 (mid-thickness) is the visual default this preview always used. */
export const DXF_KFACTOR_MIN = 0.1;
export const DXF_KFACTOR_MAX = 0.9;
export const DXF_DEFAULT_KFACTOR = 0.5;

export function normalizeDxfBendRadiusMm(value, fallback = DXF_DEFAULT_BEND_RADIUS_MM) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < 0) {
    return fallback;
  }
  return Math.min(DXF_BEND_RADIUS_MAX_MM, numeric);
}

export function normalizeDxfKFactor(value, fallback = DXF_DEFAULT_KFACTOR) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return fallback;
  }
  return Math.min(DXF_KFACTOR_MAX, Math.max(DXF_KFACTOR_MIN, numeric));
}

/** Drawing-unit interpretation. "auto" trusts the file's $INSUNITS; picking a unit
 *  reinterprets the drawing's coordinates in that unit — the fix for the common file that
 *  says nothing (or lies) about its units. */
export const DXF_UNIT_OPTIONS = Object.freeze([
  { value: "auto", label: "Auto", mmPerUnit: null },
  { value: "mm", label: "Millimetres", mmPerUnit: 1 },
  { value: "in", label: "Inches", mmPerUnit: 25.4 },
  { value: "cm", label: "Centimetres", mmPerUnit: 10 },
  { value: "m", label: "Metres", mmPerUnit: 1000 }
]);
export const DXF_DEFAULT_UNITS = "auto";

export function normalizeDxfUnits(value, fallback = DXF_DEFAULT_UNITS) {
  const text = String(value || "").trim().toLowerCase();
  return DXF_UNIT_OPTIONS.some((option) => option.value === text) ? text : fallback;
}

/** How much to scale the drawing's plan, given the file's own mm-per-unit. Auto is 1 (the
 *  parse already applied the file's units); an override rescales relative to them. */
export function dxfUnitsPlanScale(units, fileUnitsScaleMm) {
  const option = DXF_UNIT_OPTIONS.find((candidate) => candidate.value === normalizeDxfUnits(units));
  if (!option || option.mmPerUnit === null) {
    return 1;
  }
  const fileScale = Number(fileUnitsScaleMm);
  return option.mmPerUnit / (Number.isFinite(fileScale) && fileScale > 0 ? fileScale : 1);
}

function fileUnitsShortLabel(fileUnitsScaleMm) {
  const scale = Number(fileUnitsScaleMm);
  const match = DXF_UNIT_OPTIONS.find(
    (option) => option.mmPerUnit !== null && Math.abs(option.mmPerUnit - scale) < 1e-9
  );
  return match ? match.value : "mm";
}

export function DxfDrawingSettings({
  thicknessMm = DXF_DEFAULT_THICKNESS_MM,
  onThicknessChange,
  units = DXF_DEFAULT_UNITS,
  onUnitsChange,
  fileUnitsScaleMm = 1,
  bends = [],
  onBendChange,
  bendStyle = DXF_DEFAULT_BEND_STYLE,
  onBendStyleChange,
  bendRadiusMm = DXF_DEFAULT_BEND_RADIUS_MM,
  onBendRadiusChange,
  kFactor = DXF_DEFAULT_KFACTOR,
  onKFactorChange,
  onReset
}) {
  const thickness = normalizeDxfThicknessMm(thicknessMm);
  const commitThickness = (next) => onThicknessChange?.(normalizeDxfThicknessMm(next, thickness));
  const style = normalizeDxfBendStyle(bendStyle);
  const radius = normalizeDxfBendRadiusMm(bendRadiusMm);
  const neutralK = normalizeDxfKFactor(kFactor);
  const activeUnits = normalizeDxfUnits(units);

  return (
    <FileSheetSectionBody>
      <FileSheetSubsection title="Material">
        <FileSheetSliderField
          label="Thickness"
          value={`${thickness.toFixed(1)} mm`}
          onValueCommit={commitThickness}
          valueInputProps={{
            ariaLabel: "Thickness value",
            min: DXF_THICKNESS_MIN_MM,
            max: DXF_THICKNESS_MAX_MM
          }}
        >
          <Slider
            aria-label="Thickness"
            className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
            value={[thickness]}
            min={DXF_THICKNESS_MIN_MM}
            max={DXF_THICKNESS_MAX_MM}
            step={DXF_THICKNESS_STEP_MM}
            onValueChange={([next]) => commitThickness(next)}
          />
        </FileSheetSliderField>
        {/* Units reinterpret the drawing's coordinates; Auto trusts the file's $INSUNITS
            (shown so the user can see what the file claims). */}
        <FileSheetSelectRow
          label="Units"
          value={activeUnits}
          onValueChange={(next) => onUnitsChange?.(normalizeDxfUnits(next, activeUnits))}
          options={DXF_UNIT_OPTIONS.map((option) => (option.value === "auto"
            ? { ...option, label: `Auto (${fileUnitsShortLabel(fileUnitsScaleMm)})` }
            : option))}
        />
      </FileSheetSubsection>

      {bends.length > 0 ? (
        <FileSheetSubsection title="Bends">
          <FileSheetSelectRow
            label="Style"
            value={style}
            onValueChange={(next) => onBendStyleChange?.(normalizeDxfBendStyle(next, bendStyle))}
            options={[
              { value: "boxed", label: "Boxed" },
              { value: "curved", label: "Curved" }
            ]}
          />
          {/* Sheet-metal bend geometry only means anything when the surface actually
              curves; Boxed is a schematic fold with no radius to size. */}
          {style === "curved" ? (
            <FileSheetSliderField
              label="Radius"
              value={radius > 0 ? `${radius.toFixed(1)} mm` : "Auto"}
              onValueCommit={(next) => onBendRadiusChange?.(normalizeDxfBendRadiusMm(next, radius))}
              valueInputProps={{ ariaLabel: "Bend radius value", className: "w-16" }}
            >
              <Slider
                aria-label="Bend radius"
                className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
                value={[radius]}
                min={0}
                max={DXF_BEND_RADIUS_MAX_MM}
                step={0.5}
                onValueChange={([next]) => onBendRadiusChange?.(normalizeDxfBendRadiusMm(next, radius))}
              />
            </FileSheetSliderField>
          ) : null}
          {style === "curved" ? (
            <FileSheetSliderField
              label="K-factor"
              value={neutralK.toFixed(2)}
              onValueCommit={(next) => onKFactorChange?.(normalizeDxfKFactor(next, neutralK))}
              valueInputProps={{ ariaLabel: "K-factor value", className: "w-16" }}
            >
              <Slider
                aria-label="K-factor"
                className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
                value={[neutralK]}
                min={DXF_KFACTOR_MIN}
                max={DXF_KFACTOR_MAX}
                step={0.01}
                onValueChange={([next]) => onKFactorChange?.(normalizeDxfKFactor(next, neutralK))}
              />
            </FileSheetSliderField>
          ) : null}
          {/* One row per bend: the item label IS the slider label, direction rides inline
              beside the value box (settings-ui.md "Repeated item groups", single-row form). */}
          {bends.map((bend, index) => {
            const angle = normalizeDxfBendAngleDeg(bend?.angleDeg);
            const direction = normalizeDxfBendDirection(bend?.direction);
            const commitAngle = (next) => onBendChange?.(index, {
              angleDeg: normalizeDxfBendAngleDeg(next, angle)
            });
            return (
              <FileSheetSliderField
                key={index}
                label={`Bend ${index + 1}`}
                value={`${Math.round(angle)}°`}
                trailing={(
                  <div className="flex shrink-0 items-center gap-1.5">
                    <FileSheetValueInput
                      ariaLabel={`Bend ${index + 1} angle value`}
                      value={`${Math.round(angle)}°`}
                      onValueCommit={commitAngle}
                      className="w-12"
                    />
                    <FileSheetSegmentedControl
                      fit
                      ariaLabel={`Bend ${index + 1} direction`}
                      value={direction}
                      onChange={(next) => onBendChange?.(index, {
                        direction: normalizeDxfBendDirection(next, direction)
                      })}
                      options={[
                        { value: "up", label: "Up", title: "Bend up", Icon: ArrowUp, iconOnly: true },
                        { value: "down", label: "Down", title: "Bend down", Icon: ArrowDown, iconOnly: true }
                      ]}
                    />
                  </div>
                )}
              >
                <Slider
                  aria-label={`Bend ${index + 1} angle`}
                  className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
                  value={[angle]}
                  min={DXF_BEND_ANGLE_MIN_DEG}
                  max={DXF_BEND_ANGLE_MAX_DEG}
                  step={DXF_BEND_ANGLE_STEP_DEG}
                  onValueChange={([next]) => commitAngle(next)}
                />
              </FileSheetSliderField>
            );
          })}
        </FileSheetSubsection>
      ) : null}

      {/* One Reset per tab (settings-ui.md): full-width outline + RotateCcw as the last
          row, restoring material AND bends to their defaults. Layer visibility lives in
          the Layers tab and resets with everything else. */}
      {onReset ? (
        <FileSheetControlRow label={null}>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={`${FILE_SHEET_COMPACT_BUTTON_CLASSES} w-full justify-center`}
            onClick={() => onReset()}
            aria-label="Reset drawing settings"
            title="Reset"
          >
            <RotateCcw className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
            <span>Reset</span>
          </Button>
        </FileSheetControlRow>
      ) : null}
    </FileSheetSectionBody>
  );
}

export function buildDxfDrawingTab(props) {
  return {
    id: FILE_SHEET_SECTION_IDS.DXF_SETTINGS,
    title: "DXF",
    content: <DxfDrawingSettings {...props} />
  };
}
