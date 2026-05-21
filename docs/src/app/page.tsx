import Image from "next/image";
import { ExternalLink } from "lucide-react";
import { CopyButton } from "@/components/copy-button";
import { HeroSection } from "@/components/hero-section";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

const skillsCliCommand = "npx skills add earthtojake/text-to-cad";
const skillsShUrl = "https://www.skills.sh/";

const supportedAgents = [
  { name: "Claude Code", slug: "claude-code", icon: "claude-code.svg" },
  { name: "Cursor", slug: "cursor", icon: "cursor.svg" },
  { name: "Codex", slug: "codex", icon: "codex.svg" },
  { name: "GitHub Copilot", slug: "github-copilot", icon: "copilot.svg" },
  { name: "Windsurf", slug: "windsurf", icon: "windsurf.svg" },
  { name: "Gemini", slug: "gemini", icon: "gemini.svg" },
  { name: "Cline", slug: "cline", icon: "cline.svg" },
  { name: "AMP", slug: "amp", icon: "amp.svg" },
  { name: "Antigravity", slug: "antigravity", icon: "antigravity.svg" },
  { name: "ClawdBot", slug: "clawdbot", icon: "clawdbot.svg" },
  { name: "Droid", slug: "droid", icon: "droid.svg" },
  { name: "Goose", slug: "goose", icon: "goose.svg" },
  { name: "Kilo", slug: "kilo", icon: "kilo.svg" },
  { name: "Kiro CLI", slug: "kiro-cli", icon: "kiro-cli.svg" },
  { name: "OpenCode", slug: "opencode", icon: "opencode.svg" },
  { name: "Roo", slug: "roo", icon: "roo.svg" },
  { name: "Trae", slug: "trae", icon: "trae.svg" },
  { name: "VS Code", slug: "vscode", icon: "vscode.svg" },
];

const skillGroups = [
  {
    name: "CAD",
    path: "skills/cad",
    summary:
      "Creates and edits CAD models from plain-language requests, with STEP as the main output.",
  },
  {
    name: "Render",
    path: "skills/render",
    summary:
      "Shows local browser previews and snapshots for CAD and robot files.",
  },
  {
    name: "step.parts",
    path: "skills/step-parts",
    summary:
      "Finds off-the-shelf STEP parts like screws, bearings, motors, and connectors.",
  },
  {
    name: "URDF",
    path: "skills/urdf",
    summary:
      "Writes robot structure files with links, joints, limits, inertials, and meshes.",
  },
  {
    name: "SRDF",
    path: "skills/srdf",
    summary:
      "Adds MoveIt planning groups, end effectors, poses, and collision rules to a URDF.",
  },
  {
    name: "SDF",
    path: "skills/sdf",
    summary:
      "Creates simulator models and worlds with frames, physics, sensors, and lights.",
  },
  {
    name: "SendCutSend",
    path: "skills/sendcutsend",
    summary: "Checks DXF and STEP files before upload to SendCutSend.",
  },
];

function TryNowCommand() {
  return (
    <div className="flex min-h-[54px] min-w-0 max-w-full items-stretch border border-border bg-card">
      <code className="flex min-w-0 flex-1 items-center overflow-x-auto whitespace-nowrap px-3 text-sm leading-none text-foreground">
        <span className="mr-[1ch] text-muted-foreground">$</span>
        {skillsCliCommand}
      </code>
      <CopyButton text={skillsCliCommand} label="Copy install command" compact />
    </div>
  );
}

function AgentTile({
  agent,
  hidden = false,
}: {
  agent: (typeof supportedAgents)[number];
  hidden?: boolean;
}) {
  return (
    <a
      aria-hidden={hidden ? "true" : undefined}
      aria-label={`Skills for ${agent.name}`}
      className="group flex h-[54px] w-[150px] shrink-0 items-center gap-2.5 border border-border bg-card px-2.5 text-muted-foreground transition hover:bg-secondary hover:text-foreground sm:w-[168px]"
      href={`https://www.skills.sh/agent/${agent.slug}`}
      rel="noreferrer"
      tabIndex={hidden ? -1 : undefined}
      target="_blank"
    >
      <span className="flex size-7 shrink-0 items-center justify-center">
        <Image
          alt=""
          src={`https://www.skills.sh/agents/${agent.icon}`}
          width={44}
          height={44}
          unoptimized
          className="size-5 object-contain opacity-70 grayscale transition group-hover:opacity-100 group-hover:grayscale-0 dark:invert"
        />
      </span>
      <span className="min-w-0 truncate text-label uppercase tracking-[1.3px]">
        {agent.name}
      </span>
    </a>
  );
}

function AgentCarousel() {
  return (
    <div className="agent-carousel relative min-h-[54px] min-w-0 overflow-hidden">
      <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-12 bg-gradient-to-r from-background to-transparent sm:w-20" />
      <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-12 bg-gradient-to-l from-background to-transparent sm:w-20" />
      <div className="agent-carousel-track flex w-max gap-2">
        {supportedAgents.map((agent) => (
          <AgentTile key={agent.slug} agent={agent} />
        ))}
        {supportedAgents.map((agent) => (
          <AgentTile key={`${agent.slug}-duplicate`} agent={agent} hidden />
        ))}
      </div>
    </div>
  );
}

function SectionIntro({
  id,
  title,
  description,
}: {
  id?: string;
  title: string;
  description: string;
}) {
  return (
    <div>
      <h2
        id={id}
        className="text-heading font-medium tracking-normal text-foreground"
      >
        {title}
      </h2>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
        {description}
      </p>
    </div>
  );
}

function SkillLink({ skill }: { skill: (typeof skillGroups)[number] }) {
  return (
    <a
      className="inline-flex min-w-0 items-center gap-1.5 text-label uppercase tracking-[1.5px] text-primary transition hover:text-primary/80"
      href={`https://github.com/earthtojake/text-to-cad/blob/main/${skill.path}/SKILL.md`}
      target="_blank"
      rel="noreferrer"
    >
      <span className="truncate">{skill.path}</span>
      <ExternalLink className="size-3 shrink-0" />
    </a>
  );
}

export default function Home() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <SiteHeader />

      <div className="mx-auto w-full max-w-[1200px] px-4 py-4 sm:px-6">
        <div className="min-w-0 space-y-2">
          <HeroSection />

          <section
            aria-label="Install CAD Skills with supported agents"
            className="grid gap-5 py-6 lg:grid-cols-[minmax(18rem,0.8fr)_minmax(0,1.2fr)] lg:items-center lg:gap-12"
          >
            <div className="min-w-0 space-y-3">
              <h2 className="text-sm font-medium uppercase tracking-[1.5px] text-foreground">
                Try It Now
              </h2>
              <TryNowCommand />
            </div>

            <div className="min-w-0 space-y-3">
              <h2 className="text-sm font-medium uppercase tracking-[1.5px] text-foreground">
                Available For These Agents
              </h2>
              <AgentCarousel />
            </div>
          </section>

          <section
            id="skills"
            aria-labelledby="skills-title"
            className="scroll-mt-20 space-y-3 py-6"
          >
            <SectionIntro
              id="skills-title"
              title="SKILLS"
              description="Agents use CAD skills to generate, source, and render 3D models, robot description files, and more."
            />

            <div className="border border-border bg-card">
              <div className="grid grid-cols-[minmax(0,1fr)] border-b border-border px-3.5 py-2.5 text-xs uppercase tracking-[1.5px] text-muted-foreground md:grid-cols-[minmax(9rem,12rem)_minmax(0,1fr)_max-content] md:gap-5 md:pl-0 md:pr-3.5">
                <span className="md:pl-3.5">skill</span>
                <span className="hidden md:block">summary</span>
                <span className="hidden text-right md:block">source</span>
              </div>
              <ul className="divide-y divide-border">
                {skillGroups.map((skill) => (
                  <li
                    key={skill.name}
                    className="card-glow grid gap-3 px-3.5 py-3 hover:bg-secondary/60 md:grid-cols-[minmax(9rem,12rem)_minmax(0,1fr)_max-content] md:items-center md:gap-5 md:pl-0 md:pr-3.5"
                  >
                    <div className="flex min-w-0 items-center md:pl-3.5">
                      <div className="min-w-0">
                        <h3 className="text-sm font-medium text-foreground">
                          {skill.name}
                        </h3>
                        <p className="mt-0.5 text-label uppercase tracking-wider text-muted-foreground md:hidden">
                          {skill.path}
                        </p>
                      </div>
                    </div>
                    <div className="min-w-0 text-sm leading-6 text-muted-foreground">
                      <p>{skill.summary}</p>
                    </div>
                    <div className="min-w-0 md:justify-self-end md:pt-0.5 md:text-right">
                      <SkillLink skill={skill} />
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </section>

          <section
            id="installation"
            aria-labelledby="installation-title"
            className="scroll-mt-20 space-y-3 py-6"
          >
            <SectionIntro
              id="installation-title"
              title="INSTALL"
              description="Use the Skills CLI to add CAD Skills to supported local agents."
            />

            <div className="max-w-xl space-y-3">
              <TryNowCommand />
              <p className="text-sm leading-6 text-muted-foreground">
                Restart your agent if newly installed skills do not appear. Learn
                more at{" "}
                <a
                  className="inline-flex items-center gap-1 text-primary transition hover:text-primary/80"
                  href={skillsShUrl}
                  rel="noreferrer"
                  target="_blank"
                >
                  skills.sh
                  <ExternalLink className="size-3" aria-hidden="true" />
                </a>
                .
              </p>
            </div>
          </section>
        </div>
      </div>

      <SiteFooter />
    </main>
  );
}
