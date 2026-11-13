// Applied before first paint so a saved theme never flashes the wrong colours.
try {
  const saved = localStorage.getItem("netmon-theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
} catch { /* storage unavailable: follow the operating system */ }
