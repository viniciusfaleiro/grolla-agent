// Comportamentos de chat que o HTMX não cobre: Enter envia, a caixa cresce e a conversa rola sozinha.
(() => {
  const scroller = () => document.getElementById("chat-scroll");
  const nearBottom = (el) => el.scrollHeight - el.scrollTop - el.clientHeight < 96;
  let stick = true;

  const toBottom = () => {
    const el = scroller();
    if (el && stick) el.scrollTop = el.scrollHeight;
  };

  const grow = (area) => {
    area.style.height = "auto";
    area.style.height = `${Math.min(area.scrollHeight, 192)}px`;
  };

  // Só rola se quem lê já estava no fim, ou se acabou de enviar uma mensagem.
  document.addEventListener("htmx:beforeSwap", (event) => {
    const el = scroller();
    if (el && el.contains(event.detail.target)) {
      stick = nearBottom(el) || event.detail.requestConfig.verb === "post";
    }
  });
  document.addEventListener("htmx:afterSettle", toBottom);

  document.addEventListener("htmx:afterRequest", (event) => {
    const form = event.detail.elt;
    if (event.detail.successful && form.matches && form.matches("form[data-chat-form]")) {
      form.reset();
      const area = form.querySelector("textarea");
      if (area) {
        grow(area);
        area.focus();
      }
    }
  });

  document.addEventListener("keydown", (event) => {
    const area = event.target;
    if (!area.matches || !area.matches("textarea[data-enter-sends]")) return;
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (area.value.trim()) area.form.requestSubmit();
    }
  });

  document.addEventListener("input", (event) => {
    if (event.target.matches && event.target.matches("textarea[data-enter-sends]")) grow(event.target);
  });

  document.addEventListener("DOMContentLoaded", () => {
    stick = true;
    toBottom();
  });
})();
