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

IDENTITY: WHY A CUSTOM COMPONENT AND NOT A COOKIE
=================================================
A random UUID in the browser's localStorage, read back through a Streamlit
custom component (src/analytics/rl_identity/).

The obvious approach — write a first-party cookie, read it via
st.context.cookies — DOES NOT WORK on Streamlit Community Cloud. Measured on the
deployed app:

    cookies_type              StreamlitCookies
    cookie_keys               []
    cookie_count              0
    raw_cookie_header_present False
    header_names              [... Sec-Websocket-Key, Upgrade, Connection,
                               X-Forwarded-For, X-Streamlit-User]

The headers Python sees are the WEBSOCKET UPGRADE headers, and Cloud's proxy
does not forward the Cookie header through that upgrade. The browser holds the
cookie perfectly well; the server is simply never told about it.

That produced a silent, nasty failure: the writer only wrote when the cookie was
absent, so the browser kept its original id forever while the server minted a
fresh UUID on every session. The two diverged with nothing to indicate it, and
PostHog recorded a new person per reload.

The component fixes it structurally, not by patching the symptom:

  * It returns a value to Python over the component channel — a websocket
    message, not a request header — so the proxy's header handling is irrelevant.
  * localStorage is the SINGLE source of truth. The server never mints an id at
    all; it only ever reports what the browser gave it. Two stores cannot
    disagree when there is only one store. The legacy cookie is actively cleared.

Still NO fingerprinting. The id is a random UUID generated in the browser, never
derived from IP or user-agent. `st.context.ip_address` is never read, and no IP
is sent to PostHog. If storage is blocked the visit counts as new — an accepted,
stated undercount rather than a reason to fingerprint.

LIMITS ON RETURNING-VISITOR COUNTS (both understate, never overstate)
=====================================================================
1. Storage blocked or cleared -> the visit is still counted, under an id that
   lasts one page load, tagged `id_type: "ephemeral"`. Compare that share
   against "persistent" to size the blind spot instead of guessing at it.

2. Safari caps script-writable storage at 7 days. localStorage written by
   JavaScript — which is what this is — falls under Intelligent Tracking
   Prevention's cap: if a Safari user does not visit the site as a first party
   within 7 days, the storage is evicted and they return as a new visitor.
   A Safari visitor returning fortnightly is therefore invisible as a returnee.

   Note this was NOT avoidable by staying with cookies: ITP caps
   document.cookie-written cookies the same way. Only a server-set HttpOnly
   cookie escapes it, and Streamlit Cloud gives no way to set one — the same
   proxy that strips the Cookie header is in the path.

Chrome and Firefox have no equivalent 7-day cap today, so this skews the
undercount toward Safari and iOS traffic specifically.

FAIL-OPEN
=========
Analytics must never be able to break the product. With no API key configured
the module is a silent no-op, and every network path is wrapped so a PostHog
outage cannot raise into the Streamlit thread.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

_SESSION_FLAG = "_rl_analytics_session"
_LAST_PAGE = "_rl_analytics_last_page"
_CLIENT = "_rl_analytics_client"
_DISTINCT = "_rl_analytics_distinct_id"
_PENDING = "_rl_analytics_pending"
_UNRESOLVED_RUNS = "_rl_analytics_unresolved_runs"
_ID_EPHEMERAL = "_rl_analytics_id_ephemeral"


def _secret(name: str, default: str = "") -> str:
    """Read from st.secrets, then the environment. Never raises."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    import os
    return os.environ.get(name, default)


def _analytics_on() -> bool:
    return bool(_secret("POSTHOG_API_KEY"))


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


@st.cache_resource(show_spinner=False)
def _identity_component():
    """The declared component, built once per process."""
    return components.declare_component(
        "rl_identity", path=str(Path(__file__).parent / "rl_identity")
    )


def ensure_identity() -> str | None:
    """Render the identity component once per run and return the visitor id.

    Call this ONCE, early, from the entrypoint — not from inside event(). A
    keyed component may only be rendered once per run, and event() can fire
    several times in a single run.

    Returns None on the very first run of a session: the component has not yet
    reported back. Setting a component value triggers a rerun, so the id arrives
    moments later and the events buffered in the meantime are flushed then.
    """
    known = st.session_state.get(_DISTINCT)
    if known:
        # Drain anything that queued up while the id was still unknown.
        if st.session_state.get(_PENDING):
            client = _client()
            if client is not None:
                _flush(client, known)
        return known
    if not _analytics_on():
        # No key configured: never touch the visitor's browser storage at all.
        return None

    vid = None
    try:
        vid = _identity_component()(key="rl_identity", default=None)
    except Exception:
        vid = None

    if not vid:
        # The component has not answered. Normally it answers on the next rerun,
        # so wait one round. But if it has still not answered after that — the
        # iframe failed to load, or storage access is throwing in a way the
        # component could not recover from — fall back to an EPHEMERAL id for
        # this session rather than let the buffer sit there.
        #
        # The alternative is losing the events entirely, which would understate
        # total traffic, not merely returning visitors. A visit we cannot
        # recognise must still be counted, as a new one.
        runs = st.session_state.get(_UNRESOLVED_RUNS, 0) + 1
        st.session_state[_UNRESOLVED_RUNS] = runs
        if runs >= 2:
            st.session_state[_DISTINCT] = f"eph-{uuid.uuid4()}"
            st.session_state[_ID_EPHEMERAL] = True
            client = _client()
            if client is not None:
                _flush(client, st.session_state[_DISTINCT])
            return st.session_state[_DISTINCT]
        return None

    # The component answers with {id, persistent}. persistent=False means the
    # browser could not store the id, so this visit counts as new — recorded,
    # and marked so the blind spot is measurable.
    if isinstance(vid, dict):
        resolved, persistent = str(vid.get("id") or ""), bool(vid.get("persistent"))
    else:
        resolved, persistent = str(vid), True
    if not resolved:
        return None

    st.session_state[_DISTINCT] = resolved
    st.session_state[_ID_EPHEMERAL] = not persistent
    # Flush HERE, not lazily on the next event. On the rerun where the id
    # arrives, start_session() and page_view() both return early (their guards
    # are already set from the first run), so no further event() call happens
    # and a lazy flush would silently drop the session's first page_view — the
    # one carrying the referrer.
    client = _client()
    if client is not None:
        _flush(client, resolved)
    return resolved


def distinct_id() -> str | None:
    """The resolved visitor id, or None until the browser has reported it.

    Read-only: this never mints an id. The browser is the only source, which is
    what makes server and browser incapable of diverging.
    """
    return st.session_state.get(_DISTINCT)


def debug_identity() -> dict:
    """What the SERVER can actually see about cookies. Diagnostic only.

    Cookie VALUES are redacted apart from our own rl_vid, which is a random UUID
    we minted: the same jar carries Streamlit's XSRF token, and this renders in
    the page behind nothing more than a query parameter.
    """
    out: dict = {}
    try:
        cookies = dict(st.context.cookies or {})
        out["cookies_type"] = type(st.context.cookies).__name__
        out["cookie_keys"] = sorted(cookies)
        out["cookie_count"] = len(cookies)
        out["rl_vid_in_context"] = cookies.get("rl_vid", "<ABSENT — expected: identity no longer uses cookies>")
    except Exception as e:
        out["cookies_error"] = f"{type(e).__name__}: {e}"

    try:
        headers = dict(st.context.headers or {})
        raw = headers.get("Cookie") or headers.get("cookie")
        out["header_names"] = sorted(headers)
        out["raw_cookie_header_present"] = raw is not None
        out["raw_cookie_header_len"] = len(raw) if raw else 0
        out["raw_cookie_names"] = (
            sorted(c.split("=")[0].strip() for c in raw.split(";")) if raw else []
        )
    except Exception as e:
        out["headers_error"] = f"{type(e).__name__}: {e}"

    out["server_side_distinct_id"] = st.session_state.get(_DISTINCT, "<not yet resolved>")
    out["identity_source"] = "localStorage via rl_identity component"
    out["analytics_enabled"] = _analytics_on()
    out["buffered_events"] = len(st.session_state.get(_PENDING, []))
    return out


def event(name: str, **props) -> None:
    """Send one event. Silent no-op when analytics is off; never raises.

    Events raised before the browser has reported its id are BUFFERED rather
    than dropped or sent under a throwaway id. Without this the first page_view
    of every session — the most important one, since it carries the referrer —
    would either vanish or be attributed to a person who never existed.
    """
    client = _client()
    if client is None:
        return

    clean = {k: v for k, v in props.items() if v is not None}
    vid = distinct_id()

    if vid is None:
        pending = st.session_state.setdefault(_PENDING, [])
        if len(pending) < 50:          # bounded; a stuck component can't grow it forever
            pending.append((name, clean))
        return

    _flush(client, vid)
    _capture(client, vid, name, clean)


def _flush(client, vid: str) -> None:
    """Send anything buffered before the id arrived, in original order."""
    pending = st.session_state.get(_PENDING)
    if not pending:
        return
    st.session_state[_PENDING] = []
    for name, props in pending:
        _capture(client, vid, name, props)


def _capture(client, vid: str, name: str, props: dict) -> None:
    # id_type rides on every event so the size of the blind spot is measurable
    # rather than assumed: "ephemeral" means this visitor's browser storage was
    # unavailable, so they count as new and can never be seen returning. Compare
    # its share against "persistent" to know how much the returning-visitor
    # number is understated.
    props = dict(props)
    props["id_type"] = "ephemeral" if st.session_state.get(_ID_EPHEMERAL) else "persistent"
    try:
        client.capture(distinct_id=vid, event=name, properties=props)
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
