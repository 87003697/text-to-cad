import FileSheet from "./FileSheet";
import FileSheetTabbedSurface from "./FileSheetTabbedSurface";
import { buildFileMetadataTab } from "./FileMetadataSection";
import { buildFileStatusTab } from "./FileStatusSection";
import ImplicitGraphicsSection from "./ImplicitGraphicsSection";
import { buildParameterControlsTab } from "./ParameterControlsSection";

export default function ImplicitFileSheet({
  open,
  title = "Implicit CAD",
  isDesktop,
  width,
  selectedEntry = null,
  onOpenChange,
  onStartResize,
  parameterRuntime = null,
  graphicsRuntime = null,
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
    buildParameterControlsTab({
      runtime: parameterRuntime,
      label: "implicit parameter",
      loadingLabel: "Loading implicit parameters...",
      noParametersLabel: "No implicit parameters.",
      hideWhenEmpty: true,
      animationAriaLabel: "Implicit animation",
      copyTitle: "Copy implicit parameter JSON",
      pasteTitle: "Paste implicit parameter JSON"
    }),
    {
      id: "graphics",
      title: "Graphics",
      content: <ImplicitGraphicsSection runtime={graphicsRuntime} />
    },
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
        kind="implicit"
        sections={sections}
        openSectionIds={openSectionIds}
        onOpenSectionIdsChange={onOpenSectionIdsChange}
      />
    </FileSheet>
  );
}
