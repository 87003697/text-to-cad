import { FILE_SHEET_SECTION_IDS } from "./fileSheetSections.js";

// Layout model for the tabbed file sheet sidebar.
//
// The file sheet renders each section as a tab (Chrome-inspector style). For
// STEP files the strip can be split into a top and bottom pane, with tabs that
// can be dragged between panes and a resizable divider between them. Other file
// kinds render a single tab strip.
//
// The arrangement (which pane each tab lives in, tab order, split ratio, and
// whether the split is active) is a global per-kind preference persisted to
// localStorage. Which tab is *active* in each pane is resolved from the
// per-file `openSectionIds` list (see resolveFileSheetTabPanes) so that the
// existing reveal-on-select behavior keeps working.

export const FILE_SHEET_TAB_LAYOUT_STORAGE_KEY = "cad-viewer:file-sheet-tab-layout:v1";

export const DEFAULT_FILE_SHEET_SPLIT_RATIO = 0.5;
export const MIN_FILE_SHEET_SPLIT_RATIO = 0.2;
export const MAX_FILE_SHEET_SPLIT_RATIO = 0.8;

export const FILE_SHEET_TAB_PANES = Object.freeze({ TOP: "top", BOTTOM: "bottom" });

const SPLITTABLE_KINDS = Object.freeze(new Set(["step"]));

// The default-active tab for a pane when the per-file open list names none of
// its tabs. Top pane prefers the Tree; bottom pane prefers Display.
const DEFAULT_PANE_ACTIVE = Object.freeze({
  step: {
    [FILE_SHEET_TAB_PANES.TOP]: FILE_SHEET_SECTION_IDS.STEP_TREE,
    [FILE_SHEET_TAB_PANES.BOTTOM]: FILE_SHEET_SECTION_IDS.THEME_DISPLAY
  }
});

function normalizeString(value) {
  return String(value == null ? "" : value).trim();
}

function uniqueStrings(values) {
  if (!Array.isArray(values)) {
    return [];
  }
  return [...new Set(values.map(normalizeString).filter(Boolean))];
}

export function kindSupportsSplit(kind) {
  return SPLITTABLE_KINDS.has(normalizeString(kind));
}

export function clampSplitRatio(ratio) {
  const numericRatio = Number(ratio);
  if (!Number.isFinite(numericRatio)) {
    return DEFAULT_FILE_SHEET_SPLIT_RATIO;
  }
  return Math.min(MAX_FILE_SHEET_SPLIT_RATIO, Math.max(MIN_FILE_SHEET_SPLIT_RATIO, numericRatio));
}

// Which pane a freshly-rendered tab belongs to in the default layout.
function defaultPaneForSection(kind, sectionId) {
  if (kindSupportsSplit(kind) && normalizeString(sectionId) === FILE_SHEET_SECTION_IDS.STEP_TREE) {
    return FILE_SHEET_TAB_PANES.TOP;
  }
  return kindSupportsSplit(kind) ? FILE_SHEET_TAB_PANES.BOTTOM : FILE_SHEET_TAB_PANES.TOP;
}

export function defaultFileSheetTabArrangement(kind, sectionIds) {
  const ids = uniqueStrings(sectionIds);
  if (!kindSupportsSplit(kind)) {
    return { split: false, top: ids, bottom: [], ratio: DEFAULT_FILE_SHEET_SPLIT_RATIO };
  }
  let top = ids.filter((id) => defaultPaneForSection(kind, id) === FILE_SHEET_TAB_PANES.TOP);
  let bottom = ids.filter((id) => !top.includes(id));
  // Guarantee both panes carry at least one tab when possible so the split is
  // meaningful; otherwise fall back to a single strip.
  if (!top.length && bottom.length) {
    top = [bottom[0]];
    bottom = bottom.slice(1);
  }
  const split = top.length > 0 && bottom.length > 0;
  return {
    split,
    top: split ? top : ids,
    bottom: split ? bottom : [],
    ratio: DEFAULT_FILE_SHEET_SPLIT_RATIO
  };
}

// Reconcile a stored arrangement against the sections currently rendered:
// drop tabs that no longer exist, append newly-rendered tabs to their default
// pane, and keep the split valid (both panes non-empty, or collapse to one).
export function normalizeFileSheetTabArrangement(arrangement, kind, sectionIds) {
  const rendered = uniqueStrings(sectionIds);
  if (!rendered.length) {
    return { split: false, top: [], bottom: [], ratio: DEFAULT_FILE_SHEET_SPLIT_RATIO };
  }
  if (!arrangement || typeof arrangement !== "object") {
    return defaultFileSheetTabArrangement(kind, rendered);
  }

  const renderedSet = new Set(rendered);
  const assigned = new Set();
  const filterPane = (pane) => uniqueStrings(pane).filter((id) => {
    if (!renderedSet.has(id) || assigned.has(id)) {
      return false;
    }
    assigned.add(id);
    return true;
  });

  let top = filterPane(arrangement.top);
  let bottom = kindSupportsSplit(kind) ? filterPane(arrangement.bottom) : [];

  // Append any rendered tab not yet placed, in render order, to its default pane.
  for (const id of rendered) {
    if (assigned.has(id)) {
      continue;
    }
    if (defaultPaneForSection(kind, id) === FILE_SHEET_TAB_PANES.TOP) {
      top.push(id);
    } else {
      bottom.push(id);
    }
    assigned.add(id);
  }

  const ratio = clampSplitRatio(arrangement.ratio);

  if (!kindSupportsSplit(kind)) {
    return { split: false, top: [...top, ...bottom], bottom: [], ratio };
  }

  const wantsSplit = arrangement.split !== false;
  if (wantsSplit && top.length > 0 && bottom.length > 0) {
    return { split: true, top, bottom, ratio };
  }
  if (wantsSplit) {
    // Split requested but a pane is empty — re-derive the default split.
    return { ...defaultFileSheetTabArrangement(kind, rendered), ratio };
  }
  // Collapsed: single strip backed by `top`.
  return { split: false, top: [...top, ...bottom], bottom: [], ratio };
}

function arrangementPaneList(arrangement, pane) {
  return Array.isArray(arrangement?.[pane]) ? arrangement[pane] : [];
}

// Move a tab into a pane at a target index. Removing the last tab from a pane
// while split collapses the layout into a single strip.
export function moveFileSheetTab(arrangement, kind, sectionId, targetPane, targetIndex) {
  const id = normalizeString(sectionId);
  if (!id) {
    return arrangement;
  }
  const pane = targetPane === FILE_SHEET_TAB_PANES.BOTTOM
    ? FILE_SHEET_TAB_PANES.BOTTOM
    : FILE_SHEET_TAB_PANES.TOP;
  let top = arrangementPaneList(arrangement, FILE_SHEET_TAB_PANES.TOP).filter((value) => value !== id);
  let bottom = arrangementPaneList(arrangement, FILE_SHEET_TAB_PANES.BOTTOM).filter((value) => value !== id);

  const target = pane === FILE_SHEET_TAB_PANES.TOP ? top : bottom;
  const index = Number.isFinite(Number(targetIndex))
    ? Math.min(Math.max(Math.trunc(Number(targetIndex)), 0), target.length)
    : target.length;
  target.splice(index, 0, id);

  const ratio = clampSplitRatio(arrangement?.ratio);
  if (!kindSupportsSplit(kind)) {
    return { split: false, top: [...top, ...bottom], bottom: [], ratio };
  }
  // Collapse if a pane was emptied by the move.
  if (!top.length || !bottom.length) {
    return { split: false, top: [...top, ...bottom], bottom: [], ratio };
  }
  return { split: true, top, bottom, ratio };
}

// Toggle the split. Collapsing merges the bottom pane into the top strip;
// expanding re-derives the default split when no bottom assignment survives.
export function setFileSheetTabSplit(arrangement, kind, nextSplit, sectionIds) {
  const ratio = clampSplitRatio(arrangement?.ratio);
  if (!kindSupportsSplit(kind)) {
    return normalizeFileSheetTabArrangement({ ...arrangement, split: false }, kind, sectionIds);
  }
  if (!nextSplit) {
    const top = [
      ...arrangementPaneList(arrangement, FILE_SHEET_TAB_PANES.TOP),
      ...arrangementPaneList(arrangement, FILE_SHEET_TAB_PANES.BOTTOM)
    ];
    return normalizeFileSheetTabArrangement({ split: false, top, bottom: [], ratio }, kind, sectionIds);
  }
  return normalizeFileSheetTabArrangement({ ...arrangement, split: true }, kind, sectionIds);
}

export function setFileSheetTabRatio(arrangement, ratio) {
  return { ...arrangement, ratio: clampSplitRatio(ratio) };
}

function defaultActiveForPane(kind, pane, paneTabs) {
  const preferred = DEFAULT_PANE_ACTIVE[normalizeString(kind)]?.[pane];
  if (preferred && paneTabs.includes(preferred)) {
    return preferred;
  }
  return paneTabs[0] || "";
}

// Resolve the active tab for a pane from the per-file open list: the last open
// id that lives in the pane wins, falling back to the pane default.
function resolveActiveTab(kind, pane, paneTabs, openSectionIds) {
  const open = uniqueStrings(openSectionIds);
  let active = "";
  for (const id of open) {
    if (paneTabs.includes(id)) {
      active = id;
    }
  }
  return active || defaultActiveForPane(kind, pane, paneTabs);
}

// Build the panes the surface renders: one entry per visible pane, each with
// its ordered tabs and resolved active tab.
export function resolveFileSheetTabPanes(arrangement, kind, openSectionIds) {
  const top = arrangementPaneList(arrangement, FILE_SHEET_TAB_PANES.TOP);
  const bottom = arrangementPaneList(arrangement, FILE_SHEET_TAB_PANES.BOTTOM);
  const split = arrangement?.split === true && kindSupportsSplit(kind) && top.length > 0 && bottom.length > 0;
  if (!split) {
    const tabs = [...top, ...bottom];
    return {
      split: false,
      ratio: clampSplitRatio(arrangement?.ratio),
      panes: [{
        pane: FILE_SHEET_TAB_PANES.TOP,
        tabs,
        activeId: resolveActiveTab(kind, FILE_SHEET_TAB_PANES.TOP, tabs, openSectionIds)
      }]
    };
  }
  return {
    split: true,
    ratio: clampSplitRatio(arrangement?.ratio),
    panes: [
      {
        pane: FILE_SHEET_TAB_PANES.TOP,
        tabs: top,
        activeId: resolveActiveTab(kind, FILE_SHEET_TAB_PANES.TOP, top, openSectionIds)
      },
      {
        pane: FILE_SHEET_TAB_PANES.BOTTOM,
        tabs: bottom,
        activeId: resolveActiveTab(kind, FILE_SHEET_TAB_PANES.BOTTOM, bottom, openSectionIds)
      }
    ]
  };
}

// Produce the next per-file open list when a tab is activated: drop the other
// tabs that share its pane, then append it so it wins resolution.
export function activateFileSheetTab(openSectionIds, arrangement, kind, pane, sectionId) {
  const id = normalizeString(sectionId);
  if (!id) {
    return uniqueStrings(openSectionIds);
  }
  const paneTabs = new Set(arrangementPaneList(arrangement, pane));
  const next = uniqueStrings(openSectionIds).filter((value) => value === id || !paneTabs.has(value));
  if (!next.includes(id)) {
    next.push(id);
  } else {
    // Move to the end so last-in-pane resolution selects it.
    next.splice(next.indexOf(id), 1);
    next.push(id);
  }
  return next;
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function readFileSheetTabLayoutStore(storage) {
  if (!storage) {
    return {};
  }
  try {
    const raw = storage.getItem(FILE_SHEET_TAB_LAYOUT_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw);
    return isPlainObject(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

export function writeFileSheetTabLayoutStore(storage, store) {
  if (!storage) {
    return;
  }
  try {
    storage.setItem(FILE_SHEET_TAB_LAYOUT_STORAGE_KEY, JSON.stringify(isPlainObject(store) ? store : {}));
  } catch {
    // Ignore quota/serialization errors — tab layout is a non-critical preference.
  }
}
