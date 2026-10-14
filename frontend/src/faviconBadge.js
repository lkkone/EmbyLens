// A small canvas badge keeps the original avatar and needs no external library.
export function createFaviconBadge() {
  const link = document.querySelector('link[rel~="icon"]');
  if (!link) return { update() {}, dispose() {} };
  const original = link.getAttribute("href");
  const originalType = link.getAttribute("type");
  let imagePromise;
  let version = 0;
  let previous = -1;
  function restore() {
    link.setAttribute("href", original);
    if (originalType) link.setAttribute("type", originalType);
    else link.removeAttribute("type");
  }
  function image() {
    if (!imagePromise) imagePromise = new Promise((resolve, reject) => {
      const avatar = new Image();
      avatar.onload = () => resolve(avatar);
      avatar.onerror = reject;
      avatar.src = original;
    });
    return imagePromise;
  }
  async function update(count) {
    count = Math.max(0, Math.floor(Number(count) || 0));
    if (count === previous) return;
    previous = count;
    const request = ++version;
    if (!count) { restore(); return; }
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 64;
    const context = canvas.getContext("2d");
    if (!context) return;
    try { context.drawImage(await image(), 0, 0, 64, 64); } catch { /* Badge still works if the avatar cannot load. */ }
    if (request !== version) return;
    context.beginPath();
    context.arc(44, 44, 19, 0, Math.PI * 2);
    context.fillStyle = "#dc3545";
    context.fill();
    context.lineWidth = 2;
    context.strokeStyle = "#fff";
    context.stroke();
    context.fillStyle = "#fff";
    context.font = `bold ${count > 99 ? 17 : count > 9 ? 22 : 26}px Arial, sans-serif`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(count > 99 ? "99+" : String(count), 44, 45);
    link.setAttribute("type", "image/png");
    link.setAttribute("href", canvas.toDataURL("image/png"));
  }
  return { update, dispose() { ++version; restore(); } };
}
