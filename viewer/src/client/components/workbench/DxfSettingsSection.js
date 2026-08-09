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

/** Sheet-metal thicknesses a drawing plausibly gets cut at, in millimetres.
 *
 * Zero is a real setting, not a degenerate one: a drawing IS a 2D profile, so the honest
 * default is the face with no material behind it. The renderer collapses the prism to a
 * sheet at 0 rather than to nothing (see CadViewer's thickness effect). */
export const DXF_THICKNESS_MIN_MM = 0;
export const DXF_THICKNESS_MAX_MM = 25;
export const DXF_THICKNESS_STEP_MM = 0.1;
export const DXF_DEFAULT_THICKNESS_MM = 0;

export const DXF_BEND_ANGLE_MIN_DEG = 0;
export const DXF_BEND_ANGLE_MAX_DEG = 180;
export const DXF_BEND_ANGLE_STEP_DEG = 1;
export const DXF_DEFAULT_BEND_ANGLE_DEG = 90;

export const DXF_BEND_DIRECTIONS = Object.freeze(["up", "down"]);

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

export function DxfBendsSection({ bends = [], onBendChange }) {
  return (
    <FileSheetSectionBody>
      {/* One section per bend, in axis order — "the bend angle" stopped being a single
          setting the moment a drawing had two bends that want different angles. Sections
          never nest (settings-ui.md): the tab body is the flat list Bend 1, Bend 2, ... */}
      {bends.map((bend, index) => {
        const angle = normalizeDxfBendAngleDeg(bend?.angleDeg);
        const direction = normalizeDxfBendDirection(bend?.direction);
        const commitAngle = (next) => onBendChange?.(index, {
          angleDeg: normalizeDxfBendAngleDeg(next, angle)
        });
        return (
          <FileSheetSubsection title={`Bend ${index + 1}`} key={index}>
            <FileSheetSliderField
              label="Angle"
              value={`${Math.round(angle)}°`}
              onValueCommit={commitAngle}
              valueInputProps={{
                ariaLabel: `Bend ${index + 1} angle value`,
                min: DXF_BEND_ANGLE_MIN_DEG,
                max: DXF_BEND_ANGLE_MAX_DEG
              }}
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
            <FileSheetInlineControlRow label="Direction">
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
            </FileSheetInlineControlRow>
          </FileSheetSubsection>
        );
      })}
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
  if (!(props?.bends?.length > 0)) {
    return null;
  }
  return {
    id: FILE_SHEET_SECTION_IDS.DXF_BENDS,
    title: "Bends",
    content: <DxfBendsSection {...props} />
  };
}
