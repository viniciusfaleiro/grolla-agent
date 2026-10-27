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
    if (event.detail.successful && form.matches && form.matches("form[data-reset]")) form.reset();
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

// Alarme do incidente: aviso na tela e título piscando quando a faixa muda com a página aberta,
// e o "há X min" da faixa atualizado sem ir ao servidor.
(() => {
  const MESSAGES = {
    normalizado: ["✅ Normalizado", "Alertas normalizados: nenhum alerta deste incidente está disparando."],
    voltou: ["⚠️ Voltou a disparar", "O alarme voltou a disparar depois de normalizar."],
  };
  const banner = () => document.getElementById("alarm");
  let shown = null; // situação na carga da página: abrir a página não avisa
  let flashing = null;
  const original = document.title;

  const stopFlash = () => {
    if (flashing) clearInterval(flashing);
    flashing = null;
    document.title = original;
  };

  const flash = (text) => {
    stopFlash();
    let on = false;
    flashing = setInterval(() => {
      on = !on;
      document.title = on ? text : original;
    }, 1000);
    if (document.visibilityState === "visible" && document.hasFocus()) setTimeout(stopFlash, 10000);
  };

  const toast = (kind, text) => {
    const el = document.createElement("div");
    el.className = `toast toast-${kind}`;
    el.setAttribute("role", "status");
    el.innerHTML = "<p></p>";
    el.querySelector("p").textContent = text;
    el.addEventListener("click", () => el.remove());
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 8000);
  };

  const check = () => {
    const el = banner();
    if (!el) return;
    const kind = el.dataset.alarm || "";
    if (shown !== null && kind !== shown && MESSAGES[kind]) {
      const [title, text] = MESSAGES[kind];
      toast(kind, text);
      flash(`${title} · ${el.dataset.title}`);
    }
    shown = kind;
  };

  const minutesAgo = (iso) => {
    const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 60000));
    if (minutes === 0) return "agora";
    if (minutes < 60) return `há ${minutes} min`;
    return `há ${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
  };
  const refreshAgo = () => {
    document.querySelectorAll("[data-ago]").forEach((el) => {
      el.textContent = minutesAgo(el.dataset.ago);
    });
  };

  document.addEventListener("DOMContentLoaded", check);
  document.addEventListener("htmx:afterSettle", check);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") stopFlash();
  });
  window.addEventListener("focus", stopFlash);
  setInterval(refreshAgo, 30000);
})();

// Linha do tempo e cartão "Agora": filtro por tipo de evento, eventos de sistema recolhidos e o
// botão de copiar o comando do próximo passo. O estado do filtro fica na seção, fora do bloco que o
// polling troca, então sobrevive a cada evento novo.
(() => {
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const filter = target.closest("[data-filtro]");
    const panel = filter && filter.closest("#linha-do-tempo");
    if (filter && panel && filter.tagName === "BUTTON") {
      panel.dataset.filtro = filter.dataset.filtro;
      panel.querySelectorAll("button[data-filtro]").forEach((b) => b.setAttribute("aria-pressed", String(b === filter)));
      return;
    }
    const toggle = target.closest("[data-system-toggle]");
    if (toggle) {
      toggle.closest("#linha-do-tempo")?.classList.toggle("com-sistema");
      return;
    }
    const copy = target.closest("[data-copy]");
    if (copy && navigator.clipboard) {
      navigator.clipboard.writeText(copy.dataset.copy).then(() => {
        const label = copy.querySelector("span");
        if (!label) return;
        label.textContent = "Copiado";
        setTimeout(() => { label.textContent = "Copiar"; }, 2000);
      });
    }
  });
})();
