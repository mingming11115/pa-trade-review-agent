import { useEffect, useState } from "react";

export function useTypewriterText(text: string, active: boolean, delayMs = 20): string {
  const [displayed, setDisplayed] = useState(active ? "" : text);

  useEffect(() => {
    if (!active) {
      setDisplayed(text);
      return;
    }

    const timer = window.setInterval(() => {
      setDisplayed((current) => {
        if (!text.startsWith(current)) return "";
        const currentLength = Array.from(current).length;
        const target = Array.from(text);
        if (currentLength >= target.length) return current;
        return target.slice(0, currentLength + 1).join("");
      });
    }, delayMs);

    return () => window.clearInterval(timer);
  }, [active, delayMs, text]);

  return displayed;
}
