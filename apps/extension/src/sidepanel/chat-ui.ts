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


/** A live progress event from the backend while it fills the form. */
export type Progress = {
  phase: "reading" | "matching" | "planned" | "filling" | "retrying" | "next_page" | "checking" | string;
  page?: number;
  label?: string;
  done?: number;
  remaining?: number;
  fields?: number;
  reason?: string;
  repaired?: boolean;
  retry?: boolean;
};

/** The "working on it" card: what the agent is doing right now, which field,
 * how far along it is, and for how long — updated as events stream in. */
export class ProgressCard {
  private readonly root: HTMLElement;
  private readonly title: HTMLElement;
  private readonly detail: HTMLElement;
  private readonly bar: HTMLElement;
  private readonly fillEl: HTMLElement;
  private readonly count: HTMLElement;
  private readonly clock: HTMLElement;
  private readonly started = Date.now();
  private readonly timer: number;
  private log: HTMLElement;
  private current: { label: string; done: number } | null = null;

  constructor(parent: HTMLElement, headline = "Working on it…") {
    this.root = document.createElement("div");
    this.root.className = "progress";
    this.root.innerHTML = `
      <div class="p-head"><span class="spinner"></span><b class="p-title"></b><span class="p-clock"></span></div>
      <div class="p-bar indeterminate"><div class="p-fill"></div></div>
      <div class="p-row"><span class="p-detail"></span><span class="p-count"></span></div>
      <ol class="p-log"></ol>`;
    this.title = this.root.querySelector(".p-title")!;
    this.detail = this.root.querySelector(".p-detail")!;
    this.bar = this.root.querySelector(".p-bar")!;
    this.fillEl = this.root.querySelector(".p-fill")!;
    this.count = this.root.querySelector(".p-count")!;
    this.clock = this.root.querySelector(".p-clock")!;
    this.log = this.root.querySelector(".p-log")!;
    this.title.textContent = headline;
    parent.appendChild(this.root);
    parent.scrollTop = parent.scrollHeight;
    this.timer = window.setInterval(() => this.tick(), 1000);
  }

  private tick(): void {
    this.clock.textContent = `${Math.round((Date.now() - this.started) / 1000)}s`;
  }

  private setBar(done: number | undefined, remaining: number | undefined): void {
    if (done === undefined || remaining === undefined || done + remaining === 0) {
      this.bar.classList.add("indeterminate");
      this.count.textContent = done ? `${done} filled` : "";
      return;
    }
    const total = done + remaining;
    this.bar.classList.remove("indeterminate");
    this.fillEl.style.width = `${Math.round((done / total) * 100)}%`;
    this.count.textContent = `${done} of ${total}`;
  }

  private note(text: string, cls = ""): void {
    const li = document.createElement("li");
    li.textContent = text;
    if (cls) li.className = cls;
    this.log.appendChild(li);
    while (this.log.children.length > 4) this.log.firstElementChild?.remove();
  }

  /** The field announced last is confirmed filled once the count moves on. */
  private settlePrevious(done: number | undefined): void {
    if (this.current && done !== undefined && done > this.current.done) {
      this.note(`✓ ${this.current.label}`, "ok");
    }
    this.current = null;
  }

  /** One line for the panel's status bar. */
  statusLine(ev: Progress): string | null {
    const total = ev.done !== undefined && ev.remaining !== undefined ? ev.done + ev.remaining : null;
    switch (ev.phase) {
      case "reading":
        return "Reading the page…";
      case "matching":
        return "Matching your profile to the form…";
      case "filling":
        return total ? `Filling… ${ev.done} of ${total} fields` : "Filling…";
      case "next_page":
        return "Going to the next page…";
      case "checking":
        return "Double-checking the form…";
      default:
        return null;
    }
  }

  update(ev: Progress): void {
    const page = ev.page && ev.page > 1 ? ` (page ${ev.page})` : "";
    switch (ev.phase) {
      case "reading":
        this.title.textContent = `Reading the page${page}…`;
        this.detail.textContent = ev.fields ? `${ev.fields} fields found` : "";
        this.setBar(undefined, undefined);
        break;
      case "matching":
        this.title.textContent = `Matching your profile${page}…`;
        this.detail.textContent = ev.fields ? `to ${ev.fields} fields` : "";
        this.setBar(undefined, undefined);
        break;
      case "planned":
        this.title.textContent = `Filling the form${page}`;
        this.detail.textContent = ev.remaining ? `${ev.remaining} fields to fill` : "Nothing to fill here";
        this.setBar(ev.done, ev.remaining);
        break;
      case "filling":
        this.settlePrevious(ev.done);
        this.title.textContent = ev.retry ? "Trying again" : `Filling the form${page}`;
        this.detail.textContent = `“${ev.label ?? "field"}”`;
        this.setBar(ev.done, ev.remaining);
        this.current = { label: ev.label ?? "field", done: ev.done ?? 0 };
        break;
      case "retrying":
        this.detail.textContent = `“${ev.label}” rejected — ${ev.repaired ? "asking the AI to fix it" : "trying another format"}`;
        this.note(`↻ ${ev.label}: ${ev.reason ?? "rejected"}`, "retry");
        break;
      case "next_page":
        this.settlePrevious(ev.done);
        this.title.textContent = "Going to the next page…";
        this.detail.textContent = "";
        this.setBar(undefined, undefined);
        this.note(`→ page ${(ev.page ?? 1) + 1}`);
        break;
      case "checking":
        this.settlePrevious(ev.done);
        this.title.textContent = "Double-checking the form…";
        this.detail.textContent = ev.done ? `${ev.done} fields filled` : "";
        this.fillEl.style.width = "100%";
        this.bar.classList.remove("indeterminate");
        break;
    }
  }

  finish(): void {
    window.clearInterval(this.timer);
    this.root.remove();
  }
}
