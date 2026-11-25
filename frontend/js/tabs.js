// Abas acessíveis (padrão ARIA "tabs"): clique ou setas ←/→, Home e End trocam a aba.

export function setupTabs(root, { onChange } = {}) {
  const tabs = [...root.querySelectorAll(":scope > .tabs__list > [role='tab']")];

  function select(tab, { focus = false } = {}) {
    for (const item of tabs) {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.getAttribute("aria-controls")).hidden = !selected;
    }
    if (focus) tab.focus();
    onChange?.(tab);
  }

  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (event) => {
      const last = tabs.length - 1;
      const target = {
        ArrowRight: index === last ? 0 : index + 1,
        ArrowLeft: index === 0 ? last : index - 1,
        Home: 0,
        End: last,
      }[event.key];
      if (target === undefined) return;
      event.preventDefault();
      select(tabs[target], { focus: true });
    });
  }
}
