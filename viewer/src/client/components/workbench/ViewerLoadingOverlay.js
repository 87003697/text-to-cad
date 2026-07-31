import { useEffect, useState } from "react";

import { formatArtifactProgress, renderProgressBar } from "@/workbench/artifactProgress.js";

// Extracted from the published Claude spinner verb list the user linked.
const CLAUDE_CODE_LOADING_VERBS = Object.freeze([
  "Accomplishing",
  "Actioning",
  "Actualizing",
  "Architecting",
  "Baking",
  "Beaming",
  "Beboppin'",
  "Befuddling",
  "Billowing",
  "Blanching",
  "Bloviating",
  "Boogieing",
  "Boondoggling",
  "Booping",
  "Bootstrapping",
  "Brewing",
  "Bunning",
  "Burrowing",
  "Calculating",
  "Canoodling",
  "Caramelizing",
  "Cascading",
  "Catapulting",
  "Cerebrating",
  "Channeling",
  "Channelling",
  "Choreographing",
  "Churning",
  "Coalescing",
  "Cogitating",
  "Combobulating",
  "Composing",
  "Computing",
  "Concocting",
  "Considering",
  "Contemplating",
  "Cooking",
  "Crafting",
  "Creating",
  "Crunching",
  "Crystallizing",
  "Cultivating",
  "Deciphering",
  "Deliberating",
  "Determining",
  "Dilly-dallying",
  "Discombobulating",
  "Doing",
  "Doodling",
  "Drizzling",
  "Ebbing",
  "Effecting",
  "Elucidating",
  "Embellishing",
  "Enchanting",
  "Envisioning",
  "Evaporating",
  "Fermenting",
  "Fiddle-faddling",
  "Finagling",
  "Flambéing",
  "Flibbertigibbeting",
  "Flowing",
  "Flummoxing",
  "Fluttering",
  "Forging",
  "Forming",
  "Frolicking",
  "Frosting",
  "Gallivanting",
  "Galloping",
  "Garnishing",
  "Generating",
  "Gesticulating",
  "Germinating",
  "Gitifying",
  "Grooving",
  "Gusting",
  "Harmonizing",
  "Hashing",
  "Hatching",
  "Herding",
  "Honking",
  "Hullaballooing",
  "Hyperspacing",
  "Ideating",
  "Imagining",
  "Improvising",
  "Incubating",
  "Inferring",
  "Infusing",
  "Ionizing",
  "Jitterbugging",
  "Julienning",
  "Kneading",
  "Leavening",
  "Levitating",
  "Lollygagging",
  "Manifesting",
  "Marinating",
  "Meandering",
  "Metamorphosing",
  "Misting",
  "Moonwalking",
  "Moseying",
  "Mulling",
  "Mustering",
  "Musing",
  "Nebulizing",
  "Nesting",
  "Newspapering",
  "Noodling",
  "Nucleating",
  "Orbiting",
  "Orchestrating",
  "Osmosing",
  "Perambulating",
  "Percolating",
  "Perusing",
  "Philosophising",
  "Photosynthesizing",
  "Pollinating",
  "Pondering",
  "Pontificating",
  "Pouncing",
  "Precipitating",
  "Prestidigitating",
  "Processing",
  "Proofing",
  "Propagating",
  "Puttering",
  "Puzzling",
  "Quantumizing",
  "Razzle-dazzling",
  "Razzmatazzing",
  "Recombobulating",
  "Reticulating",
  "Roosting",
  "Ruminating",
  "Sautéing",
  "Scampering",
  "Schlepping",
  "Scurrying",
  "Seasoning",
  "Shenaniganing",
  "Shimmying",
  "Simmering",
  "Skedaddling",
  "Sketching",
  "Slithering",
  "Smooshing",
  "Sock-hopping",
  "Spelunking",
  "Spinning",
  "Sprouting",
  "Stewing",
  "Sublimating",
  "Swirling",
  "Swooping",
  "Symbioting",
  "Synthesizing",
  "Tempering",
  "Thinking",
  "Thundering",
  "Tinkering",
  "Tomfoolering",
  "Topsy-turvying",
  "Transfiguring",
  "Transmuting",
  "Twisting",
  "Undulating",
  "Unfurling",
  "Unravelling",
  "Vibing",
  "Waddling",
  "Wandering",
  "Warping",
  "Whatchamacalliting",
  "Whirlpooling",
  "Whirring",
  "Whisking",
  "Wibbling",
  "Working",
  "Wrangling",
  "Zesting",
  "Zigzagging"
]);

function shuffledLoadingVerbs() {
  const verbs = [...CLAUDE_CODE_LOADING_VERBS];
  for (let index = verbs.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [verbs[index], verbs[swapIndex]] = [verbs[swapIndex], verbs[index]];
  }
  return verbs;
}

export default function ViewerLoadingOverlay({
  viewerLoading,
  previewMode,
  progress = null
}) {
  const [loadingVerbs, setLoadingVerbs] = useState(() => shuffledLoadingVerbs());
  const [activeWordIndex, setActiveWordIndex] = useState(0);
  const [spinnerFrameIndex, setSpinnerFrameIndex] = useState(0);
  const [ellipsisFrameIndex, setEllipsisFrameIndex] = useState(0);

  const ASCII_SPINNER_FRAMES = ["|", "/", "-", "\\"];
  const ASCII_ELLIPSIS_FRAMES = ["", ".", "..", "..."];
  const OVERLAY_BAR_CELLS = 21;

  useEffect(() => {
    if (!viewerLoading || previewMode) {
      return undefined;
    }

    const nextLoadingVerbs = shuffledLoadingVerbs();
    setLoadingVerbs(nextLoadingVerbs);
    setActiveWordIndex(0);
    setSpinnerFrameIndex(0);
    setEllipsisFrameIndex(0);

    const verbIntervalId = window.setInterval(() => {
      setActiveWordIndex((current) => (current + 1) % nextLoadingVerbs.length);
    }, 900);

    const spinnerIntervalId = window.setInterval(() => {
      setSpinnerFrameIndex((current) => (current + 1) % ASCII_SPINNER_FRAMES.length);
    }, 90);

    const ellipsisIntervalId = window.setInterval(() => {
      setEllipsisFrameIndex((current) => (current + 1) % ASCII_ELLIPSIS_FRAMES.length);
    }, 360);

    return () => {
      window.clearInterval(verbIntervalId);
      window.clearInterval(spinnerIntervalId);
      window.clearInterval(ellipsisIntervalId);
    };
  }, [previewMode, viewerLoading]);

  if (!viewerLoading || previewMode) {
    return null;
  }

  const displayFrame = progress ? formatArtifactProgress(progress) : null;
  const activeVerb = loadingVerbs[activeWordIndex] || CLAUDE_CODE_LOADING_VERBS[0];
  // Cell counts, not pixels: the whole block is laid out in `ch` units, so the bar's
  // rendered length is cells x the mono advance width. OVERLAY_BAR_CELLS is set to hold
  // the bar at the same physical length it had one font step larger (18 cells at
  // text-sm) — shrinking the type must not shrink the bar with it.
  const spinnerFrame = ASCII_SPINNER_FRAMES[spinnerFrameIndex];
  const ellipsis = ASCII_ELLIPSIS_FRAMES[ellipsisFrameIndex];

  // Stacked rows on a fixed character grid. Every cell whose contents change — the
  // spinner, the ellipsis, the percent, the counts — sits in a reserved width, and the
  // two variable-length strings (the verb and the phase label) are the only things on
  // their rows. So nothing reflows as the build advances: the bar stays put while it
  // fills, and rows appear below the verb rather than widening it.
  return (
    <div className="pointer-events-none absolute inset-0 z-20 overflow-hidden">
      <div className="cad-loading-overlay absolute inset-0" />
      <div className="absolute inset-0 flex items-center justify-center px-4">
        <div
          role="status"
          aria-live="polite"
          className="cad-loading-ascii relative z-10 flex w-[30ch] max-w-[min(90vw,38rem)] flex-col gap-y-0.5 text-left font-mono text-xs leading-snug text-popover-foreground"
        >
          {/* Coarse on purpose: the phase changes a handful of times per build, so a
              screen reader is not read a new sentence every poll. The live value rides
              on the progressbar below, where it can be queried instead of announced. */}
          <span className="sr-only">{displayFrame?.label || activeVerb}</span>
          <span aria-hidden="true" className="flex w-full">
            <span className="inline-block w-[3ch]">{spinnerFrame}</span>
            <span>{activeVerb}</span>
            <span className="inline-block w-[3ch]">{ellipsis}</span>
          </span>
          {displayFrame ? (
            <>
              <span
                role="progressbar"
                aria-label={displayFrame.label}
                aria-valuenow={displayFrame.percent}
                aria-valuemin={0}
                aria-valuemax={100}
                className="flex w-full pl-[3ch]"
              >
                <span aria-hidden="true">{renderProgressBar(displayFrame.ratio, OVERLAY_BAR_CELLS)}</span>
                <span aria-hidden="true" className="inline-block w-[5ch] text-right tabular-nums">
                  {displayFrame.percent}%
                </span>
              </span>
              <span aria-hidden="true" className="flex w-full justify-between pl-[3ch] opacity-70">
                <span>{displayFrame.label}</span>
                <span className="tabular-nums">{displayFrame.counts}</span>
              </span>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
