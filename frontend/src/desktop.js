export const desktopBridge =
  typeof window !== "undefined" ? window.contractguardDesktop ?? null : null;

export const isDesktop = Boolean(desktopBridge?.isDesktop);
