/**
 * Lightweight markdown-like formatting for chat messages and AI text.
 * Renders [@citekey] as clickable links to the library source page.
 *
 * @param text - raw text with **bold**, _italic_, [@citekey] markers
 * @param projectId - project ID for source links (defaults to 'demo')
 */
export function formatMarkdown(text: string, projectId: string = 'demo'): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // [@citekey] — clickable link to source page (bold variant too)
    .replace(
      /\*?\*?\[(@([\w]+))\]\*?\*?/g,
      `<a href="/${projectId}/library/$2" class="citekey-link">$1</a>`,
    )
    // **bold**
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // _italic_
    .replace(/_(.+?)_/g, '<em>$1</em>')
}
