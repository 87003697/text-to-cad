import { Copy, Download, FolderOpen, Link, LoaderCircle } from "lucide-react";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger
} from "@/components/ui/context-menu";
import { fileAccessAssetsForEntry } from "@/workbench/fileAccessAssets";

function FileAccessSection({
  entry,
  asset,
  canRevealFileAssets,
  canCopyFileAssetLinks,
  canCopyFileAssetPaths,
  busyKey = "",
  onDownloadFileAsset,
  onRevealFileAsset,
  onCopyFileAssetReference
}) {
  if (!asset) {
    return null;
  }

  const key = `${asset.fileRef}:${asset.asset}`;
  const revealBusy = busyKey === key;
  const RevealIcon = revealBusy ? LoaderCircle : FolderOpen;
  const canCopyFileAssetReference = typeof onCopyFileAssetReference === "function";

  return (
    <>
      <ContextMenuItem
        className="text-xs"
        onSelect={(event) => {
          event.preventDefault();
          onDownloadFileAsset(entry, asset.asset, asset);
        }}
      >
        <Download className="size-3.5 shrink-0" aria-hidden="true" />
        <span className="min-w-0 truncate">Download</span>
      </ContextMenuItem>
      {canRevealFileAssets ? (
        <ContextMenuItem
          className="text-xs"
          disabled={revealBusy}
          onSelect={(event) => {
            event.preventDefault();
            onRevealFileAsset(entry, asset.asset, asset);
          }}
        >
          <RevealIcon className={revealBusy ? "size-3.5 shrink-0 animate-spin" : "size-3.5 shrink-0"} aria-hidden="true" />
          <span className="min-w-0 truncate">Reveal in folder</span>
        </ContextMenuItem>
      ) : null}
      {canCopyFileAssetPaths && canCopyFileAssetReference ? (
        <>
          <ContextMenuItem
            className="text-xs"
            onSelect={() => {
              onCopyFileAssetReference(entry, asset.asset, asset, "path");
            }}
          >
            <Copy className="size-3.5 shrink-0" aria-hidden="true" />
            <span className="min-w-0 truncate">Copy Path</span>
          </ContextMenuItem>
          <ContextMenuItem
            className="text-xs"
            onSelect={() => {
              onCopyFileAssetReference(entry, asset.asset, asset, "relativePath");
            }}
          >
            <Copy className="size-3.5 shrink-0" aria-hidden="true" />
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
          <Link className="size-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 truncate">Copy Link</span>
        </ContextMenuItem>
      ) : null}
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
  onRevealFileAsset,
  onCopyFileAssetReference,
  children
}) {
  const actionsAvailable = entry && typeof onDownloadFileAsset === "function";
  if (!actionsAvailable) {
    return children;
  }

  const assets = fileAccessAssetsForEntry(entry);
  if (!assets.output) {
    return children;
  }

  return (
    <ContextMenu modal={false}>
      <ContextMenuTrigger asChild>
        {children}
      </ContextMenuTrigger>
      <ContextMenuContent className="w-64">
        <FileAccessSection
          entry={entry}
          asset={assets.output}
          canRevealFileAssets={canRevealFileAssets && typeof onRevealFileAsset === "function"}
          canCopyFileAssetLinks={canCopyFileAssetLinks}
          canCopyFileAssetPaths={canCopyFileAssetPaths}
          busyKey={busyKey}
          onDownloadFileAsset={onDownloadFileAsset}
          onRevealFileAsset={onRevealFileAsset}
          onCopyFileAssetReference={onCopyFileAssetReference}
        />
      </ContextMenuContent>
    </ContextMenu>
  );
}
