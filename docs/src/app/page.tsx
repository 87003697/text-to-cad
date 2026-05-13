import Image from "next/image";
import { ExternalLink } from "lucide-react";
import { CopyButton } from "@/components/copy-button";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

const quickStartCommands = [
  "git clone https://github.com/earthtojake/text-to-cad.git",
  "cd text-to-cad",
  "./scripts/codex-install.sh",
];

const providerInstalls = [
  {
    name: "Codex",
    command: "./scripts/codex-install.sh",
    destination: "${CODEX_HOME:-$HOME/.codex}/skills",
  },
  {
    name: "Claude Code",
    command: "./scripts/claude-install.sh",
    destination: "${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}",
  },
  {
    name: "Gemini CLI",
    command: "./scripts/gemini-install.sh",
    destination: "${GEMINI_SKILLS_DIR:-.gemini/skills}",
  },
  {
    name: "OpenClaw",
    command: "./scripts/openclaw-install.sh",
    destination: "${OPENCLAW_SKILLS_DIR:-$HOME/.openclaw/skills}",
  },
  {
    name: "Universal installer",
    command: "npx agent-skills-cli add earthtojake/text-to-cad",
    destination: "Supported local agent skill roots",
  },
];

const installOptions = [
  ["List bundled skills", "./scripts/install.sh --list"],
  ["Install one skill", "./scripts/install.sh --agent codex --skill cad"],
  ["Preview changes", "./scripts/install.sh --agent all --dry-run"],
  ["Use symlinks", "./scripts/install.sh --agent project --link"],
];

const skillGroups = [
  {
    orbitSrc: "/skill-logos/cad-orbit.gif",
    name: "CAD",
    path: "skills/cad",
    summary:
      "Builds and edits parametric CAD from natural-language requirements, with STEP as the primary checked output.",
    details:
      "Use it for mechanical parts, assemblies, fixtures, enclosures, measurements, @cad references, and secondary DXF/STL/3MF/GLB exports.",
  },
  {
    orbitSrc: "/skill-logos/cad-explorer-orbit.gif",
    name: "CAD Explorer",
    path: "skills/cad-explorer",
    summary:
      "Opens local visual review links for generated CAD and robot-description files.",
    details:
      "Use it when you want to inspect STEP, STP, STL, 3MF, DXF, URDF, SRDF, or SDF outputs in the browser.",
  },
  {
    orbitSrc: "/skill-logos/urdf-orbit.gif",
    name: "URDF",
    path: "skills/urdf",
    summary:
      "Generates robot structure descriptions from Python gen_urdf() sources.",
    details:
      "Use it for links, joints, limits, inertials, mesh references, visual/collision geometry, and frame semantics.",
  },
  {
    orbitSrc: "/skill-logos/srdf-orbit.gif",
    name: "SRDF",
    path: "skills/srdf",
    summary:
      "Adds MoveIt planning semantics on top of an existing valid URDF.",
    details:
      "Use it for planning groups, virtual joints, passive joints, end effectors, group states, disabled collisions, IK, and path-planning semantics.",
  },
  {
    orbitSrc: "/skill-logos/sdf-orbit.gif",
    name: "SDF",
    path: "skills/sdf",
    summary:
      "Creates SDFormat models and worlds for simulator-specific behavior.",
    details:
      "Use it for poses, frames, physics, sensors, lights, plugins, mesh URIs, and world or model layout.",
  },
  {
    orbitSrc: "/skill-logos/sendcutsend-orbit.gif",
    name: "SendCutSend",
    path: "skills/sendcutsend",
    summary:
      "Preflights DXF and STEP/STP files for SendCutSend orders.",
    details:
      "Use it for laser cutting, CNC routing, bending, tapping, countersinking, hardware insertion, finishing, and upload readiness.",
  },
];

function CodeBlock({
  lines,
  label,
}: {
  lines: string[];
  label?: string;
}) {
  const code = lines.join("\n");

  return (
    <div className="relative min-w-0 max-w-full">
      <pre className="min-w-0 max-w-full overflow-x-auto rounded-md border border-[color:var(--border)] bg-[var(--muted)] px-4 py-3 pr-14 text-sm leading-6 text-[var(--foreground)]">
        <code>{code}</code>
      </pre>
      <div className="absolute right-2 top-2">
        <CopyButton text={code} label={label ?? "Copy commands"} />
      </div>
    </div>
  );
}

function CommandLine({ command }: { command: string }) {
  return <CodeBlock lines={[command]} label="Copy command" />;
}

export default function Home() {
  return (
    <main className="min-h-screen bg-[var(--background)] text-[var(--foreground)]">
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-5 py-6 sm:px-8 sm:py-8">
        <SiteHeader />
        <div className="flex flex-1 flex-col gap-20 py-14 sm:py-20">
          <section
            id="skills"
            aria-labelledby="skills-title"
            className="space-y-8"
          >
            <div className="max-w-3xl">
              <h2
                id="skills-title"
                className="text-3xl font-semibold tracking-normal sm:text-4xl"
              >
                Skills
              </h2>
              <p className="mt-4 text-sm leading-6 text-[var(--muted-foreground)]">
                Agents use CAD skills to generate and render 3D models, robot
                description files, and more.
              </p>
            </div>
            <ul className="divide-y divide-[color:var(--border)] border-y border-[color:var(--border)]">
              {skillGroups.map((skill) => {
                return (
                  <li key={skill.name} className="py-6">
                    <div className="grid gap-4 md:grid-cols-[2.5rem_minmax(0,1fr)_auto]">
                      <div
                        aria-hidden="true"
                        className="relative size-10 overflow-hidden rounded-[12px] border border-primary/30 bg-primary/10"
                      >
                        <Image
                          src={skill.orbitSrc}
                          alt=""
                          width={160}
                          height={160}
                          unoptimized
                          className="size-full object-contain p-0.5"
                        />
                      </div>
                      <div className="min-w-0">
                        <h3 className="text-xl font-semibold tracking-normal">
                          {skill.name}
                        </h3>
                        <p className="mt-3 text-sm leading-6">
                          {skill.summary}
                        </p>
                        <p className="mt-2 text-sm leading-6 text-[var(--muted-foreground)]">
                          {skill.details}
                        </p>
                      </div>
                      <a
                        className="inline-flex w-fit items-center gap-1.5 self-start text-sm font-semibold text-[var(--foreground)] underline underline-offset-4 transition hover:text-[var(--muted-foreground)] md:justify-self-end"
                        href={`https://github.com/earthtojake/text-to-cad/blob/main/${skill.path}/SKILL.md`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <span>{skill.path}</span>
                        <ExternalLink className="size-3.5" />
                      </a>
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>

          <section
            id="installation"
            aria-labelledby="installation-title"
            className="grid gap-8 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]"
          >
            <div>
              <h2
                id="installation-title"
                className="text-3xl font-semibold tracking-normal sm:text-4xl"
              >
                Install
              </h2>
              <p className="mt-4 text-sm leading-6 text-[var(--muted-foreground)]">
                Clone the repo once, then run the installer for the agent you
                use. Restart the agent if newly installed skills do not appear.
              </p>
              <p className="mt-4 text-sm leading-6">
                <a
                  className="font-semibold underline underline-offset-4 hover:text-[var(--muted-foreground)]"
                  href="https://github.com/earthtojake/text-to-cad/blob/main/INSTALLATION.md"
                  target="_blank"
                  rel="noreferrer"
                >
                  Read the full installation guide
                </a>
              </p>
            </div>
            <div className="min-w-0 space-y-6">
              <CodeBlock lines={quickStartCommands} />
              <div className="divide-y divide-[color:var(--border)] border-y border-[color:var(--border)]">
                {providerInstalls.map((provider) => (
                  <div
                    key={provider.name}
                    className="grid gap-3 py-4 sm:grid-cols-[10rem_1fr]"
                  >
                    <div className="font-medium">{provider.name}</div>
                    <div className="min-w-0 space-y-2">
                      <CommandLine command={provider.command} />
                      <p className="text-xs leading-5 text-[var(--muted-foreground)]">
                        Installs to {provider.destination}.
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section
            aria-labelledby="installer-title"
            className="grid gap-8 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]"
          >
            <div>
              <p className="text-sm uppercase text-[var(--muted-foreground)]">
                Installer controls
              </p>
              <h2
                id="installer-title"
                className="mt-3 text-2xl font-semibold tracking-normal"
              >
                Target one skill or one workspace
              </h2>
              <p className="mt-4 text-sm leading-6 text-[var(--muted-foreground)]">
                The shared installer supports specific agents, one-skill
                installs, dry runs, forced replacement, and symlinked
                project-local setups.
              </p>
            </div>
            <div className="min-w-0 divide-y divide-[color:var(--border)] border-y border-[color:var(--border)]">
              {installOptions.map(([label, command]) => (
                <div
                  key={label}
                  className="grid gap-3 py-4 sm:grid-cols-[10rem_1fr]"
                >
                  <div className="text-sm text-[var(--muted-foreground)]">
                    {label}
                  </div>
                  <CommandLine command={command} />
                </div>
              ))}
            </div>
          </section>

          <section
            id="local-development"
            aria-labelledby="local-development-title"
            className="grid gap-8 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]"
          >
            <div>
              <p className="text-sm uppercase text-[var(--muted-foreground)]">
                Local development
              </p>
              <h2
                id="local-development-title"
                className="mt-3 text-2xl font-semibold tracking-normal"
              >
                Optional CAD runtime setup
              </h2>
              <p className="mt-4 text-sm leading-6 text-[var(--muted-foreground)]">
                Skill installation gives the agent the workflows. Local CAD
                generation also needs the Python CAD dependencies and CAD
                Explorer dependencies installed in this repo.
              </p>
            </div>
            <CodeBlock
              lines={[
                "python3.11 -m venv .venv",
                "./.venv/bin/python -m pip install --upgrade pip",
                "./.venv/bin/pip install -r skills/cad/requirements.txt",
                "npm --prefix skills/cad-explorer/scripts/explorer install",
              ]}
            />
          </section>
        </div>
        <SiteFooter />
      </div>
    </main>
  );
}
