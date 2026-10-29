(function () {
  document.querySelectorAll(".mermaid").forEach((container) => {
    const code = container.querySelector(":scope > code");
    if (code) {
      container.textContent = code.textContent.replace(
        /<(?!br\s*\/?\s*>)([^>\n]+)>/gi,
        "&lt;$1&gt;"
      );
    }
  });

  mermaid.initialize({
    startOnLoad: true,
    securityLevel: "strict",
  });
})();
