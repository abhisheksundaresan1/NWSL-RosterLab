"""
src/analytics/track.py — the only module that talks to PostHog.

WHY SERVER-SIDE
===============
Streamlit is a single-page app whose navigation never triggers a page load, so a
client-side analytics snippet cannot see a "page view". Injecting one via
st.markdown is worse: scripts are stripped, and components.html runs inside a
child iframe that cannot observe the parent's routing at all.

Emitting from Python removes the problem rather than working around it. The
server already knows exactly which page rendered and which button was pressed,
so every event is a fact rather than an inference from the DOM.

THE CORRECTNESS RISK: RERUNS
============================
Streamlit re-executes the entire script on every widget interaction. A page_view
emitted per render would overcount by 10-50x and quietly make the numbers
worthless. Every event here is therefore de-duplicated through st.session_state:
page_view fires only when the page slug actually changes, session_start only
once per session. Download and query events need no guard — the Streamlit APIs
that produce them return True only on the interaction rerun.

IDENTITY AND PRIVACY
====================
A single first-party cookie, `rl_vid`, holding a random UUID. Nothing else.

There is deliberately NO fingerprint fallback. Hashing IP + user-agent would
recover some of the visitors who block cookies, but that is browser
fingerprinting on a public site, and the accuracy is not worth it. When the
cookie is unavailable the visit simply counts as new, which undercounts
returning visitors by an unknown amount — an accepted, stated limitation.

`st.context.ip_address` is never read. No IP address is sent to PostHog.

FAIL-OPEN
=========
Analytics must never be able to break the product. With no API key configured
the module is a silent no-op, and every network path is wrapped so a PostHog
outage cannot raise into the Streamlit thread.
"""

from __future__ import annotations

import uuid

import streamlit as st

_COOKIE = "rl_vid"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 365      # one year

_SESSION_FLAG = "_rl_analytics_session"
_LAST_PAGE = "_rl_analytics_last_page"
_CLIENT = "_rl_analytics_client"
_DISTINCT = "_rl_analytics_distinct_id"


def _secret(name: str, default: str = "") -> str:
    """Read from st.secrets, then the environment. Never raises."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    import os
    return os.environ.get(name, default)


def _client():
    """A configured PostHog client, or None when analytics is switched off.

    Cached in session_state rather than module scope so a missing key is
    re-checked cheaply and one bad import can never take the app down.
    """
    if _CLIENT in st.session_state:
        return st.session_state[_CLIENT]

    client = None
    key = _secret("POSTHOG_API_KEY")
    if key:
        try:
            from posthog import Posthog
            client = Posthog(
                project_api_key=key,
                host=_secret("POSTHOG_HOST", "https://us.i.posthog.com"),
                # Never let a slow or dead endpoint stall a page render.
                timeout=3,
                # Errors are swallowed rather than raised into the script thread.
                on_error=lambda *a, **k: None,
            )
        except Exception:
            client = None

    st.session_state[_CLIENT] = client
    return client


def _ensure_cookie_writer(vid: str) -> None:
    """Ask the browser to persist `vid` as a first-party cookie.

    It writes on the app's own origin, which is why the value survives to the
    next visit and makes a returning visitor recognisable at all.

    st.html(unsafe_allow_javascript=True), which runs the script in the main
    document. The two iframe-based alternatives were both tried and rejected:
    st.components.v1.html works but is deprecated with a removal date of
    2026-06-01 that has already passed, and st.iframe rendered no element at all
    at height=0. Running in the main document is also simpler — the script writes
    document.cookie directly instead of reaching through window.parent.
    """
    st.html(
        f"""<script>
        try {{
            if (document.cookie.indexOf("{_COOKIE}=") === -1) {{
                document.cookie = "{_COOKIE}={vid}; path=/; max-age={_COOKIE_MAX_AGE}; SameSite=Lax";
            }}
        }} catch (e) {{}}
        </script>""",
        unsafe_allow_javascript=True,
    )


def distinct_id() -> str:
    """Stable per-visitor id: the cookie when present, otherwise a fresh UUID.

    A fresh UUID means this visit is counted as a NEW visitor. That is the
    intended behaviour when cookies are blocked — see the module docstring.
    """
    if _DISTINCT in st.session_state:
        return st.session_state[_DISTINCT]

    vid = ""
    try:
        vid = st.context.cookies.get(_COOKIE, "") or ""
    except Exception:
        vid = ""

    if not vid:
        vid = str(uuid.uuid4())
        _ensure_cookie_writer(vid)

    st.session_state[_DISTINCT] = vid
    return vid


def event(name: str, **props) -> None:
    """Send one event. Silent no-op when analytics is off; never raises."""
    client = _client()
    if client is None:
        return
    try:
        client.capture(
            distinct_id=distinct_id(),
            event=name,
            properties={k: v for k, v in props.items() if v is not None},
        )
    except Exception:
        pass


def start_session() -> None:
    """Fire session_start exactly once per Streamlit session.

    Referrer is captured here and only here: in-app navigation produces no
    Referer header, so the value is meaningful for the first request alone.
    The ?ref= query parameter is the more reliable of the two, because Reddit
    and LinkedIn frequently strip or rewrite the header.
    """
    if st.session_state.get(_SESSION_FLAG):
        return
    st.session_state[_SESSION_FLAG] = True

    referrer = ref = locale = None
    try:
        referrer = st.context.headers.get("Referer")
        locale = st.context.locale
    except Exception:
        pass
    try:
        ref = st.query_params.get("ref")
    except Exception:
        pass

    event("session_start", referrer=referrer, ref=ref, locale=locale)


def page_view(slug: str) -> None:
    """Fire page_view only when the page actually changed.

    THE rerun guard. Without it every slider drag and every expander click would
    log another view of the same page.
    """
    if st.session_state.get(_LAST_PAGE) == slug:
        return
    st.session_state[_LAST_PAGE] = slug
    event("page_view", page=slug)


def card_download(card_type: str, **props) -> None:
    """A PNG card was downloaded — the share loop, and the key custom event."""
    event("card_download", card_type=card_type, **props)


def ask_query(mode: str, query: str | None = None, results: int | None = None) -> None:
    """A Scout query ran. `mode` is "canned" or "typed", counted separately."""
    event("ask_query", mode=mode, query=query, results=results)
