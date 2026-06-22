import FileSheet from "./FileSheet";
import FileSheetTabbedSurface from "./FileSheetTabbedSurface";
import { buildFileMetadataTab } from "./FileMetadataSection";
import { buildFileStatusTab } from "./FileStatusSection";

export default function MeshFileSheet({
  open,
  title = "Mesh",
  isDesktop,
  width,
  selectedEntry = null,
  onOpenChange,
  onStartResize,
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
  const sections = [
    buildFileStatusTab(statusItems),
    ...themeTabs,
    buildFileMetadataTab({
      entry: selectedEntry,
      fileDownloadAvailable,
      viewerServerInfo,
      localFileOpenAvailable,
      fileAccessBusyKey,
      onOpenFileAsset,
      suppressDynamicStatus: suppressDynamicMetadataStatus
    })
  ];

  return (
    <FileSheet
      open={open}
      title={title}
      isDesktop={isDesktop}
      width={width}
      onOpenChange={onOpenChange}
      onStartResize={onStartResize}
      scrollBody={false}
    >
      <FileSheetTabbedSurface
        kind="mesh"
        sections={sections}
        openSectionIds={openSectionIds}
        onOpenSectionIdsChange={onOpenSectionIdsChange}
      />
    </FileSheet>
  );
}
