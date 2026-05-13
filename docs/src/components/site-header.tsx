"use client";

import Image from "next/image";
import Link from "next/link";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

function GitHubLogo({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="currentColor"
      focusable="false"
      viewBox="0 0 24 24"
    >
      <path d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2c-3.3.7-4-1.6-4-1.6-.5-1.3-1.2-1.6-1.2-1.6-1-.7.1-.7.1-.7 1.1.1 1.7 1.2 1.7 1.2 1 1.7 2.6 1.2 3.3.9.1-.7.4-1.2.7-1.5-2.7-.3-5.5-1.3-5.5-5.9 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.6.1-3.2 0 0 1-.3 3.3 1.2a11.2 11.2 0 0 1 6 0C17 4.7 18 5 18 5c.7 1.6.3 2.9.1 3.2.8.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6A12 12 0 0 0 12 .3Z" />
    </svg>
  );
}

export function SiteHeader() {
  return (
    <header className="border-b border-[color:var(--border)] pb-7">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <Link href="/" className="min-w-0 focus:outline-none">
          <div className="flex min-w-0 items-center gap-1">
            <span className="relative size-12 shrink-0 overflow-hidden sm:size-16">
              <Image
                src="/skill-logos/brain-cog-orbit.gif"
                width={160}
                height={160}
                unoptimized
                alt=""
                aria-hidden="true"
                className="size-full object-cover"
              />
            </span>
            <h1 className="min-w-0 text-3xl font-semibold tracking-normal transition hover:opacity-80 sm:text-5xl">
              CAD Skills
            </h1>
          </div>
          <p className="mt-3 max-w-2xl text-base leading-7 text-[var(--muted-foreground)] sm:text-lg">
            A collection of agent skills for CAD, robotics and hardware design
          </p>
        </Link>
        <div className="flex flex-wrap items-center gap-2 sm:shrink-0 sm:justify-end">
          <nav
            aria-label="Primary"
            className="flex flex-wrap items-center gap-2 text-sm font-semibold text-[var(--foreground)]"
          >
            <a
              className="rounded-md border border-[color:var(--border)] px-3 py-2 transition hover:bg-[var(--muted)]"
              href="#skills"
            >
              Skills
            </a>
            <a
              className="rounded-md border border-[color:var(--border)] px-3 py-2 transition hover:bg-[var(--muted)]"
              href="#installation"
            >
              Install
            </a>
            <a
              className="rounded-md border border-[color:var(--border)] px-3 py-2 transition hover:bg-[var(--muted)]"
              href="https://demo.cadskills.xyz"
              target="_blank"
              rel="noreferrer"
            >
              Demo
            </a>
          </nav>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                asChild
                variant="outline"
                size="icon"
                className="h-10 w-10 rounded-md border-[color:var(--border)] bg-[var(--background)] text-[var(--foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
              >
                <a
                  href="https://github.com/earthtojake/text-to-cad"
                  target="_blank"
                  rel="noreferrer"
                  aria-label="Open text-to-cad on GitHub"
                >
                  <GitHubLogo className="size-4" />
                </a>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">GitHub</TooltipContent>
          </Tooltip>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
