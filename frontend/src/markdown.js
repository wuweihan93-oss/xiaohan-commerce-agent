import MarkdownIt from "markdown-it";
const markdown = new MarkdownIt({ html: false, breaks: true });
markdown.validateLink = (url) => {
  try {
    const parsed = new URL(url);
    return (
      ["http:", "https:"].includes(parsed.protocol) &&
      !parsed.username &&
      !parsed.password
    );
  } catch {
    return false;
  }
};
markdown.renderer.rules.image = (tokens, index) =>
  markdown.utils.escapeHtml(tokens[index].content || "图片");
const defaultOpen =
  markdown.renderer.rules.link_open ||
  ((tokens, index, options, env, renderer) =>
    renderer.renderToken(tokens, index, options));
markdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
  tokens[index].attrSet("target", "_blank");
  tokens[index].attrSet("rel", "noopener noreferrer");
  return defaultOpen(tokens, index, options, env, renderer);
};
export function renderMarkdown(text) {
  return markdown.render(typeof text === "string" ? text : "");
}
