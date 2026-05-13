"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

type CopyButtonProps = {
  text: string;
  label?: string;
};

export function CopyButton({ text, label = "Copy command" }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) {
      return;
    }

    const timeout = window.setTimeout(() => setCopied(false), 1400);
    return () => window.clearTimeout(timeout);
  }, [copied]);

  const copyText = async () => {
    await window.navigator.clipboard.writeText(text);
    setCopied(true);
  };

  return (
    <Button
      type="button"
      variant="outline"
      size="icon-sm"
      className="h-8 w-8 rounded-md border-[color:var(--border)] bg-[var(--background)] text-[var(--foreground)] hover:bg-[var(--muted)]"
      onClick={copyText}
      aria-label={copied ? "Copied" : label}
      title={copied ? "Copied" : label}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
    </Button>
  );
}
