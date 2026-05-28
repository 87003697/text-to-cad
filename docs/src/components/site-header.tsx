import { SiteHeaderClient } from "@/components/site-header-client";
import packageJson from "../../package.json";

const GITHUB_REPO_API_URL =
  "https://api.github.com/repos/earthtojake/text-to-cad";

async function getGitHubStars() {
  try {
    const response = await fetch(GITHUB_REPO_API_URL, {
      headers: {
        Accept: "application/vnd.github+json",
        "User-Agent": "cad-skills-docs",
      },
      next: { revalidate: 60 * 60 },
    });

    if (!response.ok) {
      return null;
    }

    const data = (await response.json()) as { stargazers_count?: unknown };
    const stars = Number(data.stargazers_count);

    return Number.isFinite(stars) ? stars : null;
  } catch {
    return null;
  }
}

export async function SiteHeader() {
  const githubStars = await getGitHubStars();
  return (
    <SiteHeaderClient
      githubStars={githubStars}
      version={packageJson.version}
    />
  );
}
