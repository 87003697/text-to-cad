import { Slider } from "@/components/ui/slider";

import {
  FILE_SHEET_PRECISION_SLIDER_CLASSES,
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

/** Curved is how sheet metal actually bends; Sharp is the mitered fold for a schematic
 *  look. Curved is the default because the preview should look like the part. */
export const DXF_BEND_STYLES = Object.freeze(["curved", "sharp"]);
export const DXF_DEFAULT_BEND_STYLE = "curved";

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

export function DxfDrawingSettings({
  thicknessMm = DXF_DEFAULT_THICKNESS_MM,
  onThicknessChange,
  bends = [],
  onBendChange,
  bendStyle = DXF_DEFAULT_BEND_STYLE,
  onBendStyleChange
}) {
  const thickness = normalizeDxfThicknessMm(thicknessMm);
  const commitThickness = (next) => onThicknessChange?.(normalizeDxfThicknessMm(next, thickness));

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
      </FileSheetSubsection>

      {bends.length > 0 ? (
        <FileSheetSubsection title="Bends">
          <FileSheetSelectRow
            label="Style"
            value={normalizeDxfBendStyle(bendStyle)}
            onValueChange={(next) => onBendStyleChange?.(normalizeDxfBendStyle(next, bendStyle))}
            options={[
              { value: "curved", label: "Curved" },
              { value: "sharp", label: "Sharp" }
            ]}
          />
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
                    />
                    <FileSheetSegmentedControl
                      fit
                      ariaLabel={`Bend ${index + 1} direction`}
                      value={direction}
                      onChange={(next) => onBendChange?.(index, {
                        direction: normalizeDxfBendDirection(next, direction)
                      })}
                      options={[
                        { value: "up", label: "Up" },
                        { value: "down", label: "Down" }
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
    </FileSheetSectionBody>
  );
}

export function buildDxfDrawingTab(props) {
  return {
    id: FILE_SHEET_SECTION_IDS.DXF_SETTINGS,
    title: "Drawing",
    content: <DxfDrawingSettings {...props} />
  };
}
