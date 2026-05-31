import {
  Activity,
  Box,
  Check,
  Code2,
  Moon,
  Pause,
  Play,
  RefreshCcw,
  RotateCcw,
  SlidersHorizontal,
  Sun,
  Zap
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DEFAULT_IMPLICIT_GRAPHICS_SETTINGS,
  IMPLICIT_GRAPHICS_LIMITS,
  loadImplicitSource,
  normalizeImplicitGraphicsSettings,
  normalizeParameterValue,
  normalizeParameterValues
} from "implicitjs";

import { ImplicitViewport } from "./ImplicitViewport.jsx";

const DEFAULT_EXAMPLE_ID = "mobius-strip";
const COMPILE_DEBOUNCE_MS = 260;

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

function normalizeAnimationElapsed(elapsedSec, animation) {
  const duration = Math.max(Number(animation?.duration) || 1, 0.001);
  const elapsed = Math.max(Number(elapsedSec) || 0, 0);
  return animation?.loop === false
    ? Math.min(elapsed, duration)
    : elapsed % duration;
}

function animatedParameterValues(definition, animation, baseValues, elapsedSec) {
  if (!definition || typeof animation?.update !== "function") {
    return baseValues;
  }
  const normalizedBase = normalizeParameterValues(definition, baseValues);
  const duration = Math.max(Number(animation.duration) || 1, 0.001);
  const elapsed = normalizeAnimationElapsed(elapsedSec, animation);
  const nextValues = { ...normalizedBase };
  const set = (parameterId, value) => {
    const id = String(parameterId || "").trim();
    const parameter = definition.parameterMap?.[id];
    if (parameter) {
      nextValues[id] = normalizeParameterValue(parameter, value);
    }
  };

  animation.update({
    ...normalizedBase,
    elapsed,
    elapsedSec: elapsed,
    duration,
    progress: clampNumber(elapsed / duration, 0, 1),
    cycle: elapsed / duration,
    t: elapsed,
    time: elapsed,
    loop: animation.loop !== false,
    params: normalizedBase,
    set
  });

  return nextValues;
}

function lineNumbersForCode(code) {
  const lineCount = Math.max(String(code || "").split("\n").length, 1);
  return Array.from({ length: lineCount }, (_, index) => index + 1).join("\n");
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

function StatusBadge({ state, label }) {
  return (
    <span className={`status-badge status-${state}`}>
      <span className="status-dot" />
      {label}
    </span>
  );
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

function CodeEditor({ code, onChange, examples, selectedExampleId, onSelectExample, onReloadExample, compileState }) {
  const textareaRef = useRef(null);
  const gutterRef = useRef(null);

  const handleKeyDown = (event) => {
    if (event.key !== "Tab") {
      return;
    }
    event.preventDefault();
    const target = event.currentTarget;
    const start = target.selectionStart;
    const end = target.selectionEnd;
    const nextCode = `${code.slice(0, start)}  ${code.slice(end)}`;
    onChange(nextCode);
    window.requestAnimationFrame(() => {
      target.selectionStart = start + 2;
      target.selectionEnd = start + 2;
    });
  };

  const handleScroll = (event) => {
    if (gutterRef.current) {
      gutterRef.current.scrollTop = event.currentTarget.scrollTop;
    }
  };

  return (
    <section className="editor-shell">
      <div className="panel-header">
        <div>
          <span className="panel-kicker">source module</span>
          <h2><Code2 size={17} /> implicit.js editor</h2>
        </div>
        <StatusBadge
          state={compileState === "ready" ? "ok" : compileState === "error" ? "error" : "busy"}
          label={compileState}
        />
      </div>

      <div className="editor-actions">
        <label className="select-field">
          <FieldLabel>examples</FieldLabel>
          <select value={selectedExampleId} onChange={(event) => onSelectExample(event.target.value)}>
            {examples.map((example) => (
              <option key={example.id} value={example.id}>{example.label}</option>
            ))}
          </select>
        </label>
        <button className="text-button" type="button" onClick={onReloadExample}>
          <RefreshCcw size={15} />
          reload
        </button>
      </div>

      <div className="code-frame">
        <pre ref={gutterRef} className="line-gutter" aria-hidden="true">{lineNumbersForCode(code)}</pre>
        <textarea
          ref={textareaRef}
          spellCheck={false}
          value={code}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          onScroll={handleScroll}
          aria-label="Implicit JavaScript source editor"
        />
      </div>
    </section>
  );
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
  runtimeError
}) {
  const parameters = definition?.parameters || [];
  const animations = definition?.animations || [];
  const summary = compileSummary(model);

  return (
    <aside className="inspector">
      <div className="panel-header compact">
        <div>
          <span className="panel-kicker">runtime</span>
          <h2><SlidersHorizontal size={16} /> controls</h2>
        </div>
      </div>

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
    </aside>
  );
}

export default function App() {
  const [examples, setExamples] = useState([]);
  const [selectedExampleId, setSelectedExampleId] = useState(DEFAULT_EXAMPLE_ID);
  const [code, setCode] = useState("");
  const [loadedExampleCode, setLoadedExampleCode] = useState("");
  const [compileState, setCompileState] = useState("loading");
  const [compileError, setCompileError] = useState("");
  const [compiledModel, setCompiledModel] = useState(null);
  const [definition, setDefinition] = useState(null);
  const [paramValues, setParamValues] = useState({});
  const [graphics, setGraphics] = useState(() => normalizeImplicitGraphicsSettings(DEFAULT_IMPLICIT_GRAPHICS_SETTINGS));
  const [activeAnimationId, setActiveAnimationId] = useState("");
  const [playing, setPlaying] = useState(false);
  const [animationElapsed, setAnimationElapsed] = useState(0);
  const [themeMode, setThemeMode] = useState(() => localStorage.getItem("implicit-demo-theme") || "dark");
  const [cameraResetToken, setCameraResetToken] = useState(0);
  const selectedExampleRef = useRef(DEFAULT_EXAMPLE_ID);
  const examplesRef = useRef([]);
  const debouncedCode = useDebouncedValue(code, COMPILE_DEBOUNCE_MS);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", themeMode === "dark");
    localStorage.setItem("implicit-demo-theme", themeMode);
  }, [themeMode]);

  const loadExample = useCallback(async (exampleId, exampleList) => {
    const list = Array.isArray(exampleList) && exampleList.length
      ? exampleList
      : examplesRef.current;
    const example = list.find((candidate) => candidate.id === exampleId) || list[0];
    if (!example) {
      return;
    }
    selectedExampleRef.current = example.id;
    setSelectedExampleId(example.id);
    setCompileState("loading");
    const response = await fetch(`/examples/${example.file}?v=${Date.now()}`);
    if (!response.ok) {
      throw new Error(`Could not load ${example.file}`);
    }
    const source = await response.text();
    setLoadedExampleCode(source);
    setCode(source);
    setPlaying(false);
    setAnimationElapsed(0);
  }, []);

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
        const list = Array.isArray(manifest) ? manifest : [];
        examplesRef.current = list;
        setExamples(list);
        await loadExample(DEFAULT_EXAMPLE_ID, list);
      })
      .catch((error) => {
        if (!cancelled) {
          setCompileState("error");
          setCompileError(error instanceof Error ? error.message : String(error));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [loadExample]);

  useEffect(() => {
    if (!debouncedCode.trim()) {
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setCompileState("compiling");
    setCompileError("");

    loadImplicitSource(debouncedCode, {
      signal: controller.signal,
      sourceUrl: `editor://${selectedExampleRef.current || "inline"}.implicit.js`
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
    }).catch((error) => {
      if (cancelled || error?.name === "AbortError") {
        return;
      }
      setCompileState("error");
      setCompileError(error instanceof Error ? error.message : String(error));
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

  const animatedValues = useMemo(() => (
    activeAnimation && (playing || animationElapsed > 0)
      ? animatedParameterValues(definition, activeAnimation, paramValues, animationElapsed)
      : normalizeParameterValues(definition, paramValues)
  ), [activeAnimation, animationElapsed, definition, paramValues, playing]);

  const liveModel = useMemo(() => {
    if (!definition) {
      return compiledModel;
    }
    try {
      return definition.buildModel(animatedValues, {
        activeId: activeAnimation?.id || "",
        playing,
        elapsedSec: normalizeAnimationElapsed(animationElapsed, activeAnimation),
        speed: 1
      });
    } catch {
      return compiledModel;
    }
  }, [activeAnimation, animatedValues, animationElapsed, compiledModel, definition, playing]);

  const dirty = code !== loadedExampleCode;
  const selectedExample = examples.find((example) => example.id === selectedExampleId);

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

  const reloadSelectedExample = useCallback(() => {
    loadExample(selectedExampleId).catch((error) => {
      setCompileState("error");
      setCompileError(error instanceof Error ? error.message : String(error));
    });
  }, [loadExample, selectedExampleId]);

  return (
    <div className="app-shell">
      <header className="top-rail">
        <div className="brand-block">
          <div className="brand-mark"><Box size={18} /></div>
          <div>
            <span className="panel-kicker">standalone package test</span>
            <h1>implicitjs workbench</h1>
          </div>
        </div>
        <div className="top-actions">
          <div className="compile-readout">
            {compileState === "ready" ? <Check size={15} /> : compileState === "error" ? <Zap size={15} /> : <Activity size={15} />}
            <span>{compileState === "ready" ? "live shader ready" : compileState}</span>
          </div>
          <IconButton
            title={themeMode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            onClick={() => setThemeMode((mode) => mode === "dark" ? "light" : "dark")}
          >
            {themeMode === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </IconButton>
        </div>
      </header>

      <main className="workspace-grid">
        <CodeEditor
          code={code}
          onChange={setCode}
          examples={examples}
          selectedExampleId={selectedExampleId}
          onSelectExample={(exampleId) => loadExample(exampleId).catch((error) => {
            setCompileState("error");
            setCompileError(error instanceof Error ? error.message : String(error));
          })}
          onReloadExample={reloadSelectedExample}
          compileState={compileState}
        />

        <section className="visual-shell">
          <div className="viewport-topbar">
            <div>
              <span className="panel-kicker">visualizer</span>
              <h2>{liveModel?.name || "waiting for source"}</h2>
            </div>
            <div className="viewport-actions">
              {dirty ? <span className="status-badge status-busy"><span className="status-dot" /> edited</span> : null}
              <IconButton title="Reset camera" onClick={() => setCameraResetToken((token) => token + 1)}>
                <RotateCcw size={16} />
              </IconButton>
            </div>
          </div>

          <div className="visual-body">
            <div className="viewport-frame">
              <ImplicitViewport
                model={compileState === "ready" ? liveModel : null}
                graphics={graphics}
                themeMode={themeMode}
                cameraResetToken={cameraResetToken}
              />
              {compileState === "error" ? (
                <div className="compile-error">
                  <FieldLabel>compile error</FieldLabel>
                  <pre>{compileError}</pre>
                </div>
              ) : null}
              {selectedExample ? (
                <div className="example-caption">
                  <strong>{selectedExample.label}</strong>
                  <span>{selectedExample.description}</span>
                </div>
              ) : null}
            </div>
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
              runtimeError={compileState === "error" ? compileError : ""}
            />
          </div>
        </section>
      </main>
    </div>
  );
}
