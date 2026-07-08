import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger
} from "@/components/ui/context-menu";
import { fileAccessAssetsForEntry } from "@/workbench/fileAccessAssets";
import { IMPLICIT_EXPORT_FORMATS } from "@/workbench/implicitExport";
import { openInBusyKey } from "@/workbench/openInApps";
import { STEP_EXPORT_FORMATS, isImportedStepEntry, stepExportItemLabel } from "@/workbench/stepExport";

// Entries whose geometry can be exported to STEP/3MF/STL/GLB via the server export endpoint.
function isStepExportEntry(entry) {
  const kind = String(entry?.kind || "").trim().toLowerCase();
  return kind === "step" || kind === "assembly";
}

function OpenInSection({
  entry,
  openInTargets = [],
  busyKey = "",
  onOpenInApp
}) {
  if (
    typeof onOpenInApp !== "function" ||
    !isStepExportEntry(entry) ||
    !Array.isArray(openInTargets) ||
    !openInTargets.length
  ) {
    return null;
  }
  const fileRef = String(entry?.file || entry?.id || "").trim();
  return (
    <>
      <ContextMenuSub>
        <ContextMenuSubTrigger className="text-xs">
          <span className="min-w-0 truncate">Open in</span>
        </ContextMenuSubTrigger>
        <ContextMenuSubContent className="w-52">
          {openInTargets.map((target) => (
            <ContextMenuItem
              key={target.id}
              className="text-xs"
              disabled={!target.installed || busyKey === openInBusyKey(fileRef, target.id)}
              onSelect={() => {
                onOpenInApp(entry, target);
              }}
            >
              <span className="min-w-0 truncate">{target.name}</span>
              {!target.installed ? (
                <span className="ml-auto text-[10px] text-muted-foreground">Not installed</span>
              ) : null}
            </ContextMenuItem>
          ))}
        </ContextMenuSubContent>
      </ContextMenuSub>
      <ContextMenuSeparator />
    </>
  );
}

function ExplorerViewSection({
  entry,
  onRevealInExplorerView
}) {
  if (typeof onRevealInExplorerView !== "function") {
    return null;
  }

  return (
    <ContextMenuItem
      className="text-xs"
      onSelect={() => {
        onRevealInExplorerView(entry);
      }}
    >
      <span className="min-w-0 truncate">Reveal in Explorer View</span>
    </ContextMenuItem>
  );
}

function FileAccessSection({
  entry,
  asset,
  canRevealFileAssets,
  canCopyFileAssetLinks,
  canCopyFileAssetPaths,
  busyKey = "",
  onRevealFileAsset,
  onRevealInExplorerView,
  onCopyFileAssetReference
}) {
  if (!asset) {
    return null;
  }

  const key = `${asset.fileRef}:${asset.asset}`;
  const revealBusy = busyKey === key;
  const canCopyFileAssetReference = typeof onCopyFileAssetReference === "function";

  return (
    <>
      {canRevealFileAssets ? (
        <ContextMenuItem
          className="text-xs"
          disabled={revealBusy}
          onSelect={() => {
            onRevealFileAsset(entry, asset.asset, asset);
          }}
        >
          <span className="min-w-0 truncate">Reveal in Folder</span>
        </ContextMenuItem>
      ) : null}
      <ExplorerViewSection
        entry={entry}
        onRevealInExplorerView={onRevealInExplorerView}
      />
      {canCopyFileAssetPaths && canCopyFileAssetReference ? (
        <>
          <ContextMenuItem
            className="text-xs"
            onSelect={() => {
              onCopyFileAssetReference(entry, asset.asset, asset, "path");
            }}
          >
            <span className="min-w-0 truncate">Copy Path</span>
          </ContextMenuItem>
          <ContextMenuItem
            className="text-xs"
            onSelect={() => {
              onCopyFileAssetReference(entry, asset.asset, asset, "relativePath");
            }}
          >
            <span className="min-w-0 truncate">Copy Relative Path</span>
          </ContextMenuItem>
        </>
      ) : null}
      {canCopyFileAssetLinks && canCopyFileAssetReference ? (
        <ContextMenuItem
          className="text-xs"
          onSelect={() => {
            onCopyFileAssetReference(entry, asset.asset, asset, "link");
          }}
        >
          <span className="min-w-0 truncate">Copy Link</span>
        </ContextMenuItem>
      ) : null}
    </>
  );
}

function ImplicitExportSection({
  entry,
  busyKey = "",
  onExportImplicitFile
}) {
  if (
    String(entry?.kind || "").trim().toLowerCase() !== "implicit" ||
    typeof onExportImplicitFile !== "function"
  ) {
    return null;
  }
  const fileRef = String(entry?.file || entry?.id || "").trim();
  return (
    <>
      <ContextMenuSeparator />
      {IMPLICIT_EXPORT_FORMATS.map((format) => {
        const upperFormat = format.toUpperCase();
        const key = `${fileRef}:export:${format}`;
        return (
          <ContextMenuItem
            key={format}
            className="text-xs"
            disabled={busyKey === key}
            onSelect={() => {
              onExportImplicitFile(entry, format);
            }}
          >
            <span className="min-w-0 truncate">Export to {upperFormat}</span>
          </ContextMenuItem>
        );
      })}
    </>
  );
}

function StepExportSection({
  entry,
  busyKey = "",
  onExportStepFile
}) {
  if (typeof onExportStepFile !== "function" || !isStepExportEntry(entry)) {
    return null;
  }
  const fileRef = String(entry?.file || entry?.id || "").trim();
  const imported = isImportedStepEntry(entry);
  return (
    <>
      <ContextMenuSeparator />
      {STEP_EXPORT_FORMATS.map((format) => {
        const key = `${fileRef}:export:${format}`;
        return (
          <ContextMenuItem
            key={format}
            className="text-xs"
            disabled={busyKey === key}
            onSelect={() => {
              onExportStepFile(entry, format);
            }}
          >
            <span className="min-w-0 truncate">{stepExportItemLabel(format, { imported })}</span>
          </ContextMenuItem>
        );
      })}
    </>
  );
}

export default function FileAccessContextMenu({
  entry,
  canRevealFileAssets = false,
  canCopyFileAssetLinks = false,
  canCopyFileAssetPaths = false,
  busyKey = "",
  openInTargets = [],
  onDownloadFileAsset,
  onExportImplicitFile,
  onExportStepFile,
  onOpenInApp,
  onRevealFileAsset,
  onRevealInExplorerView,
  onCopyFileAssetReference,
  children
}) {
  const revealInExplorerViewAvailable = entry && typeof onRevealInExplorerView === "function";
  const assetActionsAvailable = entry && typeof onDownloadFileAsset === "function";
  const implicitExportAvailable = entry && typeof onExportImplicitFile === "function" &&
    String(entry?.kind || "").trim().toLowerCase() === "implicit";
  const stepExportAvailable = entry && typeof onExportStepFile === "function" && isStepExportEntry(entry);
  const openInAvailable = entry && typeof onOpenInApp === "function" && isStepExportEntry(entry) &&
    Array.isArray(openInTargets) && openInTargets.length > 0;
  if (!revealInExplorerViewAvailable && !assetActionsAvailable && !implicitExportAvailable && !stepExportAvailable && !openInAvailable) {
    return children;
  }

  const assets = fileAccessAssetsForEntry(entry);
  if (!revealInExplorerViewAvailable && !assets.output && !implicitExportAvailable && !stepExportAvailable && !openInAvailable) {
    return children;
  }

  return (
    <ContextMenu modal={false}>
      <ContextMenuTrigger asChild>
        {children}
      </ContextMenuTrigger>
      <ContextMenuContent className="w-64">
        <OpenInSection
          entry={entry}
          openInTargets={openInTargets}
          busyKey={busyKey}
          onOpenInApp={onOpenInApp}
        />
        {!assets.output || !assetActionsAvailable ? (
          <ExplorerViewSection
            entry={entry}
            onRevealInExplorerView={onRevealInExplorerView}
          />
        ) : null}
        {assets.output && assetActionsAvailable ? (
          <FileAccessSection
            entry={entry}
            asset={assets.output}
            canRevealFileAssets={canRevealFileAssets && typeof onRevealFileAsset === "function"}
            canCopyFileAssetLinks={canCopyFileAssetLinks}
            canCopyFileAssetPaths={canCopyFileAssetPaths}
            busyKey={busyKey}
            onRevealFileAsset={onRevealFileAsset}
            onRevealInExplorerView={onRevealInExplorerView}
            onCopyFileAssetReference={onCopyFileAssetReference}
          />
        ) : null}
        <ImplicitExportSection
          entry={entry}
          busyKey={busyKey}
          onExportImplicitFile={onExportImplicitFile}
        />
        <StepExportSection
          entry={entry}
          busyKey={busyKey}
          onExportStepFile={onExportStepFile}
        />
      </ContextMenuContent>
    </ContextMenu>
  );
}
