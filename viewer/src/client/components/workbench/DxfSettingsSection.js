import { ArrowDown, ArrowUp, RotateCcw, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";

import {
  FILE_SHEET_COMPACT_BUTTON_CLASSES,
  FILE_SHEET_PRECISION_SLIDER_CLASSES,
  FileSheetButtonRow,
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
 * The DXF settings tabs, per viewer/docs/settings-ui.md: Material (units + stock) and
 * Bends (fold style + per-bend controls), each a tab of its own with its own Reset.
 *
 * Everything here is a RENDER-TIME parameter. Thickness and boxed bends reshape the cached
 * prism; curved bends re-mesh live from the package's cached contours (geometry.json).
 * Nothing set here can invalidate a package. All dimensional state is kept in MILLIMETRES;
 * the Units setting converts what the inputs display and accept — it sits first because it
 * reframes every dimensional row under it.
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

/** Curved wraps the surface around each bend like real sheet metal — the default, because
 *  the preview should look like the part; Boxed is the mitered fold for a schematic look. */
export const DXF_BEND_STYLES = Object.freeze(["boxed", "curved"]);
export const DXF_DEFAULT_BEND_STYLE = "curved";

/** Inside bend radius in mm; 0 means "auto" (the mesher's visual default, 0.6x thickness). */
export const DXF_BEND_RADIUS_MAX_MM = 20;
export const DXF_DEFAULT_BEND_RADIUS_MM = 0;

/** Where the neutral axis sits within the thickness. 0.44 is the common air-bend value;
 *  0.5 (mid-thickness) is the visual default this preview always used. */
export const DXF_KFACTOR_MIN = 0.1;
export const DXF_KFACTOR_MAX = 0.9;
export const DXF_DEFAULT_KFACTOR = 0.5;

/**
 * The unit the sheet's dimensional inputs display and accept. State stays millimetres —
 * this converts at the input boundary only, so switching units never changes the part.
 */
export const DXF_UNIT_OPTIONS = Object.freeze([
  { value: "mm", label: "Millimetres", mmPerUnit: 1, decimals: 1, sliderStep: 0.1 },
  { value: "cm", label: "Centimetres", mmPerUnit: 10, decimals: 2, sliderStep: 0.01 },
  { value: "in", label: "Inches", mmPerUnit: 25.4, decimals: 2, sliderStep: 0.01 },
  { value: "m", label: "Metres", mmPerUnit: 1000, decimals: 4, sliderStep: 0.0001 }
]);
export const DXF_DEFAULT_UNITS = "mm";

export function normalizeDxfUnits(value, fallback = DXF_DEFAULT_UNITS) {
  const text = String(value || "").trim().toLowerCase();
  return DXF_UNIT_OPTIONS.some((option) => option.value === text) ? text : fallback;
}

function unitOption(units) {
  return DXF_UNIT_OPTIONS.find((option) => option.value === normalizeDxfUnits(units))
    || DXF_UNIT_OPTIONS[0];
}

function displayLength(mm, option) {
  return (Number(mm) || 0) / option.mmPerUnit;
}

function formatLength(mm, option) {
  return `${displayLength(mm, option).toFixed(option.decimals)} ${option.value}`;
}

/** Parse a typed length in the active unit back to millimetres. */
function parseLengthToMm(value, option, fallbackMm) {
  const numeric = Number(String(value ?? "").replace(new RegExp(`\\s*${option.value}\\s*$`), ""));
  return Number.isFinite(numeric) ? numeric * option.mmPerUnit : fallbackMm;
}

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

/** Model orientation as quarter-turns about each world axis, applied after the fold. A
 *  folded part often lands facing the wrong way (a U opening down, a flange toward the
 *  camera); quarter-turns re-seat it without free-rotation fiddliness. */
export const DXF_DEFAULT_ORIENTATION = Object.freeze({ x: 0, y: 0, z: 0 });

export function normalizeDxfOrientation(value) {
  const quarter = (component) => {
    const numeric = Math.trunc(Number(component));
    return Number.isFinite(numeric) ? ((numeric % 4) + 4) % 4 : 0;
  };
  return {
    x: quarter(value?.x),
    y: quarter(value?.y),
    z: quarter(value?.z)
  };
}

/** The tab-footer Reset (settings-ui.md: outline + RotateCcw, full row, one per tab). */
function DxfResetRow({ label, onReset }) {
  if (!onReset) {
    return null;
  }
  return (
    <FileSheetControlRow label={null}>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className={`${FILE_SHEET_COMPACT_BUTTON_CLASSES} w-full justify-center`}
        onClick={() => onReset()}
        aria-label={label}
        title="Reset"
      >
        <RotateCcw className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
        <span>Reset</span>
      </Button>
    </FileSheetControlRow>
  );
}

export function DxfMaterialSettings({
  thicknessMm = DXF_DEFAULT_THICKNESS_MM,
  onThicknessChange,
  units = DXF_DEFAULT_UNITS,
  onUnitsChange,
  onReset
}) {
  const activeUnits = normalizeDxfUnits(units);
  const unit = unitOption(activeUnits);
  const thickness = normalizeDxfThicknessMm(thicknessMm);
  const commitThickness = (next) => onThicknessChange?.(
    normalizeDxfThicknessMm(parseLengthToMm(next, unit, thickness), thickness)
  );

  return (
    <FileSheetSectionBody>
      <FileSheetSubsection>
        {/* Units first: it reframes every dimensional row below it. */}
        <FileSheetSelectRow
          label="Units"
          value={activeUnits}
          onValueChange={(next) => onUnitsChange?.(normalizeDxfUnits(next, activeUnits))}
          options={DXF_UNIT_OPTIONS.map(({ value, label }) => ({ value, label }))}
        />
        <FileSheetSliderField
          label="Thickness"
          value={formatLength(thickness, unit)}
          onValueCommit={commitThickness}
          valueInputProps={{
            ariaLabel: "Thickness value",
            min: 0,
            max: displayLength(DXF_THICKNESS_MAX_MM, unit)
          }}
        >
          <Slider
            aria-label="Thickness"
            className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
            value={[displayLength(thickness, unit)]}
            min={0}
            max={displayLength(DXF_THICKNESS_MAX_MM, unit)}
            step={unit.sliderStep}
            onValueChange={([next]) => onThicknessChange?.(
              normalizeDxfThicknessMm(next * unit.mmPerUnit, thickness)
            )}
          />
        </FileSheetSliderField>
      </FileSheetSubsection>
      <DxfResetRow label="Reset material settings" onReset={onReset} />
    </FileSheetSectionBody>
  );
}

export function DxfBendsSettings({
  bends = [],
  onBendChange,
  bendStyle = DXF_DEFAULT_BEND_STYLE,
  onBendStyleChange,
  bendRadiusMm = DXF_DEFAULT_BEND_RADIUS_MM,
  onBendRadiusChange,
  kFactor = DXF_DEFAULT_KFACTOR,
  onKFactorChange,
  units = DXF_DEFAULT_UNITS,
  onRotateOrientation,
  onReset
}) {
  const style = normalizeDxfBendStyle(bendStyle);
  const radius = normalizeDxfBendRadiusMm(bendRadiusMm);
  const neutralK = normalizeDxfKFactor(kFactor);
  const unit = unitOption(units);

  return (
    <FileSheetSectionBody>
      <FileSheetSubsection title="Style">
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
            value={radius > 0 ? formatLength(radius, unit) : "Auto"}
            onValueCommit={(next) => onBendRadiusChange?.(
              normalizeDxfBendRadiusMm(parseLengthToMm(next, unit, radius), radius)
            )}
            valueInputProps={{ ariaLabel: "Bend radius value", className: "w-16" }}
          >
            <Slider
              aria-label="Bend radius"
              className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
              value={[displayLength(radius, unit)]}
              min={0}
              max={displayLength(DXF_BEND_RADIUS_MAX_MM, unit)}
              step={unit.sliderStep}
              onValueChange={([next]) => onBendRadiusChange?.(
                normalizeDxfBendRadiusMm(next * unit.mmPerUnit, radius)
              )}
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
      </FileSheetSubsection>

      {/* One row per bend: the item label IS the slider label, direction rides inline
          beside the value box (settings-ui.md "Repeated item groups", single-row form). */}
      <FileSheetSubsection title="Bends">
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

      {/* Model orientation: a folded part often lands facing the wrong way. Sibling
          actions form a button row (equal columns, icon + label, no row label); each click
          turns the model 90 degrees about that world axis. */}
      {onRotateOrientation ? (
        <FileSheetSubsection title="Orientation">
          <FileSheetButtonRow columns={3}>
            {["x", "y", "z"].map((axis) => (
              <Button
                key={axis}
                type="button"
                variant="outline"
                size="sm"
                className={`${FILE_SHEET_COMPACT_BUTTON_CLASSES} justify-center`}
                aria-label={`Rotate model 90 degrees about ${axis.toUpperCase()}`}
                title={`Rotate 90° about ${axis.toUpperCase()}`}
                onClick={() => onRotateOrientation(axis)}
              >
                <RotateCw className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                <span>{axis.toUpperCase()} 90°</span>
              </Button>
            ))}
          </FileSheetButtonRow>
        </FileSheetSubsection>
      ) : null}

      <DxfResetRow label="Reset bend settings" onReset={onReset} />
    </FileSheetSectionBody>
  );
}

export function buildDxfMaterialTab(props) {
  return {
    id: FILE_SHEET_SECTION_IDS.DXF_MATERIAL,
    title: "Material",
    content: <DxfMaterialSettings {...props} />
  };
}

export function buildDxfBendsTab(props) {
  return {
    id: FILE_SHEET_SECTION_IDS.DXF_BENDS,
    title: "Bends",
    content: <DxfBendsSettings {...props} />
  };
}
