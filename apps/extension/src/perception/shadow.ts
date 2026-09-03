// Deep DOM traversal across OPEN shadow roots and same-origin iframes
// (Module 11). Closed shadow roots and cross-origin frames are opaque by
// design and reported as inaccessible, never forced (invariant: respect
// browser security boundaries).

/** All elements matching selector in this root and every open shadow root and
 * accessible same-origin iframe beneath it, in document order. */
export function deepQueryAll<T extends Element = Element>(
  root: Document | ShadowRoot | Element,
  selector: string,
): T[] {
  const results: T[] = [];
  const scope: ParentNode = root as ParentNode;

  for (const el of Array.from(scope.querySelectorAll<T>(selector))) {
    results.push(el);
  }
  // Descend into open shadow roots.
  for (const host of Array.from(scope.querySelectorAll<HTMLElement>("*"))) {
    const shadow = host.shadowRoot; // null for closed roots
    if (shadow) {
      results.push(...deepQueryAll<T>(shadow, selector));
    }
  }
  // Descend into accessible same-origin iframes.
  const iframes =
    "querySelectorAll" in scope
      ? Array.from((scope as ParentNode).querySelectorAll("iframe"))
      : [];
  for (const frame of iframes) {
    let doc: Document | null = null;
    try {
      doc = (frame as HTMLIFrameElement).contentDocument;
    } catch {
      doc = null; // cross-origin: inaccessible
    }
    if (doc) results.push(...deepQueryAll<T>(doc, selector));
  }
  return results;
}

/** The composed parent of a node, crossing a shadow boundary via its host. */
export function composedParent(node: Node): HTMLElement | null {
  const el = node as Element;
  if (el.parentElement) return el.parentElement;
  const root = node.getRootNode();
  if (root instanceof ShadowRoot) return root.host as HTMLElement;
  // Inside an iframe document: step to the frame element if same-origin.
  const doc = node.ownerDocument;
  try {
    const frame = doc?.defaultView?.frameElement;
    if (frame) return frame as HTMLElement;
  } catch {
    return null; // cross-origin frame boundary
  }
  return null;
}

/** Deep getElementById across open shadow roots and same-origin iframes. */
export function deepGetById(
  root: Document | ShadowRoot,
  id: string,
): HTMLElement | null {
  const direct = "getElementById" in root ? root.getElementById(id) : null;
  if (direct) return direct;
  const cssEscape = globalThis.CSS?.escape;
  const escaped = cssEscape ? cssEscape(id) : id.replace(/"/g, '\\"');
  const found = deepQueryAll<HTMLElement>(root, `[id="${escaped}"]`);
  return found[0] ?? null;
}
