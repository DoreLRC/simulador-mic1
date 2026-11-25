// Tela inicial (menu de entrada) e créditos. O simulador carrega por trás e fica inerte
// (sem foco nem cliques) enquanto a tela inicial está aberta.

export class Splash {
  constructor(root, { covered = [] } = {}) {
    this.root = root;
    this.covered = covered.filter(Boolean);
    this.views = Object.fromEntries(
      [...root.querySelectorAll("[data-view]")].map((node) => [node.dataset.view, node]),
    );
    this.opener = null;

    root.addEventListener("click", (event) => {
      const action = event.target.closest("[data-splash]")?.dataset.splash;
      if (action === "start") this.close();
      else if (action === "credits") this.show("credits");
      else if (action === "back") this.show("menu");
    });
    root.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !this.views.credits.hidden) {
        event.preventDefault();
        this.show("menu");
      }
    });
    // A página abre com a tela inicial visível (está no HTML); aqui só se ajusta o foco.
    if (!root.hidden) this.open();
  }

  get isOpen() {
    return !this.root.hidden;
  }

  open() {
    this.opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    this.root.hidden = false;
    document.body.classList.add("has-splash");
    for (const node of this.covered) node.inert = true;
    this.show("menu");
  }

  close() {
    this.root.hidden = true;
    document.body.classList.remove("has-splash");
    for (const node of this.covered) node.inert = false;
    if (this.opener && this.opener !== document.body && document.contains(this.opener)) {
      this.opener.focus();
    }
  }

  show(view) {
    for (const [name, node] of Object.entries(this.views)) node.hidden = name !== view;
    this.views[view].querySelector("button")?.focus();
  }
}
