"""The Page check, and the screenshots in docs/screenshots.

Starts the built image twice in a throwaway Docker network: once as the app,
its page on made-up data (tests/page/demo.py, host name a1b2c3d4-time-server,
so the slug is a1b2c3d4_time_server), once as a stand-in for Home Assistant at
the ingress proxy's address 172.30.32.2 (tests/page/ingress.py), which passes
the page on under /api/hassio_ingress/<token>/ and holds it in a frame. A
headless browser then opens the page as Home Assistant's ingress would and
checks, in light, dark and at phone width, in English and German:

- every view (Overview, Upstream, Clients) by its URL ?tab=<view> (no tab: the
  overview), with the tabs centred in the header's row, icon beside label and
  a line under the active one, and no bottom bar (narrow, below 870 px: a bar
  at the bottom, icon over label) and the right one active; tab, reload and
  back in Home Assistant's frame;
- the header (STANDARD.md, section 7), as Home Assistant's own pages draw it
  (hass-tabs-subpage): the menu button first (shown only when narrow), the
  h1 with the name exactly as `name` in config.yaml but, with tabs, hidden
  like the icon; then the stamp "As of HH:MM:SS" (hidden when narrow), the
  refresh button and the settings button, which leads to
  /config/app/a1b2c3d4_time_server/config outside the frame (target _top);
  menu and buttons inside the header;
- Home Assistant's frame (the stand-in plays its part, share/ha-app/ha-frame.js
  the page's): no kiosk mode on a wide screen; on a phone (390 x 844, with
  a notch of 47 px and a home bar of 34 px, and without) kiosk mode, so no
  bar of Home Assistant above the app, the insets handled by the page: the
  header from the top, 56 px plus the notch, the bar at the bottom of the
  window with the home bar's padding, every tab on the screen; the menu
  button opens Home Assistant's sidebar; wider and back switches kiosk mode
  off and on with one message each way;
- the version line "Time Server <version>" as the last element of body;
- no console errors, no failed scripts and no Content Security Policy violations;
- refresh (in place, and the stamp moves), the client list's filter and the
  edit dialog work, and nothing is wider than a phone;
- the page takes Home Assistant's language over the browser's (a stand-in for
  its frontend in German, English and French around the frame): reloading once
  when it changes, never again, with the cookie for the ingress path only.

Then it takes the screenshots, unless --check is given.

    scripts/screenshots.py [--check] [--image time-server:check] [--build]
    uv run --with playwright==1.58.0 python scripts/screenshots.py --build

--build builds the image first (with the version from config.yaml). Uses the
installed Google Chrome, else Playwright's Chromium (`playwright install
chromium` once). Every name, address and number on the pictures is made up;
the addresses are from the ranges reserved for documentation.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Error, Frame, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/screenshots"
CONFIG = (ROOT / "time_server/config.yaml").read_text()
VERSION = re.search(r'^version: "?([^"\n]+)"?$', CONFIG, re.M)[1]
# The name the app store shows; the header must carry exactly this
NAME = re.search(r'^name: "?([^"\n]+?)"?$', CONFIG, re.M)[1]
HOST = "a1b2c3d4-time-server"
SLUG = "a1b2c3d4_time_server"
INGRESS = "/api/hassio_ingress/demo-token/"
NET, APP, PROXY = "chrony-page-check", "chrony-page-check-app", "chrony-page-check-ingress"
TEXTS = {"en-US": (NAME, "As of", "Refresh", "Settings", "Menu"),
         "de-DE": (NAME, "Stand", "Aktualisieren", "Einstellungen", "Menü")}
# The views (tabs in the header), where each one is, and their labels
TABS = (("overview", "?tab=overview"), ("upstream", "?tab=upstream"), ("clients", "?tab=clients"))
TAB_LABELS = {"en-US": ["Overview", "Upstream", "Clients"], "de-DE": ["Übersicht", "Upstream", "Clients"]}
# Collects Content Security Policy violations, which reach no console
WATCH_CSP = """window.__csp = [];
document.addEventListener("securitypolicyviolation", (e) => window.__csp.push(e.violatedDirective + " " + e.blockedURI));"""

problems: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("    ok: " if ok else "    FAILED: ") + what)
    if not ok:
        problems.append(what)


def security_headers(headers: dict[str, str], where: str) -> None:
    """The headers STANDARD.md §3 asks of every answer of the page."""
    policy = {part.split()[0]: part.split()[1:]
              for part in headers.get("content-security-policy", "").split(";") if part.strip()}
    scripts = policy.get("script-src", [])
    check(policy.get("default-src") == ["'none'"]
          and "'unsafe-inline'" not in scripts and any(s.startswith(("'sha256-", "'nonce-")) for s in scripts)
          and policy.get("frame-ancestors") == ["'self'"] and policy.get("base-uri") == ["'none'"]
          and policy.get("form-action") in (["'none'"], ["'self'"])
          and headers.get("x-content-type-options") == "nosniff"
          and headers.get("referrer-policy") in ("no-referrer", "same-origin")
          and headers.get("cache-control") == "no-store",
          f"{where}: security headers (CSP {headers.get('content-security-policy')!r}, "
          f"{headers.get('x-content-type-options')}, {headers.get('referrer-policy')}, {headers.get('cache-control')})")


def docker(*args: str, check_: bool = True) -> str:
    return subprocess.run(["docker", *args], check=check_, capture_output=True, text=True).stdout.strip()


def cleanup() -> None:
    docker("rm", "-f", APP, PROXY, check_=False)
    docker("network", "rm", NET, check_=False)


def start(image: str) -> str:
    """The app and the ingress stand-in; the address of the stand-in on this machine."""
    cleanup()
    # Another app's Page check on this machine may hold the subnet for a while
    for _ in range(100):
        made = subprocess.run(["docker", "network", "create", "--subnet", "172.30.32.0/23", NET],
                              capture_output=True, text=True)
        if made.returncode == 0:
            break
        if "overlaps" not in made.stderr:
            raise SystemExit(made.stderr)
        print("    waiting for 172.30.32.0/23, in use by another network")
        time.sleep(3)
    else:
        raise SystemExit("172.30.32.0/23 stays in use by another Docker network")
    tests = f"{ROOT / 'tests'}:/tests:ro"
    docker("run", "-d", "--name", APP, "--network", NET, "--ip", "172.30.32.10", "--hostname", HOST,
           "-e", "TZ=Europe/Berlin", "-v", tests, "--entrypoint", "python3", image, "/tests/page/demo.py")
    docker("run", "-d", "--name", PROXY, "--network", NET, "--ip", "172.30.32.2", "-p", "127.0.0.1::8080",
           "-v", tests, "--entrypoint", "python3", image, "/tests/page/ingress.py", "http://172.30.32.10:8099")
    port = docker("port", PROXY, "8080/tcp").splitlines()[0].rsplit(":", 1)[1]
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            with urllib.request.urlopen(base + INGRESS + "?tab=clients", timeout=2) as answer:
                # Ready once the first read of chronyc and the name lookups are in (the clients' view)
                if answer.status == 200 and b"purifier-bedroom.lan" in answer.read():
                    return base
        except OSError:
            pass
        time.sleep(1)
    print(docker("logs", APP, check_=False))
    raise SystemExit("The page did not come up")


def header(p: Page | Frame, lang: str, phone: bool, tab: str = "overview", safe: tuple[int, int] = (0, 0)) -> None:
    """The header as Home Assistant's own pages draw it (hass-tabs-subpage, STANDARD.md §7):
    phone is Home Assistant's narrow, safe the insets top and bottom the page keeps clear of."""
    title, as_of, refresh, settings, menu = TEXTS[lang]
    shape = p.evaluate("""() => {
      const h = document.querySelector("body > header");
      const a = h && h.querySelector(":scope > .actions");
      if (!h) return null;
      const box = h.getBoundingClientRect(), shown = (e) => !!e && getComputedStyle(e).display !== "none";
      const m = h.querySelector(":scope > .menu"), h1 = h.querySelector(":scope > h1");
      const nav = h.querySelector(":scope > nav.tabs");
      const inside = (e) => { const r = e.getBoundingClientRect();
        return r.left >= box.left - 0.5 && r.right <= box.right + 0.5 && r.top >= box.top - 0.5 && r.bottom <= box.bottom + 0.5; };
      return {
        tags: [...h.children].map((k) => k.tagName.toLowerCase() + (k.className ? "." + k.className.split(" ").join(".") : "")),
        top: box.top, height: box.height, narrow: document.documentElement.classList.contains("narrow"),
        h1: h1?.textContent, h1Hidden: !!h1 && h1.getBoundingClientRect().width <= 1 && h1.getBoundingClientRect().height <= 1,
        brand: [...h.querySelectorAll("img")].some(shown),
        menu: m && {first: h.firstElementChild === m, ha: m.hasAttribute("data-ha-menu"), role: m.getAttribute("role"),
                    title: m.title, label: m.getAttribute("aria-label"), icon: !!m.querySelector("svg path"), shown: shown(m)},
        // The menu and the buttons inside the header's box (below the notch)
        inside: [m, ...h.querySelectorAll(":scope > .actions > a")].filter(shown).every(inside),
        tabs: nav && (() => {
          const tabs = [...nav.querySelectorAll(":scope > a.tab")], first = tabs[0].getBoundingClientRect();
          const last = tabs[tabs.length - 1].getBoundingClientRect(), r = nav.getBoundingClientRect();
          const actions = a.getBoundingClientRect();
          return {labels: tabs.map((t) => t.textContent), hrefs: tabs.map((t) => t.getAttribute("href")),
                  active: [...nav.querySelectorAll("a.tab.active[aria-current=page]")].map((t) => t.getAttribute("href")),
                  icons: tabs.every((t) => shown(t.querySelector("svg"))),
                  fixed: getComputedStyle(nav).position === "fixed",
                  // Wide: in the header's row, centred between its start and the actions
                  row: tabs.every(inside), centred: Math.abs((first.left + last.right) / 2 - (r.left + r.right) / 2) < 1
                       && r.right <= actions.left + 0.5,
                  // Narrow: the bar at the bottom of the window, clear of the home bar, every tab on the screen
                  bottom: r.bottom - innerHeight, pad: getComputedStyle(nav).paddingBottom,
                  onScreen: tabs.every((t) => { const q = t.getBoundingClientRect(); return q.left >= 0 && q.right <= innerWidth && q.bottom <= innerHeight - parseFloat(getComputedStyle(nav).paddingBottom) + 0.5; }),
                  line: tabs.map((t) => getComputedStyle(t, "::after").content !== "none" && getComputedStyle(t, "::after").content !== "")};
        })(),
        kids: a ? [...a.children].map((e) => ({ tag: e.tagName, id: e.id, cls: e.className, role: e.getAttribute("role"),
          href: e.getAttribute("href"), target: e.getAttribute("target"), title: e.title, text: e.textContent,
          shown: e.offsetParent !== null, icon: !!e.querySelector("svg path") })) : [],
        last: document.body.lastElementChild.outerHTML,
      };
    }""")
    check(shape is not None, f"{lang}: header")
    if shape is None:
        return
    kids = shape["kids"]
    where = f"{lang}, {tab}, {'narrow' if phone else 'wide'}"
    check(shape["narrow"] == phone, f"{where}: html.narrow {phone}")
    check(shape["tags"] == ["a.icon-button.menu", "h1.name", "nav.tabs", "div.actions"] and shape["h1"] == title,
          f"{where}: header holds the menu button, h1 {title!r}, the tabs and the actions ({shape['tags']})")
    check(shape["h1Hidden"] and not shape["brand"], f"{where}: with tabs no icon and no visible name")
    check(abs(shape["top"]) < 0.5 and abs(shape["height"] - 56 - safe[0]) < 0.5,
          f"{where}: header from the top, 56 + {safe[0]} px high ({shape['top']}, {shape['height']})")
    m = shape["menu"] or {}
    check(m.get("first") and m.get("ha") and m.get("role") == "button" and m.get("title") == menu == m.get("label")
          and m.get("icon") and m.get("shown") == phone,
          f"{where}: menu button first, {'shown' if phone else 'hidden'} ({m})")
    tabs = shape["tabs"] or {}
    check(tabs.get("labels") == TAB_LABELS[lang] and tabs.get("hrefs") == [href for _, href in TABS],
          f"{where}: the tabs ({tabs.get('labels')}, {tabs.get('hrefs')})")
    check(tabs.get("active") == [dict(TABS)[tab]], f"{where}: {tab} is the active tab ({tabs.get('active')})")
    check(bool(tabs.get("icons")), f"{where}: every tab with its icon")
    if phone:
        check(tabs.get("fixed") and abs(tabs.get("bottom", 1)) < 0.5 and tabs.get("pad") == f"{safe[1]}px"
              and tabs.get("onScreen") and not any(tabs.get("line", [])),
              f"{where}: tabs in a bar at the bottom, {safe[1]} px clear of the home bar, all on the screen ({tabs})")
    else:
        check(not tabs.get("fixed") and tabs.get("row") and tabs.get("centred")
              and tabs.get("line") == [href == dict(TABS)[tab] for _, href in TABS],
              f"{where}: tabs centred in the header's row, a line under the active one, no bottom bar ({tabs})")
    check(shape["inside"], f"{where}: menu and buttons inside the header")
    check(len(kids) == 3, f"{lang}: three actions in the header ({len(kids)})")
    if len(kids) == 3:
        stamp, again, gear = kids
        check(stamp["tag"] == "SPAN" and "stamp" in stamp["cls"].split() and "opt" in stamp["cls"].split()
              and re.fullmatch(rf"{as_of} \d\d:\d\d:\d\d", stamp["text"]) is not None,
              f"{lang}: stamp {stamp['text']!r}")
        check(stamp["shown"] != phone, f"{where}: stamp {'hidden' if phone else 'shown'}")
        check(again["tag"] == "A" and again["id"] == "refresh" and again["href"] == "" and again["role"] == "button"
              and again["title"] == refresh and again["icon"], f"{lang}: refresh button")
        check(gear["tag"] == "A" and gear["href"] == f"/config/app/{SLUG}/config" and gear["target"] == "_top"
              and gear["title"] == settings and gear["icon"], f"{lang}: settings button ({gear['href']})")
    check(shape["last"] == f'<footer class="version">{NAME} {VERSION}</footer>', f"{lang}: version line last")


def in_frame(p: Page, wide: bool, safe: tuple[int, int]) -> Frame:
    """The app's frame in the stand-in for Home Assistant, once the page has subscribed."""
    frame = p.frame_locator("#ingress")
    frame.locator("body > header h1").wait_for(state="attached")
    p.wait_for_function("window.__safe !== null")
    p.wait_for_timeout(500)  # room for the answer, and for any further message, which must not loop
    app = next(f for f in p.frames if f.parent_frame is not None)
    narrow = "html:not(.narrow)" if wide else "html.narrow"
    app.wait_for_selector(narrow, state="attached")
    if safe != (0, 0):
        app.wait_for_function(f"getComputedStyle(document.documentElement).getPropertyValue('--safe-area-inset-top').trim() === '{safe[0]}px'")
    return app


def frame_checks(p: Page, app: Frame, lang: str, wide: bool, safe: tuple[int, int], where: str) -> None:
    """What the page asked of Home Assistant (the stand-in records it) and where its frame sits."""
    seen = p.evaluate("""() => ({kiosk: window.__kiosk, safe: window.__safe, menu: window.__menu,
      messages: window.__messages.map((m) => m.type + (m.type.endsWith("/subscribe-properties") ? ":" + m.kioskMode : "")),
      bar: getComputedStyle(document.getElementById("ha-bar")).display !== "none",
      frame: (() => { const r = document.getElementById("ingress").getBoundingClientRect(); return [r.top, r.bottom - innerHeight]; })()})""")
    check(seen["kiosk"] is (not wide), f"{where}: kiosk mode {'not ' if wide else ''}requested ({seen['messages']})")
    check(seen["safe"] is True, f"{where}: the page keeps clear of the notch itself (handleSafeArea)")
    check(not seen["bar"], f"{where}: no bar of Home Assistant above the app")
    check(seen["frame"] == [0, 0], f"{where}: the frame fills the window ({seen['frame']})")
    check(len(seen["messages"]) <= 2, f"{where}: no loop of messages ({seen['messages']})")
    header(app, lang, not wide, "overview", safe)
    check(app.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"{where}: no sideways scrolling")


def run(image: str, shots: bool) -> None:
    base = start(image)
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome")
        except Error:
            browser = pw.chromium.launch()

        def page(width: int, height: int, scheme: str, lang: str, url: str = base + INGRESS) -> Page:
            context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme,
                                          device_scale_factor=2, locale=lang,
                                          extra_http_headers={"Accept-Language": lang})
            context.add_init_script(WATCH_CSP)
            p = context.new_page()
            p.errors = []
            p.on("console", lambda m: m.type == "error" and p.errors.append(f"console: {m.text} ({m.location.get('url')})"))
            p.on("pageerror", lambda e: p.errors.append(f"script: {e}"))
            answer = p.goto(url)
            p.wait_for_load_state("networkidle")
            if answer is not None and "/api/hassio_ingress/" in url:
                security_headers(answer.headers, url)
            return p

        def done(p: Page, what: str) -> None:
            for frame in p.frames:
                p.errors += [f"CSP: {v}" for v in frame.evaluate("window.__csp || []")]
            check(not p.errors, f"{what}: no console errors, script errors or CSP violations")
            problems.extend(p.errors)
            p.context.close()

        backgrounds = {}
        for width, height, scheme, lang in ((1200, 760, "light", "en-US"), (1200, 760, "dark", "de-DE"),
                                            (390, 844, "dark", "de-DE"), (390, 844, "light", "en-US")):
            what = f"{width} px, {scheme}, {lang}"
            print(f"==> {what}")
            p = page(width, height, scheme, lang)
            if shots and (width, scheme, lang) == (390, "dark", "de-DE"):
                # Before anything scrolls the page or the client list
                p.screenshot(path=OUT / "mobile-dark.png", full_page=True)
            header(p, lang, width <= 870)
            backgrounds[scheme] = p.evaluate("getComputedStyle(document.body).backgroundColor")
            check(p.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"{lang}: nothing wider than {width} px")
            if shots and (width, scheme) == (1200, "light"):
                p.screenshot(path=OUT / "overview.png")
            # Every view, by its URL (the first also with an empty query); the clients last, for the list's checks
            for tab, href in (("overview", "?"), *TABS):
                p.goto(base + INGRESS + href)
                header(p, lang, width <= 870, tab)
                check(p.evaluate("document.documentElement.scrollWidth <= innerWidth"),
                      f"{lang}, {tab}: nothing wider than {width} px")
                if shots and (width, scheme) == (1200, "light") and tab == "upstream":
                    p.screenshot(path=OUT / "upstream.png")
            # The page swaps in a fresh copy in place; with the request in flight the button spins.
            # The stamp shows seconds: a second later the fresh read moves it
            before = p.locator("header .stamp").text_content()
            p.wait_for_timeout(1100)
            with p.expect_response(lambda r: "refresh=1" in r.url):
                p.locator("#refresh").click()
            p.wait_for_function("!document.querySelector('#refresh.busy')")
            after = p.locator("header .stamp").text_content() if p.locator("header .stamp").count() == 1 else None
            check(after is not None and p.url == base + INGRESS + "?tab=clients", f"{lang}: refresh stays on the view")
            check(after is not None and after != before, f"{lang}: refresh moves the stamp ({before} -> {after})")
            if shots and (width, scheme) == (1200, "light"):
                p.locator(".chip[data-show=active]").click()
                p.locator(".card", has=p.locator("#clients")).screenshot(path=OUT / "clients.png")
                p.locator(".chip[data-show=all]").click()
            p.locator("tr[data-addr='192.0.2.31'] button.edit").click()
            check(p.locator("dialog#edit[open]").count() == 1, f"{lang}: edit dialog opens")
            if shots and (width, scheme) == (1200, "light"):
                p.wait_for_timeout(300)
                p.screenshot(path=OUT / "edit.png")
            p.locator("#edit-cancel").click()
            done(p, what)
        check(backgrounds["light"] != backgrounds["dark"], f"light and dark differ ({backgrounds})")

        # Home Assistant's part of the frame (tests/page/ingress.py plays it): kiosk mode
        # only while narrow, the notch and the home bar by Home Assistant's insets, the menu
        for width, height, safe, lang in ((1200, 760, (0, 0), "en-US"), (390, 844, (47, 34), "de-DE"),
                                          (390, 844, (0, 0), "en-US")):
            wide = width > 870
            where = f"frame, {width} px" + (f", notch {safe[0]} px, home bar {safe[1]} px" if safe != (0, 0) else "")
            print(f"==> {where}")
            query = "?safe=" + ",".join(map(str, safe)) if safe != (0, 0) else ""
            p = page(width, height, "dark", lang, base + "/" + query)
            app = in_frame(p, wide, safe)
            frame_checks(p, app, lang, wide, safe, where)
            if not wide:
                app.locator("header .menu").click()
                check(p.evaluate("window.__menu") == 1, f"{where}: the menu button opens Home Assistant's sidebar")
                if safe != (0, 0):
                    # Wider than narrow and back: kiosk mode off (Home Assistant's bar back), then on again
                    p.set_viewport_size({"width": 1200, "height": height})
                    app.wait_for_selector("html:not(.narrow)", state="attached")
                    p.wait_for_timeout(300)
                    check(p.evaluate("window.__kiosk") is False, f"{where}: wide again, kiosk mode off")
                    p.set_viewport_size({"width": width, "height": height})
                    app.wait_for_selector("html.narrow", state="attached")
                    p.wait_for_timeout(300)
                    sent = p.evaluate("""window.__messages.filter((m) => m.type.endsWith("-properties"))
                                         .map((m) => m.type.split("/")[1] + (m.type.endsWith("/subscribe-properties") ? ":" + m.kioskMode : ""))""")
                    check(p.evaluate("window.__kiosk") is True and sent == [
                        "subscribe-properties:true", "unsubscribe-properties", "subscribe-properties:false",
                        "subscribe-properties:true"], f"{where}: narrow again, kiosk mode on, no more messages ({sent})")
                    header(app, lang, True, "overview", safe)
            done(p, where)

        print("==> In Home Assistant's frame")
        p = page(1200, 760, "light", "en-US", base + "/")
        frame = p.frame_locator("#ingress")
        frame.locator("header h1").wait_for()
        check(frame.locator("header h1").text_content() == NAME, "page shown in the ingress frame")
        frame.locator(f'header a[href="/config/app/{SLUG}/config"]').click()
        p.wait_for_url(f"**/config/app/{SLUG}/config")
        check(p.url == f"{base}/config/app/{SLUG}/config", "settings open outside the frame")
        done(p, "frame")

        print("==> Tabs in Home Assistant's frame")
        p = page(1200, 760, "light", "en-US", base + "/")
        frame = p.frame_locator("#ingress")
        frame.locator("header h1").wait_for()
        app = next(f for f in p.frames if f.parent_frame is not None)
        for label, href in (("Clients", "?tab=clients"), ("Upstream", "?tab=upstream")):
            with p.expect_event("framenavigated", predicate=lambda f: f is app):
                frame.locator("nav.tabs > a.tab", has_text=label).click()
            frame.locator("nav.tabs > a.tab.active", has_text=label).wait_for()
            check(app.url == base + INGRESS + href, f"frame: the {label} tab opens {href} ({app.url})")
        with p.expect_event("framenavigated", predicate=lambda f: f is app):
            app.evaluate("setTimeout(() => location.reload(), 0)")
        frame.locator("nav.tabs > a.tab.active", has_text="Upstream").wait_for()
        check(app.url == base + INGRESS + "?tab=upstream", f"frame: reload stays on the view ({app.url})")
        with p.expect_event("framenavigated", predicate=lambda f: f is app):
            app.evaluate("setTimeout(() => history.back(), 0)")  # the browser's back, inside the frame
        frame.locator("nav.tabs > a.tab.active", has_text="Clients").wait_for()
        check(app.url == base + INGRESS + "?tab=clients", f"frame: back returns to the previous view ({app.url})")
        with p.expect_event("framenavigated", predicate=lambda f: f is app):
            frame.locator("nav.tabs > a.tab", has_text="Overview").click()
        frame.locator("nav.tabs > a.tab.active", has_text="Overview").wait_for()
        check(app.url == base + INGRESS + "?tab=overview", f"frame: the Overview tab opens ?tab=overview ({app.url})")
        with p.expect_event("framenavigated", predicate=lambda f: f is app):
            frame.locator("a.state[href='?tab=clients']").click()
        frame.locator("nav.tabs > a.tab.active", has_text="Clients").wait_for()
        check(app.url == base + INGRESS + "?tab=clients", "frame: the overview's client line leads to the clients")
        done(p, "tabs")

        # The browser asks for English; the page follows Home Assistant's language
        # (share/ha-app/ha-lang.js): one reload when it changes, none when not
        print("==> In Home Assistant's language, not the browser's")
        context = browser.new_context(viewport={"width": 1200, "height": 760}, locale="en-US",
                                      extra_http_headers={"Accept-Language": "en"})
        context.add_init_script(WATCH_CSP)
        p = context.new_page()
        p.errors = []
        p.on("console", lambda m: m.type == "error" and p.errors.append(f"console: {m.text} ({m.location.get('url')})"))
        p.on("pageerror", lambda e: p.errors.append(f"script: {e}"))
        for ha_lang, lang, expected in (("de", "de", 2), ("de", "de", 1), ("en-GB", "en", 2), ("fr", "en", 2),
                                        ("fr", "en", 1)):
            where = f"Home Assistant in {ha_lang}"
            app, loads = in_home_assistant(p, base, ha_lang)
            shown = app.evaluate("[document.documentElement.lang, getComputedStyle(document.documentElement).visibility]")
            check(shown == [lang, "visible"], f"{where}: page in {lang} and shown ({shown})")
            check(loads == expected, f"{where}: loaded {expected} time(s) ({loads})")
            header(app, {"de": "de-DE", "en": "en-US"}[lang], False)
            held = [(k["value"], k["path"]) for k in context.cookies() if k["name"] == "ha_lang"]
            check(held == [(ha_lang.split("-")[0], INGRESS)], f"{where}: cookie for the ingress path ({held})")
        done(p, "language")
        browser.close()


def in_home_assistant(p: Page, base: str, lang: str) -> tuple[Frame, int]:
    """Opens the page in a stand-in for Home Assistant's frontend in that language;
    the app's frame and how often it loaded."""
    loads: list[str] = []

    def seen(frame: Frame) -> None:
        if frame.parent_frame is not None and frame.url.startswith(base + INGRESS):
            loads.append(frame.url)

    p.on("framenavigated", seen)
    p.goto(f"{base}/?lang={lang}")
    p.wait_for_timeout(1500)  # room for another reload, which must not come
    p.remove_listener("framenavigated", seen)
    app = next(f for f in p.frames if f.parent_frame is not None)
    app.locator("body > header h1").wait_for()
    return app, len(loads)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="check only, take no screenshots")
    parser.add_argument("--image", default="time-server:check", help="the built image (default time-server:check)")
    parser.add_argument("--build", action="store_true", help="build the image first")
    args = parser.parse_args()
    if args.build:
        subprocess.run(["docker", "build", "-q", "--build-arg", f"BUILD_VERSION={VERSION}", "-t", args.image,
                        str(ROOT / "time_server")], check=True, stdout=subprocess.DEVNULL)
    if not args.check:
        OUT.mkdir(parents=True, exist_ok=True)
    try:
        run(args.image, not args.check)
    finally:
        if problems:
            print(docker("logs", APP, check_=False))
        cleanup()
    if problems:
        print("FAILED:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("Page check passed." + ("" if args.check else f" Screenshots in {OUT.relative_to(ROOT)}"))


if __name__ == "__main__":
    main()
