import { marked } from "./vendor/marked.esm.js";
import DOMPurify from "./vendor/purify.es.mjs";

export function renderMarkdown(element, source) {
  // 将 Markdown 转成 HTML。
  const html = marked.parse(source, {
    async: false,
    gfm: true,
  });

  // 只保留展示答案所需的标签和属性。
  const fragment = DOMPurify.sanitize(html, {
    ALLOWED_TAGS: [
      "p", "br", "strong", "em", "del",
      "h1", "h2", "h3", "h4", "h5", "h6",
      "ul", "ol", "li", "blockquote", "pre", "code", "hr",
      "table", "thead", "tbody", "tr", "th", "td", "a",
    ],
    ALLOWED_ATTR: ["href", "title", "start"],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    RETURN_DOM_FRAGMENT: true,
  });

  // 链接仅允许 HTTP、HTTPS，以及当前页面内的锚点。
  for (const link of fragment.querySelectorAll("a[href]")) {
    const href = link.getAttribute("href");
    if (href.startsWith("#")) continue;

    try {
      const url = new URL(href);
      if (!["http:", "https:"].includes(url.protocol)) {
        link.removeAttribute("href");
      }
    } catch {
      link.removeAttribute("href");
    }
  }

  // 用清理后的内容替换旧答案，并启用答案样式。
  element.replaceChildren(fragment);
  element.classList.add("markdown-answer");
}