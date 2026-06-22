import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PanelBottomClose, Rows2 } from "lucide-react";
import { cn } from "@/ui/utils";
import {
  activateFileSheetTab,
  clampSplitRatio,
  FILE_SHEET_TAB_PANES,
  kindSupportsSplit,
  moveFileSheetTab,
  normalizeFileSheetTabArrangement,
  readFileSheetTabLayoutStore,
  resolveFileSheetTabPanes,
  setFileSheetTabRatio,
  setFileSheetTabSplit,
  writeFileSheetTabLayoutStore
} from "@/workbench/fileSheetTabLayout";
import { ScrollArea } from "../ui/scroll-area";

function localStorageOrNull() {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

// Per-kind tab arrangement (pane assignment, order, split, ratio), persisted
// globally to localStorage. Active tab selection stays per-file via openSectionIds.
function useFileSheetTabArrangement(kind, sectionIds) {
  const sectionKey = sectionIds.join("|");
  const sectionIdsRef = useRef(sectionIds);
  sectionIdsRef.current = sectionIds;

  const [store, setStore] = useState(() => ({}));

  // Hydrate from storage on the client after mount to avoid SSR mismatches.
  useEffect(() => {
    const stored = readFileSheetTabLayoutStore(localStorageOrNull());
    if (stored && Object.keys(stored).length) {
      setStore(stored);
    }
  }, []);

  const arrangement = useMemo(
    () => normalizeFileSheetTabArrangement(store[kind], kind, sectionIds),
    // sectionKey captures sectionIds identity.
    [store, kind, sectionKey] // eslint-disable-line react-hooks/exhaustive-deps
  );

  const updateArrangement = useCallback((updater) => {
    setStore((current) => {
      const ids = sectionIdsRef.current;
      const base = normalizeFileSheetTabArrangement(current[kind], kind, ids);
      const next = typeof updater === "function" ? updater(base) : updater;
      const normalized = normalizeFileSheetTabArrangement(next, kind, ids);
      const nextStore = { ...current, [kind]: normalized };
      writeFileSheetTabLayoutStore(localStorageOrNull(), nextStore);
      return nextStore;
    });
  }, [kind]);

  return [arrangement, updateArrangement];
}

function FileSheetTab({
  section,
  pane,
  active,
  dragging,
  onActivate,
  onDragStart,
  onDragEnd
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      title={section.titleAttr || undefined}
      data-file-sheet-tab={section.id}
      draggable
      onClick={() => onActivate(pane, section.id)}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        try {
          event.dataTransfer.setData("text/plain", section.id);
        } catch {
          // Some browsers disallow setData outside trusted handlers; ignore.
        }
        onDragStart(section.id, pane);
      }}
      onDragEnd={onDragEnd}
      className={cn(
        "group/file-sheet-tab relative flex h-7 max-w-[11rem] shrink-0 cursor-pointer select-none items-center gap-1.5 whitespace-nowrap rounded-md px-2 text-[11px] font-medium leading-none transition-colors",
        active
          ? "bg-accent text-foreground"
          : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
        dragging && "opacity-40"
      )}
    >
      <span className="min-w-0 truncate">{section.title}</span>
    </button>
  );
}

function FileSheetTabPane({
  pane,
  tabs,
  activeId,
  sectionsById,
  dragId,
  dropIndex,
  isDropPane,
  onActivate,
  onDragStartTab,
  onDragEndTab,
  onPaneDragOver,
  onPaneDrop,
  onPaneDragLeave,
  splitToggle
}) {
  const stripRef = useRef(null);
  const activeSection = activeId ? sectionsById.get(activeId) : null;

  return (
    <section
      className="flex min-h-0 min-w-0 flex-1 flex-col"
      data-file-sheet-tab-pane={pane}
      onDragOver={(event) => onPaneDragOver(event, pane, stripRef.current)}
      onDrop={(event) => onPaneDrop(event, pane)}
      onDragLeave={onPaneDragLeave}
    >
      <div
        role="tablist"
        aria-orientation="horizontal"
        ref={stripRef}
        className={cn(
          "flex h-9 shrink-0 items-center gap-1 overflow-x-auto border-b border-sidebar-border/70 px-1.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
          isDropPane && "bg-accent/20"
        )}
      >
        {tabs.map((id, index) => {
          const section = sectionsById.get(id);
          if (!section) {
            return null;
          }
          return (
            <span key={id} className="relative flex items-center">
              {isDropPane && dropIndex === index ? (
                <span className="absolute -left-0.5 top-1 h-5 w-0.5 rounded-full bg-primary" aria-hidden="true" />
              ) : null}
              <FileSheetTab
                section={section}
                pane={pane}
                active={id === activeId}
                dragging={id === dragId}
                onActivate={onActivate}
                onDragStart={onDragStartTab}
                onDragEnd={onDragEndTab}
              />
            </span>
          );
        })}
        {isDropPane && dropIndex >= tabs.length ? (
          <span className="relative flex items-center">
            <span className="h-5 w-0.5 rounded-full bg-primary" aria-hidden="true" />
          </span>
        ) : null}
        {splitToggle ? <span className="ml-auto flex shrink-0 items-center pl-1">{splitToggle}</span> : null}
      </div>
      <ScrollArea
        className="min-h-0 flex-1"
        viewportClassName="h-full"
        data-file-sheet-tab-panel={activeId || undefined}
      >
        {activeSection ? activeSection.content : null}
      </ScrollArea>
    </section>
  );
}

function SplitToggleButton({ split, onToggle }) {
  const Icon = split ? PanelBottomClose : Rows2;
  const label = split ? "Merge tabs into one strip" : "Split tabs into two panes";
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={label}
      title={label}
      className="flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
    >
      <Icon className="size-3.5" strokeWidth={2} aria-hidden="true" />
    </button>
  );
}

function computeDropIndex(stripEl, clientX) {
  if (!stripEl) {
    return null;
  }
  const tabEls = stripEl.querySelectorAll("[data-file-sheet-tab]");
  let index = 0;
  for (const tabEl of tabEls) {
    const rect = tabEl.getBoundingClientRect();
    if (clientX < rect.left + rect.width / 2) {
      return index;
    }
    index += 1;
  }
  return index;
}

export default function FileSheetTabbedSurface({
  kind,
  sections,
  openSectionIds = [],
  onOpenSectionIdsChange
}) {
  const visibleSections = useMemo(
    () => (Array.isArray(sections) ? sections.filter(Boolean) : []),
    [sections]
  );
  const sectionIds = useMemo(() => visibleSections.map((section) => section.id), [visibleSections]);
  const sectionsById = useMemo(() => {
    const map = new Map();
    for (const section of visibleSections) {
      map.set(section.id, section);
    }
    return map;
  }, [visibleSections]);

  const [arrangement, updateArrangement] = useFileSheetTabArrangement(kind, sectionIds);
  const resolved = useMemo(
    () => resolveFileSheetTabPanes(arrangement, kind, openSectionIds),
    [arrangement, kind, openSectionIds]
  );

  const [dragId, setDragId] = useState("");
  const dragIdRef = useRef("");
  const [dropTarget, setDropTarget] = useState(null);
  const [liveRatio, setLiveRatio] = useState(null);
  const containerRef = useRef(null);
  const dividerDraggingRef = useRef(false);

  const handleActivate = useCallback((pane, sectionId) => {
    onOpenSectionIdsChange?.(activateFileSheetTab(openSectionIds, arrangement, kind, pane, sectionId));
  }, [arrangement, kind, onOpenSectionIdsChange, openSectionIds]);

  const handleDragStartTab = useCallback((sectionId) => {
    dragIdRef.current = sectionId;
    setDragId(sectionId);
  }, []);

  const clearDrag = useCallback(() => {
    dragIdRef.current = "";
    setDragId("");
    setDropTarget(null);
  }, []);

  const handlePaneDragOver = useCallback((event, pane, stripEl) => {
    if (!dragIdRef.current) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const index = computeDropIndex(stripEl, event.clientX);
    setDropTarget({ pane, index: index == null ? 0 : index });
  }, []);

  const handlePaneDragLeave = useCallback((event) => {
    // Only clear when leaving the pane entirely (not when moving onto a child).
    if (event.currentTarget.contains(event.relatedTarget)) {
      return;
    }
    setDropTarget((current) => (current ? null : current));
  }, []);

  const handlePaneDrop = useCallback((event, pane) => {
    const sectionId = dragIdRef.current;
    if (!sectionId) {
      return;
    }
    event.preventDefault();
    const targetIndex = dropTarget && dropTarget.pane === pane ? dropTarget.index : undefined;
    const nextArrangement = moveFileSheetTab(arrangement, kind, sectionId, pane, targetIndex);
    updateArrangement(nextArrangement);
    const landedPane = (nextArrangement.bottom || []).includes(sectionId)
      ? FILE_SHEET_TAB_PANES.BOTTOM
      : FILE_SHEET_TAB_PANES.TOP;
    onOpenSectionIdsChange?.(
      activateFileSheetTab(openSectionIds, nextArrangement, kind, landedPane, sectionId)
    );
    clearDrag();
  }, [arrangement, clearDrag, dropTarget, kind, onOpenSectionIdsChange, openSectionIds, updateArrangement]);

  const handleToggleSplit = useCallback(() => {
    updateArrangement((current) => setFileSheetTabSplit(current, kind, current.split !== true, sectionIds));
  }, [kind, sectionIds, updateArrangement]);

  const canSplit = kindSupportsSplit(kind) && sectionIds.length > 1;
  const splitToggle = canSplit ? (
    <SplitToggleButton split={resolved.split} onToggle={handleToggleSplit} />
  ) : null;

  // Divider resize.
  const handleDividerPointerDown = useCallback((event) => {
    event.preventDefault();
    dividerDraggingRef.current = true;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }, []);

  const handleDividerPointerMove = useCallback((event) => {
    if (!dividerDraggingRef.current || !containerRef.current) {
      return;
    }
    const rect = containerRef.current.getBoundingClientRect();
    if (rect.height <= 0) {
      return;
    }
    setLiveRatio(clampSplitRatio((event.clientY - rect.top) / rect.height));
  }, []);

  const handleDividerPointerUp = useCallback((event) => {
    if (!dividerDraggingRef.current) {
      return;
    }
    dividerDraggingRef.current = false;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    setLiveRatio((ratio) => {
      if (ratio != null) {
        updateArrangement((current) => setFileSheetTabRatio(current, ratio));
      }
      return null;
    });
  }, [updateArrangement]);

  const renderPane = (paneInfo, options = {}) => (
    <FileSheetTabPane
      pane={paneInfo.pane}
      tabs={paneInfo.tabs}
      activeId={paneInfo.activeId}
      sectionsById={sectionsById}
      dragId={dragId}
      dropIndex={dropTarget && dropTarget.pane === paneInfo.pane ? dropTarget.index : -1}
      isDropPane={Boolean(dragId) && dropTarget?.pane === paneInfo.pane}
      onActivate={handleActivate}
      onDragStartTab={handleDragStartTab}
      onDragEndTab={clearDrag}
      onPaneDragOver={handlePaneDragOver}
      onPaneDrop={handlePaneDrop}
      onPaneDragLeave={handlePaneDragLeave}
      splitToggle={options.splitToggle}
    />
  );

  if (!resolved.split) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {renderPane(resolved.panes[0], { splitToggle })}
      </div>
    );
  }

  const ratio = liveRatio == null ? resolved.ratio : liveRatio;
  const [topPane, bottomPane] = resolved.panes;

  return (
    <div ref={containerRef} className="flex min-h-0 flex-1 flex-col">
      <div
        className="flex min-h-0 min-w-0 flex-col"
        style={{ flex: `0 0 ${ratio * 100}%` }}
      >
        {renderPane(topPane, { splitToggle })}
      </div>
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize tab panes"
        onPointerDown={handleDividerPointerDown}
        onPointerMove={handleDividerPointerMove}
        onPointerUp={handleDividerPointerUp}
        className="group/file-sheet-split relative z-10 flex h-1.5 shrink-0 cursor-row-resize touch-none items-center justify-center"
      >
        <span className="h-px w-full bg-sidebar-border transition-colors group-hover/file-sheet-split:bg-ring" />
      </div>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {renderPane(bottomPane)}
      </div>
    </div>
  );
}
