import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Evidence } from "./types";

interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
}
function citationLinks() {
  return (tree: MdNode) => {
    function walk(node: MdNode) {
      if (
        !node.children ||
        ["link", "linkReference", "code", "inlineCode", "html"].includes(
          node.type,
        )
      )
        return;
      node.children = node.children.flatMap((child) => {
        if (child.type !== "text" || !child.value) {
          walk(child);
          return [child];
        }
        const result: MdNode[] = [];
        const text = child.value;
        let last = 0;
        for (const match of text.matchAll(/\[(\d+)\]/g)) {
          const index = match.index!;
          if (index > last)
            result.push({ type: "text", value: text.slice(last, index) });
          result.push({
            type: "link",
            url: `#evidence-${match[1]}`,
            children: [{ type: "text", value: match[0] }],
          });
          last = index + match[0].length;
        }
        if (last < text.length)
          result.push({ type: "text", value: text.slice(last) });
        return result.length ? result : [child];
      });
    }
    walk(tree);
  };
}

export default function AnswerText({
  text,
  citations,
  onCitation,
}: {
  text: string;
  citations: Evidence[];
  onCitation: (e: Evidence) => void;
}) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, citationLinks]}
        skipHtml
        components={{
        img({ alt }) {
          return <span>{alt ? `[图片：${alt}]` : "[图片]"}</span>;
        },
          a({ href, children }) {
            const match = href?.match(/^#evidence-(\d+)$/);
            if (match) {
              const evidence = citations.find((c) => c.id === Number(match[1]));
              return evidence ? (
                <button
                  className="inline-citation"
                  title={`${evidence.filename} · 第 ${evidence.page} 页`}
                  onClick={() => onCitation(evidence)}
                >
                  {children}
                </button>
              ) : (
                <span>{children}</span>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
