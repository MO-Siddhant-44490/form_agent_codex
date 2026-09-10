// Rendering primitives for the chat transcript: message bubbles, and prompt
// cards (a title with an optional free-text input and/or choice chips) that
// remove themselves once answered. No app logic lives here.

export type Chip = { label: string; onPick: () => void };

export type PromptOptions = {
  title: string;
  tone?: "warn" | "err";
  input?: { placeholder: string; onSubmit: (value: string) => void };
  chips?: Chip[];
};

export class ChatView {
  constructor(private readonly el: HTMLElement) {}

  clear(): void {
    this.el.innerHTML = "";
  }

  bubble(text: string, who: "agent" | "user" | "sys"): void {
    const d = document.createElement("div");
    d.className = `msg ${who}`;
    d.textContent = text;
    this.append(d);
  }

  /** A card the user resolves by typing a value or picking a chip. */
  prompt(opts: PromptOptions): void {
    const card = document.createElement("div");
    card.className = opts.tone === "err" ? "q err" : "q";
    const title = document.createElement("b");
    title.textContent = opts.title;
    card.appendChild(title);

    if (opts.input) {
      const { placeholder, onSubmit } = opts.input;
      const row = document.createElement("div");
      row.className = "row";
      const input = document.createElement("input");
      input.type = "text";
      input.placeholder = placeholder;
      const submit = () => {
        const value = input.value.trim();
        if (!value) return;
        card.remove();
        onSubmit(value);
      };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") submit();
      });
      const btn = document.createElement("button");
      btn.className = "chip";
      btn.textContent = "Answer";
      btn.addEventListener("click", submit);
      row.appendChild(input);
      row.appendChild(btn);
      card.appendChild(row);
    }

    if (opts.chips?.length) {
      const row = document.createElement("div");
      row.className = "row";
      for (const chip of opts.chips) {
        const btn = document.createElement("button");
        btn.className = "chip";
        btn.textContent = chip.label;
        btn.addEventListener("click", () => {
          card.remove();
          chip.onPick();
        });
        row.appendChild(btn);
      }
      card.appendChild(row);
    }
    this.append(card);
  }

  /** A non-interactive validation issue. */
  issue(label: string, detail: string): void {
    const div = document.createElement("div");
    div.className = "q err";
    const b = document.createElement("b");
    b.textContent = `⚠ ${label}`;
    const p = document.createElement("div");
    p.textContent = detail;
    div.appendChild(b);
    div.appendChild(p);
    this.append(div);
  }

  private append(node: HTMLElement): void {
    this.el.appendChild(node);
    this.el.scrollTop = this.el.scrollHeight;
  }
}
