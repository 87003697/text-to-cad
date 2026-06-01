import {
  Download,
  Film,
  Moon,
  Pause,
  Play,
  RotateCcw,
  SlidersHorizontal,
  Sun,
} from "lucide-react";
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DEFAULT_IMPLICIT_GRAPHICS_SETTINGS,
  animatedImplicitParameterValues,
  exportImplicitAnimatedGlb,
  exportImplicitModel,
  IMPLICIT_GRAPHICS_LIMITS,
  loadImplicitSource,
  normalizeImplicitAnimationElapsed,
  normalizeImplicitGraphicsSettings,
  normalizeParameterValue,
  normalizeParameterValues
} from "implicitjs";

import { CodePane } from "./CodePane.jsx";
import { ScrollArea } from "./components/ui/scroll-area.jsx";

const ImplicitViewport = lazy(() => (
  import("./ImplicitViewport.jsx").then((module) => ({ default: module.ImplicitViewport }))
));

const DEFAULT_TEMPLATE_ID = "mobius-strip";
const EMPTY_TEMPLATE_ID = "empty";
const SOURCE_STORAGE_KEY = "implicit-demo-source";
const COMPILE_DEBOUNCE_MS = 260;
const PREVIEW_BOOT_DELAY_MS = 520;
const MOBILE_TABS = Object.freeze([
  ["source", "Source"],
  ["preview", "Preview"],
  ["controls", "Controls"]
]);
const EMPTY_TEMPLATE = Object.freeze({
  id: EMPTY_TEMPLATE_ID,
  label: "Empty",
  file: "",
  description: "A blank implicit.js module."
});
const EMPTY_TEMPLATE_SOURCE = `const GLSL = \`
float sdf(vec3 p) {
  return 1.0;
}
\`;

export default {
  schema: "implicit.js/0.1.0",
  name: "empty implicit",
  units: "mm",
  params: {},
  glsl: GLSL
};
`;

function readStoredSource() {
  try {
    return localStorage.getItem(SOURCE_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

function clampNumber(value, min, max) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return min;
  }
  return Math.min(Math.max(numeric, min), max);
}

function useDebouncedValue(value, delayMs) {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setDebouncedValue(value), delayMs);
    return () => window.clearTimeout(timeoutId);
  }, [delayMs, value]);

  return debouncedValue;
}

function compileSummary(model) {
  if (!model) {
    return [];
  }
  const formatNumber = (value) => Number(value || 0).toFixed(Math.abs(value) >= 10 ? 1 : 2);
  return [
    { label: "steps", value: model.maxSteps },
    { label: "bounds", value: model.boundsSource || "declared" },
    { label: "radius", value: formatNumber(model.radius) },
    { label: "params", value: model.parameters?.length || 0 }
  ];
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error || "");
}

function safeFileStem(value, fallback = "implicit-model") {
  return String(value || fallback)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "") || fallback;
}

function downloadExport(result, filename) {
  const blob = new Blob([result.body], { type: result.contentType || "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function IconButton({ children, title, className = "", ...props }) {
  return (
    <button className={`icon-button ${className}`} title={title} aria-label={title} {...props}>
      {children}
    </button>
  );
}

function FieldLabel({ children }) {
  return <span className="field-label">{children}</span>;
}

function SectionTitle({ children, icon = null, meta = "" }) {
  return (
    <div className="section-bar">
      <div className="section-title-row">
        {icon}
        <span>{children}</span>
      </div>
      {meta ? <span className="section-meta">{meta}</span> : null}
    </div>
  );
}

function SplitHandle({ axis, className = "", onPointerDown, title }) {
  return (
    <div
      className={`split-handle split-${axis} ${className}`}
      role="separator"
      tabIndex={0}
      title={title}
      aria-label={title}
      onPointerDown={onPointerDown}
    />
  );
}

function PreviewLoading({ label = "Loading preview" }) {
  return (
    <div className="viewport-loading" role="status" aria-live="polite">
      <span className="loading-spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

function PreviewLoadingHost({ label }) {
  return (
    <div className="viewport-canvas-host">
      <PreviewLoading label={label} />
    </div>
  );
}

function beginSplitDrag(event, {
  axis,
  containerRef,
  max = 78,
  min = 22,
  setValue
}) {
  const container = containerRef.current;
  if (!container) {
    return;
  }
  event.preventDefault();
  const rect = container.getBoundingClientRect();
  document.documentElement.classList.add("is-resizing");

  const update = (pointerEvent) => {
    const raw = axis === "x"
      ? ((pointerEvent.clientX - rect.left) / Math.max(rect.width, 1)) * 100
      : ((pointerEvent.clientY - rect.top) / Math.max(rect.height, 1)) * 100;
    setValue(clampNumber(raw, min, max));
  };
  const stop = () => {
    document.documentElement.classList.remove("is-resizing");
    window.removeEventListener("pointermove", update);
    window.removeEventListener("pointerup", stop);
  };

  update(event);
  window.addEventListener("pointermove", update);
  window.addEventListener("pointerup", stop, { once: true });
}

function beginTrailingSplitDrag(event, {
  containerRef,
  max = 42,
  min = 18,
  setValue
}) {
  const container = containerRef.current;
  if (!container) {
    return;
  }
  event.preventDefault();
  const rect = container.getBoundingClientRect();
  document.documentElement.classList.add("is-resizing");

  const update = (pointerEvent) => {
    const raw = ((rect.right - pointerEvent.clientX) / Math.max(rect.width, 1)) * 100;
    setValue(clampNumber(raw, min, max));
  };
  const stop = () => {
    document.documentElement.classList.remove("is-resizing");
    window.removeEventListener("pointermove", update);
    window.removeEventListener("pointerup", stop);
  };

  update(event);
  window.addEventListener("pointermove", update);
  window.addEventListener("pointerup", stop, { once: true });
}

function ParameterControl({ parameter, value, onChange, animatedValue }) {
  const displayValue = animatedValue ?? value;
  if (parameter.type === "boolean") {
    return (
      <label className="toggle-row">
        <span>
          <FieldLabel>{parameter.label}</FieldLabel>
          {parameter.description ? <small>{parameter.description}</small> : null}
        </span>
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => onChange(parameter.id, event.target.checked)}
        />
      </label>
    );
  }
  if (parameter.type === "color") {
    return (
      <label className="control-row">
        <span className="control-head">
          <FieldLabel>{parameter.label}</FieldLabel>
          <code>{String(displayValue)}</code>
        </span>
        <input type="color" value={value} onChange={(event) => onChange(parameter.id, event.target.value)} />
      </label>
    );
  }
  if (parameter.type === "enum") {
    return (
      <label className="control-row">
        <FieldLabel>{parameter.label}</FieldLabel>
        <select value={value} onChange={(event) => onChange(parameter.id, event.target.value)}>
          {parameter.options.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </label>
    );
  }
  const min = Number(parameter.min);
  const max = Number(parameter.max);
  const step = Number(parameter.step) || 0.01;
  return (
    <label className="control-row">
      <span className="control-head">
        <FieldLabel>{parameter.label}</FieldLabel>
        <code>{Number(displayValue || 0).toFixed(step >= 1 ? 0 : 2)}{parameter.unit ? ` ${parameter.unit}` : ""}</code>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={Number(value || 0)}
        onChange={(event) => onChange(parameter.id, Number(event.target.value))}
      />
    </label>
  );
}

function GraphicsControl({ id, label, value, onChange }) {
  const limits = IMPLICIT_GRAPHICS_LIMITS[id];
  return (
    <label className="control-row">
      <span className="control-head">
        <FieldLabel>{label}</FieldLabel>
        <code>{Number(value).toFixed(2)}</code>
      </span>
      <input
        type="range"
        min={limits.min}
        max={limits.max}
        step={limits.step}
        value={value}
        onChange={(event) => onChange(id, Number(event.target.value))}
      />
    </label>
  );
}

function NumberControl({ label, value, min, max, step = 1, unit = "", onChange }) {
  const numericValue = Number(value);
  return (
    <label className="control-row">
      <span className="control-head">
        <FieldLabel>{label}</FieldLabel>
        <code>{Number.isFinite(numericValue) ? numericValue.toFixed(step >= 1 ? 0 : 2) : value}{unit ? ` ${unit}` : ""}</code>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function InspectorPanel({
  model,
  definition,
  paramValues,
  animatedValues,
  onParamChange,
  graphics,
  onGraphicsChange,
  activeAnimationId,
  onAnimationChange,
  playing,
  onTogglePlaying,
  onResetAnimation,
  exportSettings,
  onExportSettingChange,
  runtimeError
}) {
  const parameters = definition?.parameters || [];
  const animations = definition?.animations || [];
  const summary = compileSummary(model);

  return (
    <aside className="inspector">
      <SectionTitle icon={<SlidersHorizontal size={14} />} meta="runtime">Controls</SectionTitle>

      <ScrollArea className="inspector-scroll">
        <div className="inspector-content">
          <div className="stat-grid">
            {summary.map((item) => (
              <div className="stat-cell" key={item.label}>
                <span>{item.label}</span>
                <strong>{item.value}</strong>
              </div>
            ))}
          </div>

          {runtimeError ? (
            <div className="alert error-alert">
              <FieldLabel>runtime error</FieldLabel>
              <p>{runtimeError}</p>
            </div>
          ) : null}

          {animations.length ? (
            <section className="control-section">
              <div className="section-title">animation</div>
              <div className="animation-row">
                <select value={activeAnimationId} onChange={(event) => onAnimationChange(event.target.value)}>
                  {animations.map((animation) => (
                    <option key={animation.id} value={animation.id}>{animation.label}</option>
                  ))}
                </select>
                <IconButton title={playing ? "Pause animation" : "Play animation"} onClick={onTogglePlaying}>
                  {playing ? <Pause size={16} /> : <Play size={16} />}
                </IconButton>
                <IconButton title="Reset animation" onClick={onResetAnimation}>
                  <RotateCcw size={16} />
                </IconButton>
              </div>
            </section>
          ) : null}

          {parameters.length ? (
            <section className="control-section">
              <div className="section-title">parameters</div>
              <div className="control-stack">
                {parameters.map((parameter) => (
                  <ParameterControl
                    key={parameter.id}
                    parameter={parameter}
                    value={paramValues[parameter.id]}
                    animatedValue={animatedValues[parameter.id]}
                    onChange={onParamChange}
                  />
                ))}
              </div>
            </section>
          ) : (
            <section className="control-section">
              <div className="section-title">parameters</div>
              <p className="muted-copy">This implicit module has no exposed params.</p>
            </section>
          )}

          <section className="control-section">
            <div className="section-title">export</div>
            <div className="control-stack">
              <NumberControl
                label="mesh resolution"
                min={24}
                max={192}
                step={4}
                value={exportSettings.resolution}
                onChange={(value) => onExportSettingChange("resolution", value)}
              />
              <NumberControl
                label="animation frames"
                min={4}
                max={48}
                step={1}
                value={exportSettings.frames}
                onChange={(value) => onExportSettingChange("frames", value)}
              />
              <NumberControl
                label="animation duration"
                min={0.25}
                max={20}
                step={0.25}
                unit="s"
                value={exportSettings.duration}
                onChange={(value) => onExportSettingChange("duration", value)}
              />
            </div>
          </section>

          <section className="control-section">
            <div className="section-title">graphics</div>
            <div className="control-stack">
              <GraphicsControl id="resolutionScale" label="idle resolution" value={graphics.resolutionScale} onChange={onGraphicsChange} />
              <GraphicsControl id="interactionResolutionScale" label="drag resolution" value={graphics.interactionResolutionScale} onChange={onGraphicsChange} />
              <GraphicsControl id="detail" label="ray detail" value={graphics.detail} onChange={onGraphicsChange} />
              <GraphicsControl id="normalSmoothing" label="normal smoothing" value={graphics.normalSmoothing} onChange={onGraphicsChange} />
              {[
                ["modelColors", "model colors"],
                ["shadows", "soft shadows"],
                ["ambientOcclusion", "ambient occlusion"],
                ["rimLight", "rim light"]
              ].map(([id, label]) => (
                <label className="toggle-row" key={id}>
                  <span><FieldLabel>{label}</FieldLabel></span>
                  <input type="checkbox" checked={Boolean(graphics[id])} onChange={(event) => onGraphicsChange(id, event.target.checked)} />
                </label>
              ))}
            </div>
          </section>
        </div>
      </ScrollArea>
    </aside>
  );
}

export default function App() {
  const initialSourceRef = useRef(readStoredSource());
  const [templates, setTemplates] = useState([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState(initialSourceRef.current.trim() ? "" : DEFAULT_TEMPLATE_ID);
  const [code, setCode] = useState(() => initialSourceRef.current);
  const [loadedTemplateCode, setLoadedTemplateCode] = useState(() => initialSourceRef.current);
  const [compileState, setCompileState] = useState("loading");
  const [compileError, setCompileError] = useState("");
  const [compiledModel, setCompiledModel] = useState(null);
  const [definition, setDefinition] = useState(null);
  const [paramValues, setParamValues] = useState({});
  const [graphics, setGraphics] = useState(() => normalizeImplicitGraphicsSettings(DEFAULT_IMPLICIT_GRAPHICS_SETTINGS));
  const [exportSettings, setExportSettings] = useState({ resolution: 72, frames: 18, duration: 4 });
  const [exportState, setExportState] = useState({ status: "idle", message: "" });
  const [activeAnimationId, setActiveAnimationId] = useState("");
  const [playing, setPlaying] = useState(false);
  const [animationElapsed, setAnimationElapsed] = useState(0);
  const [themeMode, setThemeMode] = useState(() => localStorage.getItem("implicit-demo-theme") || "dark");
  const [mobileTab, setMobileTab] = useState("source");
  const [previewBooted, setPreviewBooted] = useState(false);
  const [workspaceSplit, setWorkspaceSplit] = useState(50);
  const [controlsWidth, setControlsWidth] = useState(24);
  const workspaceRef = useRef(null);
  const templatesRef = useRef([]);
  const debouncedCode = useDebouncedValue(code, COMPILE_DEBOUNCE_MS);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", themeMode === "dark");
    localStorage.setItem("implicit-demo-theme", themeMode);
  }, [themeMode]);

  useEffect(() => {
    localStorage.setItem(SOURCE_STORAGE_KEY, code);
  }, [code]);

  useEffect(() => {
    let cancelled = false;
    let idleId = 0;
    const bootPreview = () => {
      if (!cancelled) {
        setPreviewBooted(true);
      }
    };

    const timeoutId = window.setTimeout(() => {
      if ("requestIdleCallback" in window) {
        idleId = window.requestIdleCallback(bootPreview, { timeout: 900 });
        return;
      }
      bootPreview();
    }, PREVIEW_BOOT_DELAY_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
      if (idleId && "cancelIdleCallback" in window) {
        window.cancelIdleCallback(idleId);
      }
    };
  }, []);

  const loadTemplate = useCallback(async (templateId, templateList) => {
    const list = Array.isArray(templateList) && templateList.length
      ? templateList
      : templatesRef.current;
    const template = list.find((candidate) => candidate.id === templateId) || list[0];
    if (!template) {
      return;
    }
    setSelectedTemplateId(template.id);
    setCompileState("loading");
    const source = template.id === EMPTY_TEMPLATE_ID
      ? EMPTY_TEMPLATE_SOURCE
      : await (async () => {
          const response = await fetch(`/examples/${template.file}?v=${Date.now()}`);
          if (!response.ok) {
            throw new Error(`Could not load ${template.file}`);
          }
          return response.text();
        })();
    setLoadedTemplateCode(source);
    setCode(source);
    setPlaying(false);
    setAnimationElapsed(0);
    setExportState({ status: "idle", message: "" });
  }, []);

  const handleSourceChange = useCallback((source) => {
    setCode(source);
    if (source !== loadedTemplateCode) {
      setSelectedTemplateId("");
    }
  }, [loadedTemplateCode]);

  useEffect(() => {
    let cancelled = false;
    fetch("/examples/index.json")
      .then((response) => {
        if (!response.ok) {
          throw new Error("Example manifest was not found.");
        }
        return response.json();
      })
      .then(async (manifest) => {
        if (cancelled) {
          return;
        }
        const list = [EMPTY_TEMPLATE, ...(Array.isArray(manifest) ? manifest : [])];
        templatesRef.current = list;
        setTemplates(list);
        if (!initialSourceRef.current.trim()) {
          await loadTemplate(DEFAULT_TEMPLATE_ID, list);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setCompileState("error");
          setCompileError(errorMessage(error));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [loadTemplate]);

  useEffect(() => {
    if (!debouncedCode.trim()) {
      setCompiledModel(null);
      setDefinition(null);
      setCompileState("idle");
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setCompileState("compiling");
    setCompileError("");

    loadImplicitSource(debouncedCode, {
      signal: controller.signal,
      sourceUrl: "editor://implicit.implicit.js"
    }).then((model) => {
      if (cancelled) {
        return;
      }
      const nextDefinition = model.definition || null;
      setCompiledModel(model);
      setDefinition(nextDefinition);
      setParamValues((previousValues) => nextDefinition
        ? normalizeParameterValues(nextDefinition, previousValues)
        : {});
      setActiveAnimationId((previousId) => {
        const animations = nextDefinition?.animations || [];
        return animations.some((animation) => animation.id === previousId)
          ? previousId
          : animations[0]?.id || "";
      });
      setCompileState("ready");
      setExportState((current) => current.status === "error" ? { status: "idle", message: "" } : current);
    }).catch((error) => {
      if (cancelled || error?.name === "AbortError") {
        return;
      }
      setCompiledModel(null);
      setDefinition(null);
      setCompileState("error");
      setCompileError(errorMessage(error));
    });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [debouncedCode]);

  useEffect(() => {
    if (!playing) {
      return undefined;
    }
    let rafId = 0;
    let previousTime = performance.now();
    const tick = (time) => {
      const delta = Math.min(Math.max((time - previousTime) / 1000, 0), 0.1);
      previousTime = time;
      setAnimationElapsed((elapsed) => elapsed + delta);
      rafId = window.requestAnimationFrame(tick);
    };
    rafId = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(rafId);
  }, [playing]);

  const activeAnimation = useMemo(() => {
    const animations = definition?.animations || [];
    return animations.find((animation) => animation.id === activeAnimationId) || animations[0] || null;
  }, [activeAnimationId, definition]);

  useEffect(() => {
    if (activeAnimation?.duration) {
      setExportSettings((current) => ({
        ...current,
        duration: Number(activeAnimation.duration.toFixed?.(2) || activeAnimation.duration)
      }));
    }
  }, [activeAnimation?.id, activeAnimation?.duration]);

  const animatedValues = useMemo(() => (
    activeAnimation && (playing || animationElapsed > 0)
      ? animatedImplicitParameterValues(definition, activeAnimation, paramValues, animationElapsed)
      : normalizeParameterValues(definition, paramValues)
  ), [activeAnimation, animationElapsed, definition, paramValues, playing]);

  const liveResult = useMemo(() => {
    if (!definition) {
      return { model: compiledModel, error: "" };
    }
    try {
      return {
        model: definition.buildModel(animatedValues, {
          activeId: activeAnimation?.id || "",
          playing,
          elapsedSec: normalizeImplicitAnimationElapsed(animationElapsed, activeAnimation),
          speed: 1
        }),
        error: ""
      };
    } catch (error) {
      return {
        model: null,
        error: errorMessage(error)
      };
    }
  }, [activeAnimation, animatedValues, animationElapsed, compiledModel, definition, playing]);
  const liveModel = liveResult.model;
  const previewError = compileState === "error" ? compileError : liveResult.error;
  const previewStatus = previewError ? "error" : compileState;
  const previewModel = previewBooted && previewStatus === "ready" ? liveModel : null;

  const handleExportSettingChange = useCallback((id, value) => {
    setExportSettings((current) => ({
      ...current,
      [id]: id === "duration"
        ? clampNumber(value, 0.25, 20)
        : Math.floor(clampNumber(value, id === "frames" ? 4 : 24, id === "frames" ? 48 : 192))
    }));
  }, []);

  const handleExport = useCallback(async (format, { animated = false } = {}) => {
    if (!liveModel || previewError) {
      setExportState({ status: "error", message: "Fix the source before exporting." });
      return;
    }
    if (animated && !activeAnimation) {
      setExportState({ status: "error", message: "This source does not define an animation." });
      return;
    }
    setExportState({ status: "busy", message: animated ? "building animated GLB" : `building ${format.toUpperCase()}` });
    await new Promise((resolve) => window.requestAnimationFrame(resolve));
    try {
      const result = animated
        ? exportImplicitAnimatedGlb(liveModel, {
            animationId: activeAnimation?.id || "",
            params: paramValues,
            duration: exportSettings.duration,
            frames: exportSettings.frames,
            resolution: exportSettings.resolution,
          })
        : exportImplicitModel(liveModel, {
            format,
            resolution: exportSettings.resolution,
          });
      const extension = animated ? "animated.glb" : (result.extension || `.${format}`).replace(/^\./, "");
      const filename = `${safeFileStem(liveModel.name)}.${extension}`;
      downloadExport(result, filename);
      setExportState({
        status: "done",
        message: `${filename} exported`
      });
    } catch (error) {
      setExportState({ status: "error", message: errorMessage(error) });
    }
  }, [activeAnimation, exportSettings, liveModel, paramValues, previewError]);

  const handleParamChange = useCallback((parameterId, value) => {
    setParamValues((currentValues) => {
      const parameter = definition?.parameterMap?.[parameterId];
      if (!parameter) {
        return currentValues;
      }
      return {
        ...currentValues,
        [parameterId]: normalizeParameterValue(parameter, value)
      };
    });
  }, [definition]);

  const handleGraphicsChange = useCallback((id, value) => {
    setGraphics((currentSettings) => normalizeImplicitGraphicsSettings({
      ...currentSettings,
      [id]: value
    }));
  }, []);

  const handleTemplateChange = useCallback((templateId) => {
    loadTemplate(templateId).catch((error) => {
      setCompileState("error");
      setCompileError(errorMessage(error));
    });
  }, [loadTemplate]);

  const handleDownloadSource = useCallback(() => {
    const template = templates.find((candidate) => candidate.id === selectedTemplateId);
    const filename = template?.file || `${safeFileStem(liveModel?.name || "implicit-model")}.implicit.js`;
    downloadExport({
      body: code,
      contentType: "text/javascript;charset=utf-8"
    }, filename);
  }, [code, liveModel?.name, selectedTemplateId, templates]);

  return (
    <div
      className="app-shell"
      style={{
        "--workspace-left": `${workspaceSplit}%`,
        "--controls-width": `${controlsWidth}%`
      }}
    >
      <header className="top-rail">
        <div className="brand-block compact-brand">
          <img className="brand-mark" src="/favicon.svg" alt="" aria-hidden="true" />
          <strong className="brand-word">implicit.js</strong>
          <span className="brand-subtitle">model 3d objects with math</span>
        </div>
        <div className="top-actions">
          <IconButton
            title={themeMode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            onClick={() => setThemeMode((mode) => mode === "dark" ? "light" : "dark")}
          >
            {themeMode === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </IconButton>
        </div>
      </header>

      <nav className="mobile-tabs" aria-label="Mobile sections">
        {MOBILE_TABS.map(([id, label]) => (
          <button
            className={mobileTab === id ? "active" : ""}
            key={id}
            type="button"
            onClick={() => setMobileTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      <main className="workspace-grid" ref={workspaceRef}>
        <section className="editor-shell">
          <CodePane
            active={mobileTab === "source"}
            onDownloadSource={handleDownloadSource}
            onTemplateChange={handleTemplateChange}
            selectedTemplateId={selectedTemplateId}
            themeMode={themeMode}
            templates={templates}
            value={code}
            onChange={handleSourceChange}
          />
        </section>

        <SplitHandle
          axis="x"
          className="workspace-resizer"
          title="Resize editors and preview"
          onPointerDown={(event) => beginSplitDrag(event, {
            axis: "x",
            containerRef: workspaceRef,
            min: 30,
            max: 68,
            setValue: setWorkspaceSplit
          })}
        />

        <section className={`visual-shell mobile-panel ${mobileTab === "preview" ? "is-active" : ""}`}>
          <section className="viewport-frame">
            <div className="section-bar">
              <div className="section-title-row">
                <span>Preview</span>
              </div>
              <div className="preview-actions">
                <button
                  className="section-text-button"
                  disabled={!liveModel || Boolean(previewError) || exportState.status === "busy"}
                  type="button"
                  onClick={() => handleExport("glb")}
                >
                  <Download size={13} />
                  GLB
                </button>
                <button
                  className="section-text-button"
                  disabled={!liveModel || Boolean(previewError) || exportState.status === "busy"}
                  type="button"
                  onClick={() => handleExport("3mf")}
                >
                  <Download size={13} />
                  3MF
                </button>
                <button
                  className="section-text-button"
                  disabled={!liveModel || Boolean(previewError) || exportState.status === "busy"}
                  type="button"
                  onClick={() => handleExport("stl")}
                >
                  <Download size={13} />
                  STL
                </button>
                <button
                  className="section-text-button"
                  disabled={!liveModel || !activeAnimation || Boolean(previewError) || exportState.status === "busy"}
                  type="button"
                  onClick={() => handleExport("glb", { animated: true })}
                >
                  <Film size={13} />
                  animated
                </button>
              </div>
            </div>
            <div className="viewport-canvas-area">
              {previewBooted ? (
                <Suspense fallback={<PreviewLoadingHost />}>
                  <ImplicitViewport
                    model={previewModel}
                    graphics={graphics}
                    themeMode={themeMode}
                  />
                </Suspense>
              ) : (
                <PreviewLoadingHost />
              )}
              {previewError ? (
                <ScrollArea className="compile-error">
                  <div className="compile-error-content">
                    <FieldLabel>{compileState === "error" ? "compile error" : "runtime error"}</FieldLabel>
                    <pre>{previewError}</pre>
                  </div>
                </ScrollArea>
              ) : null}
              {exportState.message ? (
                <div className={`export-toast export-${exportState.status}`}>
                  {exportState.message}
                </div>
              ) : null}
            </div>
          </section>
        </section>

        <SplitHandle
          axis="x"
          className="controls-resizer"
          title="Resize preview and controls"
          onPointerDown={(event) => beginTrailingSplitDrag(event, {
            containerRef: workspaceRef,
            min: 18,
            max: 40,
            setValue: setControlsWidth
          })}
        />

        <section className={`controls-shell mobile-panel ${mobileTab === "controls" ? "is-active" : ""}`}>
          <InspectorPanel
            model={liveModel}
            definition={definition}
            paramValues={paramValues}
            animatedValues={animatedValues}
            onParamChange={handleParamChange}
            graphics={graphics}
            onGraphicsChange={handleGraphicsChange}
            activeAnimationId={activeAnimation?.id || ""}
            onAnimationChange={(animationId) => {
              setActiveAnimationId(animationId);
              setAnimationElapsed(0);
            }}
            playing={playing}
            onTogglePlaying={() => setPlaying((value) => !value)}
            onResetAnimation={() => {
              setPlaying(false);
              setAnimationElapsed(0);
            }}
            exportSettings={exportSettings}
            onExportSettingChange={handleExportSettingChange}
            runtimeError={previewError}
          />
        </section>
      </main>
    </div>
  );
}
