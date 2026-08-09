import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger
} from "@/components/ui/context-menu";
import { fileAccessAssetsForEntry } from "@/workbench/fileAccessAssets";
import {
  IMPLICIT_EXPORT_FORMATS,
  STEP_EXPORT_FORMATS,
  isImportedStepEntry,
  exportItemLabel
} from "@/workbench/modelExport";

// Kinds the server can export through /__cad/export, with the formats each offers.
// An implicit exports to mesh formats only: its `.implicit.js` source IS the native
// file, so there is nothing to "download", and cadgen.implicit_export meshes it
// server-side from the live model.
function modelExportFormatsForEntry(entry) {
  const kind = String(entry?.kind || "").trim().toLowerCase();
  if (kind === "step" || kind === "assembly") {
    return STEP_EXPORT_FORMATS;
  }
  if (kind === "implicit") {
    return IMPLICIT_EXPORT_FORMATS;
  }
  return null;
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
              onCopyFileAssetReference(entry, asset.asset, asset, "filename");
            }}
          >
            <span className="min-w-0 truncate">Copy Filename</span>
          </ContextMenuItem>
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

function ModelExportSection({
  entry,
  busyKey = "",
  onExportModelFile
}) {
  const exportFormats = modelExportFormatsForEntry(entry);
  if (typeof onExportModelFile !== "function" || !exportFormats) {
    return null;
  }
  const fileRef = String(entry?.file || entry?.id || "").trim();
  const imported = isImportedStepEntry(entry);
  return (
    <>
      <ContextMenuSeparator />
      {exportFormats.map((format) => {
        const key = `${fileRef}:export:${format}`;
        return (
          <ContextMenuItem
            key={format}
            className="text-xs"
            disabled={busyKey === key}
            onSelect={() => {
              onExportModelFile(entry, format);
            }}
          >
            <span className="min-w-0 truncate">{exportItemLabel(format, { imported })}</span>
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
  onDownloadFileAsset,
  onExportModelFile,
  onRevealFileAsset,
  onRevealInExplorerView,
  onCopyFileAssetReference,
  children
}) {
  const revealInExplorerViewAvailable = entry && typeof onRevealInExplorerView === "function";
  const assetActionsAvailable = entry && typeof onDownloadFileAsset === "function";
  const modelExportAvailable = entry &&
    typeof onExportModelFile === "function" &&
    !!modelExportFormatsForEntry(entry);
  if (!revealInExplorerViewAvailable && !assetActionsAvailable && !modelExportAvailable) {
    return children;
  }

  const assets = fileAccessAssetsForEntry(entry);
  if (!revealInExplorerViewAvailable && !assets.output && !modelExportAvailable) {
    return children;
  }

  return (
    <ContextMenu modal={false}>
      <ContextMenuTrigger asChild>
        {children}
      </ContextMenuTrigger>
      <ContextMenuContent className="w-64">
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
        <ModelExportSection
          entry={entry}
          busyKey={busyKey}
          onExportModelFile={onExportModelFile}
        />
      </ContextMenuContent>
    </ContextMenu>
  );
}
