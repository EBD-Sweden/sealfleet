export function detectPostType(
  text: string,
  replyText?: string
): "thread" | "text_link" | "text" {
  const trimmed = text.trim();
  if (/\n1\//.test(text) || trimmed.startsWith("1/")) {
    return "thread";
  }
  if (replyText && /https?:\/\//.test(replyText)) {
    return "text_link";
  }
  return "text";
}

export function splitThread(text: string): string[] {
  return text
    .split(/\n(?=\d+\/)/)
    .map((c) => c.trim())
    .filter(Boolean);
}

export function estimateTweetCount(
  text: string,
  postType?: "thread" | "text_link" | "text"
): number {
  const type = postType ?? detectPostType(text);
  if (type === "thread") {
    return splitThread(text).length;
  }
  return 1;
}
