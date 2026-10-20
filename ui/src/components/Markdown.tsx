"use client";

import { isValidElement, memo, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cx } from "./ui";

// Tables, task lists, strikethrough and bare links, as on GitHub.
const PLUGINS = [remarkGfm];

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

export const Markdown = memo(function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cx("prose-pantheon text-sm", className)}>
      <ReactMarkdown
        remarkPlugins={PLUGINS}
        components={{
          a: ({ href, children: c }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {c}
            </a>
          ),
          // A wide table scrolls on its own instead of squeezing its columns.
          table: ({ children: c }) => (
            <div className="md-table">
              <table>{c}</table>
            </div>
          ),
          // A cell holding a sentence keeps a readable width when the table has to scroll.
          td: ({ children: c, style }) => (
            <td style={style} className={textOf(c).length > 60 ? "md-long" : undefined}>
              {c}
            </td>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
});
