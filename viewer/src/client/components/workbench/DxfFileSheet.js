import { useEffect, useState } from "react";
import { Minus, Plus } from "lucide-react";
import {
  DXF_BEND_DIRECTION,
  normalizeDxfBendAngleDeg,
  normalizeDxfBendDirection,
  normalizeDxfPreviewThicknessMm
} from "cadjs/lib/dxf/buildPreviewMesh";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import FileSheet, {
  FILE_SHEET_COMPACT_ICON_BUTTON_CLASSES,
  FILE_SHEET_COMPACT_NUMERIC_INPUT_CLASSES,
  FILE_SHEET_UNIT_SUFFIX_CLASSES,
  FileSheetControlRow,
  FileSheetSegmentedControl,
  FileSheetStatusText,
  FileSheetSubsection
} from "./FileSheet";
import FileSheetTabbedSurface from "./FileSheetTabbedSurface";
import { buildFileStatusTab } from "./FileStatusSection";

const compactInputClasses = FILE_SHEET_COMPACT_NUMERIC_INPUT_CLASSES;
const compactIconButtonClasses = FILE_SHEET_COMPACT_ICON_BUTTON_CLASSES;

function formatThicknessInput(valueMm) {
  const numericValue = Number(valueMm);
  if (!Number.isFinite(numericValue)) {
    return "";
  }
  return numericValue.toFixed(4).replace(/\.?0+$/, "");
}

function formatAngleInput(valueDeg) {
  const rounded = Math.round(Number(valueDeg) * 10) / 10;
  return Number.isFinite(rounded) ? String(rounded) : "";
}

function DxfBendRow({
  index,
  setting,
  onChange
}) {
  const [draftAngle, setDraftAngle] = useState(() => formatAngleInput(setting?.angleDeg));

  useEffect(() => {
    setDraftAngle(formatAngleInput(setting?.angleDeg));
  }, [setting?.angleDeg]);

  const commitAngle = (nextValue) => {
    const normalizedAngle = normalizeDxfBendAngleDeg(nextValue, setting?.angleDeg);
    onChange(index, { angleDeg: normalizedAngle });
    setDraftAngle(formatAngleInput(normalizedAngle));
  };

  const direction = normalizeDxfBendDirection(setting?.direction);

  return (
    <FileSheetControlRow label={`B${index + 1}`}>
      <div className="grid grid-cols-[minmax(0,1fr)_5.75rem] gap-2">
        <FileSheetSegmentedControl
          value={direction}
          onChange={(nextDirection) => {
            onChange(index, { direction: nextDirection });
          }}
          ariaLabel={`Bend ${index + 1} direction`}
          options={[
            { value: DXF_BEND_DIRECTION.UP, label: "Up" },
            { value: DXF_BEND_DIRECTION.DOWN, label: "Down" }
          ]}
        />
        <label className="block">
          <span className="sr-only">Angle</span>
          <div className="relative">
            <Input
              type="number"
              min="0"
              max="180"
              step="1"
              inputMode="decimal"
              value={draftAngle}
              onChange={(event) => {
                setDraftAngle(event.target.value);
              }}
              onFocus={(event) => {
                event.currentTarget.select();
              }}
              onMouseUp={(event) => {
                event.preventDefault();
              }}
              onBlur={() => {
                commitAngle(draftAngle);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.currentTarget.blur();
                }
              }}
              className={`${compactInputClasses} w-full pr-7 text-right`}
              aria-label={`Bend ${index + 1} angle in degrees`}
            />
            <span className={FILE_SHEET_UNIT_SUFFIX_CLASSES}>°</span>
          </div>
        </label>
      </div>
    </FileSheetControlRow>
  );
}

export default function DxfFileSheet({
  open,
  isDesktop,
  width,
  selectedEntry = null,
  onOpenChange,
  valueMm,
  bendSettings,
  hasDxfData,
  viewerLoading,
  onStartResize,
  onThicknessChange,
  onBendChange,
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
  const [draftValue, setDraftValue] = useState(() => formatThicknessInput(valueMm));
  const normalizedBendSettings = Array.isArray(bendSettings) ? bendSettings : [];

  useEffect(() => {
    setDraftValue(formatThicknessInput(valueMm));
  }, [valueMm]);

  const commitValue = (nextValue) => {
    const normalizedValue = normalizeDxfPreviewThicknessMm(nextValue, valueMm);
    onThicknessChange(normalizedValue);
    setDraftValue(formatThicknessInput(normalizedValue));
  };

  const sections = [
    buildFileStatusTab(statusItems),
    {
      id: "dxf",
      title: "DXF",
      content: (
          <div className="py-2">
            <FileSheetSubsection title="Material">
              <FileSheetControlRow label="Thickness">
                <div className="grid grid-cols-[2rem_minmax(0,1fr)_2rem] items-center gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className={compactIconButtonClasses}
                    onClick={() => {
                      commitValue(valueMm - 0.25);
                    }}
                    disabled={!hasDxfData}
                    aria-label="Reduce DXF material thickness"
                    title="Reduce thickness"
                  >
                    <Minus className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                  </Button>
                  <div className="relative block">
                    <Input
                      type="number"
                      min="0.2"
                      max="25"
                      step="any"
                      inputMode="decimal"
                      value={draftValue}
                      disabled={!hasDxfData}
                      onChange={(event) => {
                        setDraftValue(event.target.value);
                      }}
                      onBlur={() => {
                        commitValue(draftValue);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.currentTarget.blur();
                        }
                      }}
                      className={`${compactInputClasses} w-full pr-9 text-right`}
                      aria-label="DXF material thickness in millimeters"
                    />
                    <span className={FILE_SHEET_UNIT_SUFFIX_CLASSES}>mm</span>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className={compactIconButtonClasses}
                    onClick={() => {
                      commitValue(valueMm + 0.25);
                    }}
                    disabled={!hasDxfData}
                    aria-label="Increase DXF material thickness"
                    title="Increase thickness"
                  >
                    <Plus className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                  </Button>
                </div>
              </FileSheetControlRow>
            </FileSheetSubsection>
            <FileSheetSubsection title="Bends">
              {normalizedBendSettings.length ? normalizedBendSettings.map((setting, index) => (
                <DxfBendRow
                  key={setting.id || `bend-${index + 1}`}
                  index={index}
                  setting={setting}
                  onChange={onBendChange}
                />
              )) : (
                <FileSheetStatusText>
                  {viewerLoading ? "Loading bends..." : "No bends."}
                </FileSheetStatusText>
              )}
            </FileSheetSubsection>
          </div>
      )
    },
    ...themeTabs
  ];

  return (
    <FileSheet
      open={open}
      title="DXF"
      isDesktop={isDesktop}
      width={width}
      onOpenChange={onOpenChange}
      onStartResize={onStartResize}
      scrollBody={false}
    >
      <FileSheetTabbedSurface
        kind="dxf"
        sections={sections}
        openSectionIds={openSectionIds}
        onOpenSectionIdsChange={onOpenSectionIdsChange}
      />
    </FileSheet>
  );
}
