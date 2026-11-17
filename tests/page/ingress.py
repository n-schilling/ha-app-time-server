"""A stand-in for Home Assistant around the page, for the Page check
(scripts/screenshots.py). It runs in the built image at the ingress proxy's
address 172.30.32.2, the only peer the page lets in, and serves on port 8080:

    /                               a page that holds the app in a frame, as Home Assistant does
    /?lang=de                       the same with a stand-in for the frontend's <home-assistant>
                                    element, whose hass names that language (and light mode),
                                    as the page reads it (share/ha-app/ha-lang.js)
    /?safe=47,34                    the same on a phone with a notch: the safe-area insets
                                    top and bottom in px that Home Assistant passes on
    /api/hassio_ingress/<token>/... the app's page, passed on to APP with the
                                    path prefix taken off and X-Ingress-Path set, like the Supervisor
    /config/app/<slug>/config       where the settings button leads (outside the frame)

The outer page plays Home Assistant's part (its app panel, ha-panel-app) for
the page's share/ha-app/ha-frame.js: it answers subscribe-properties with
properties (narrow below 870 px, the insets, the route) and again on every
resize; it shows its 40 px bar #ha-bar above the frame while narrow and not
in kiosk mode, and pads the frame by the insets unless the page handles them
(handleSafeArea). What the page asked for lands in window.__kiosk,
window.__safe and window.__menu (toggle-menu clicks), every message in
window.__messages.

    python3 /tests/page/ingress.py http://<app>:8099
"""

from __future__ import annotations

import html
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

PREFIX = "/api/hassio_ingress/demo-token"
APP = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8099"
PASS_ON = ("Accept-Language", "Cookie", "Content-Type", "Content-Length")
OUTER = f"""<!doctype html><html><head><meta charset="utf-8"><title>Home Assistant</title>
<style>html, body {{ margin: 0; height: 100%; }}
home-assistant {{ display: flex; flex-direction: column; height: 100%; }}
#ha-bar {{ flex: none; height: 40px; background: #888; }}
#ha-app {{ flex: 1; min-height: 0; box-sizing: border-box; }}
iframe {{ margin: 0; border: 0; width: 100%; height: 100%; display: block; }}</style>
<script>{{FRAME}}</script></head>
<body><home-assistant>HASS<div id="ha-bar" hidden></div><div id="ha-app">
<iframe id="ingress" title="app" src="{PREFIX}/"></iframe></div></home-assistant></body></html>"""
# Home Assistant's app panel as far as ha-frame.js talks to it; in the head, so
# it listens before the frame loads
FRAME = """(() => {
  const safe = (new URLSearchParams(location.search).get("safe") || "0,0").split(",");
  const inset = (n) => (parseInt(n, 10) || 0) + "px";
  const insets = { top: inset(safe[0]), right: "0px", bottom: inset(safe[1]), left: "0px" };
  const frame = { get contentWindow() { return document.getElementById("ingress").contentWindow; } };
  Object.assign(window, { __kiosk: null, __safe: null, __menu: 0, __messages: [] });
  let subscribed = false;
  const narrow = () => matchMedia("(max-width: 870px)").matches;
  const layout = () => {
    const bar = document.getElementById("ha-bar"), box = document.getElementById("ha-app");
    if (!bar || !box) return;
    bar.hidden = !(narrow() && !window.__kiosk);
    box.style.paddingTop = window.__safe || !bar.hidden ? "0px" : insets.top;
    box.style.paddingBottom = window.__safe ? "0px" : insets.bottom;
  };
  const send = () => {
    if (subscribed) frame.contentWindow.postMessage({ type: "home-assistant/properties", narrow: narrow(),
      route: { prefix: "/app/demo", path: "" }, safeAreaInsets: insets }, location.origin);
  };
  window.addEventListener("message", (e) => {
    if (e.source !== frame.contentWindow || !e.data) return;
    window.__messages.push(e.data);
    if (e.data.type === "home-assistant/subscribe-properties") {
      subscribed = true;
      window.__kiosk = !!e.data.kioskMode;
      window.__safe = !!e.data.handleSafeArea;
      layout();
      send();
    } else if (e.data.type === "home-assistant/unsubscribe-properties") {
      subscribed = false;
      window.__kiosk = false;
      window.__safe = false;
      layout();
    } else if (e.data.type === "home-assistant/toggle-menu") {
      window.__menu += 1;
    }
  });
  window.addEventListener("resize", () => { layout(); send(); });
  document.addEventListener("DOMContentLoaded", layout);
})();"""
# Home Assistant's frontend as far as the page reads it: the user's language
HASS = """<script>document.querySelector("home-assistant").hass =
{ language: "LANG", locale: { language: "LANG" }, themes: { darkMode: false } };</script>"""
LANG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?")


def outer(query: str) -> str:
    """The page around the frame; with ?lang=xx as Home Assistant in that language."""
    lang = (parse_qs(query).get("lang") or [""])[0]
    page = OUTER.replace("{FRAME}", FRAME)
    return page.replace("HASS", HASS.replace("LANG", lang) if LANG.fullmatch(lang) else "")


class Handler(BaseHTTPRequestHandler):
    def _forward(self) -> None:
        path = self.path[len(PREFIX):] or "/"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k in PASS_ON if (v := self.headers.get(k))}
        headers["X-Ingress-Path"] = PREFIX
        request = urllib.request.Request(APP + path, data=body, method=self.command, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as answer:
                self._send(answer.status, answer.headers, answer.read())
        except urllib.error.HTTPError as err:
            self._send(err.code, err.headers, err.read())

    def _send(self, code: int, headers, body: bytes) -> None:
        self.send_response(code)
        for key, value in headers.items():
            if key.lower() not in ("connection", "transfer-encoding", "content-length", "server", "date"):
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _page(self, text: str) -> None:
        self._send(200, {"Content-Type": "text/html; charset=utf-8"}, text.encode())

    def do_GET(self) -> None:  # noqa: N802 (the standard library's name)
        if self.path.startswith(PREFIX + "/"):
            self._forward()
        elif self.path.partition("?")[0] == "/":
            self._page(outer(self.path.partition("?")[2]))
        elif self.path == "/favicon.ico":
            # Home Assistant has one; the browser asks for it also for a page in the frame
            self._send(200, {"Content-Type": "image/x-icon"}, b"")
        elif self.path.startswith("/config/app/"):
            self._page(f"<!doctype html><title>Settings</title><h1>{html.escape(self.path)}</h1>")
        else:
            self._send(404, {"Content-Type": "text/plain"}, b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith(PREFIX + "/"):
            self._forward()
        else:
            self._send(404, {"Content-Type": "text/plain"}, b"not found")

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
