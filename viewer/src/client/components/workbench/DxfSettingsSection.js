import { Slider } from "@/components/ui/slider";

import {
  FILE_SHEET_PRECISION_SLIDER_CLASSES,
  FileSheetInlineControlRow,
  FileSheetSectionBody,
  FileSheetSegmentedControl,
  FileSheetSliderField,
  FileSheetSubsection
} from "./FileSheet";
import { FILE_SHEET_SECTION_IDS } from "@/workbench/fileSheetSections";

/**
 * A drawing's own settings, per viewer/docs/settings-ui.md.
 *
 * Everything here is a RENDER-TIME parameter, never a bake setting. The package caches one
 * prism at a reference thickness (previewGlb.js), and these reshape it in the viewport:
 * thickness is a scale along the sweep axis, exact because a flat pattern is a profile swept
 * perpendicular. Nothing set here can make a cached package stale, which is the property
 * that lets them be sliders rather than rebuild buttons.
 *
 * Values live in workspace session state — not persistence, not __cadgen__. They describe
 * how the drawing open right now is being looked at.
 *
 * Two tabs, not two sections of one: thickness is a property of the MATERIAL the profile is
 * cut from and applies to every drawing, while bends are a property of THIS drawing's
 * geometry and most drawings have none. A tab that is empty for most files does not earn a
 * permanent place beside one that is always relevant.
 */

/** Sheet-metal thicknesses a drawing plausibly gets cut at, in millimetres. */
export const DXF_THICKNESS_MIN_MM = 0.1;
export const DXF_THICKNESS_MAX_MM = 25;
export const DXF_THICKNESS_STEP_MM = 0.1;
export const DXF_DEFAULT_THICKNESS_MM = 2;

export const DXF_BEND_ANGLE_MIN_DEG = 0;
export const DXF_BEND_ANGLE_MAX_DEG = 180;
export const DXF_BEND_ANGLE_STEP_DEG = 1;
export const DXF_DEFAULT_BEND_ANGLE_DEG = 90;

export const DXF_BEND_DIRECTIONS = Object.freeze(["up", "down"]);

export function normalizeDxfThicknessMm(value, fallback = DXF_DEFAULT_THICKNESS_MM) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
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

export function DxfMaterialSection({ thicknessMm = DXF_DEFAULT_THICKNESS_MM, onThicknessChange }) {
  const thickness = normalizeDxfThicknessMm(thicknessMm);
  const commit = (next) => onThicknessChange?.(normalizeDxfThicknessMm(next, thickness));

  return (
    <FileSheetSectionBody>
      <FileSheetSubsection title="Material">
        <FileSheetSliderField
          label="Thickness"
          value={`${thickness.toFixed(1)} mm`}
          onValueCommit={commit}
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
            onValueChange={([next]) => commit(next)}
          />
        </FileSheetSliderField>
      </FileSheetSubsection>
    </FileSheetSectionBody>
  );
}

export function DxfBendsSection({
  bendLineCount = 0,
  bendAngleDeg = DXF_DEFAULT_BEND_ANGLE_DEG,
  onBendAngleChange,
  bendDirection = "up",
  onBendDirectionChange
}) {
  const angle = normalizeDxfBendAngleDeg(bendAngleDeg);
  const direction = normalizeDxfBendDirection(bendDirection);
  const commitAngle = (next) => onBendAngleChange?.(normalizeDxfBendAngleDeg(next, angle));

  return (
    <FileSheetSectionBody>
      <FileSheetSubsection title="Bends">
        {/* A read-only count is an inline row like any other — the guide's "read-only value"
            case — rather than a sentence explaining what a bend line is. */}
        <FileSheetInlineControlRow label="Bend lines" value={String(bendLineCount)} />
        <FileSheetSliderField
          label="Angle"
          value={`${Math.round(angle)}°`}
          onValueCommit={commitAngle}
          valueInputProps={{
            ariaLabel: "Angle value",
            min: DXF_BEND_ANGLE_MIN_DEG,
            max: DXF_BEND_ANGLE_MAX_DEG
          }}
        >
          <Slider
            aria-label="Angle"
            className={FILE_SHEET_PRECISION_SLIDER_CLASSES}
            value={[angle]}
            min={DXF_BEND_ANGLE_MIN_DEG}
            max={DXF_BEND_ANGLE_MAX_DEG}
            step={DXF_BEND_ANGLE_STEP_DEG}
            onValueChange={([next]) => commitAngle(next)}
          />
        </FileSheetSliderField>
        {/* The one sanctioned segmented control in the panel: two short single words, which
            the guide names explicitly as the DXF bend direction case. */}
        <FileSheetInlineControlRow
          label="Direction"
          trailing={(
            <FileSheetSegmentedControl
              fit
              ariaLabel="Bend direction"
              value={direction}
              onChange={(next) => onBendDirectionChange?.(normalizeDxfBendDirection(next, direction))}
              options={[
                { value: "up", label: "Up" },
                { value: "down", label: "Down" }
              ]}
            />
          )}
        />
      </FileSheetSubsection>
    </FileSheetSectionBody>
  );
}

export function buildDxfMaterialTab(props) {
  return {
    id: FILE_SHEET_SECTION_IDS.DXF_MATERIAL,
    title: "Material",
    content: <DxfMaterialSection {...props} />
  };
}

/** Only when the drawing HAS bend lines: a tab that is empty for most files does not earn a
 *  permanent place in the strip. */
export function buildDxfBendsTab(props) {
  if (!(props?.bendLineCount > 0)) {
    return null;
  }
  return {
    id: FILE_SHEET_SECTION_IDS.DXF_BENDS,
    title: "Bends",
    content: <DxfBendsSection {...props} />
  };
}
