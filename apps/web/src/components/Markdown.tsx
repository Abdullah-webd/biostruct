"use client";

/**
 * Renders the agent's answers.
 *
 * The model writes markdown — headings, bullets, bold, and tables when it is
 * comparing costs or candidates — and rendering that raw was the single
 * ugliest thing in the first working build: `**KPC-2**` and `### What the
 * literature says` printed straight onto the page.
 *
 * `react-markdown` is used rather than a hand-rolled parser because this text
 * is model-generated, and a half-working parser fails on exactly the input you
 * did not think of. Raw HTML is **not** enabled, which is what stops a model —
 * or anything that reached the model through a tool result — injecting markup
 * into the page.
 *
 * Every element is given the project's own typography rather than relying on
 * browser defaults, so an answer reads like the rest of the instrument.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function Markdown({ children }: { children: string }) {
  return (
    <div className="flex flex-col gap-3 text-[15px] leading-relaxed text-ink">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="leading-relaxed">{children}</p>,
          h1: ({ children }) => (
            <h3 className="mt-1 font-serif text-lg text-ink">{children}</h3>
          ),
          h2: ({ children }) => (
            <h3 className="mt-1 font-serif text-lg text-ink">{children}</h3>
          ),
          h3: ({ children }) => (
            <h4 className="mt-1 font-serif text-base text-ink">{children}</h4>
          ),
          h4: ({ children }) => (
            <h4 className="mt-1 text-sm font-medium text-ink">{children}</h4>
          ),
          ul: ({ children }) => (
            <ul className="flex list-disc flex-col gap-1.5 pl-5 marker:text-faint">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="flex list-decimal flex-col gap-1.5 pl-5 marker:text-faint">
              {children}
            </ol>
          ),
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          strong: ({ children }) => (
            <strong className="font-semibold text-ink">{children}</strong>
          ),
          em: ({ children }) => <em className="italic">{children}</em>,
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="text-signal underline decoration-line underline-offset-2 transition-colors duration-[var(--dur-ui)] hover:decoration-signal"
            >
              {children}
            </a>
          ),
          code: ({ children }) => (
            // Amino-acid sequences and SMILES land here, and both are
            // unreadable in a proportional face.
            <code className="rounded-sm bg-wash px-1 py-0.5 font-mono text-[13px] break-all text-ink">
              {children}
            </code>
          ),
          pre: ({ children }) => (
            <pre className="overflow-x-auto rounded-md border border-line bg-wash p-3 font-mono text-[12px] leading-relaxed">
              {children}
            </pre>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-line pl-3 text-muted">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="border-line" />,
          // The model reaches for a table whenever it prices two steps or
          // compares candidates, so this is a normal path, not an edge case.
          table: ({ children }) => (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-[13px]">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-wash">{children}</thead>,
          th: ({ children }) => (
            <th className="border border-line px-2.5 py-1.5 text-left font-medium text-ink">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-line px-2.5 py-1.5 align-top">{children}</td>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
