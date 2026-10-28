(function () {
  const initialized = new WeakSet();

  function initializeDiagram(svg) {
    if (initialized.has(svg)) return;

    const original = svg.viewBox.baseVal;
    if (!original || original.width <= 0 || original.height <= 0) return;

    initialized.add(svg);
    const host = svg.closest(".mermaid");
    host.classList.add("mermaid-panzoom");
    svg.classList.add("mermaid-panzoom__svg");
    svg.setAttribute(
      "aria-label",
      "Interactive workflow diagram. Use the mouse wheel to zoom and drag to pan."
    );

    const home = {
      x: original.x,
      y: original.y,
      width: original.width,
      height: original.height,
    };
    let drag = null;

    function currentViewBox() {
      const box = svg.viewBox.baseVal;
      return { x: box.x, y: box.y, width: box.width, height: box.height };
    }

    function applyViewBox(box) {
      svg.setAttribute(
        "viewBox",
        `${box.x} ${box.y} ${box.width} ${box.height}`
      );
    }

    svg.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();

        const bounds = svg.getBoundingClientRect();
        const box = currentViewBox();
        const pointerX = box.x + ((event.clientX - bounds.left) / bounds.width) * box.width;
        const pointerY = box.y + ((event.clientY - bounds.top) / bounds.height) * box.height;
        const requestedScale = Math.exp(event.deltaY * 0.0015);
        const minWidth = home.width / 12;
        const maxWidth = home.width * 2;
        const nextWidth = Math.min(maxWidth, Math.max(minWidth, box.width * requestedScale));
        const factor = nextWidth / box.width;

        applyViewBox({
          x: pointerX - (pointerX - box.x) * factor,
          y: pointerY - (pointerY - box.y) * factor,
          width: nextWidth,
          height: box.height * factor,
        });
      },
      { passive: false }
    );

    svg.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 && event.button !== 1) return;
      event.preventDefault();
      svg.setPointerCapture(event.pointerId);
      drag = {
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        viewBox: currentViewBox(),
      };
      svg.classList.add("is-panning");
    });

    svg.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const bounds = svg.getBoundingClientRect();
      applyViewBox({
        x: drag.viewBox.x - ((event.clientX - drag.clientX) / bounds.width) * drag.viewBox.width,
        y: drag.viewBox.y - ((event.clientY - drag.clientY) / bounds.height) * drag.viewBox.height,
        width: drag.viewBox.width,
        height: drag.viewBox.height,
      });
    });

    function stopPanning(event) {
      if (!drag || event.pointerId !== drag.pointerId) return;
      drag = null;
      svg.classList.remove("is-panning");
    }

    svg.addEventListener("pointerup", stopPanning);
    svg.addEventListener("pointercancel", stopPanning);
    svg.addEventListener("dblclick", () => applyViewBox(home));
  }

  function initializeAll() {
    document.querySelectorAll(".mermaid svg").forEach(initializeDiagram);
  }

  const observer = new MutationObserver(initializeAll);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  if (typeof document$ !== "undefined") {
    document$.subscribe(initializeAll);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeAll);
  } else {
    initializeAll();
  }
})();
