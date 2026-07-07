"""
ingest.py — Parse a Substack export zip into tidy DataFrames.

The Substack export contains, at minimum:
  - posts.csv                 : one row per post (metadata)
  - email_list.*.csv          : one row per current subscriber
  - posts/<id>.opens.csv      : one row per OPEN event (rich: geo, device, OS, client)
  - posts/<id>.delivers.csv   : one row per DELIVERED email
  - posts/<id>.<slug>.html    : full post body

v0.1 only read posts.csv + email_list.csv. This module reads *everything*,
and — critically — stitches the per-post opens/delivers files into two long
DataFrames keyed by the numeric post id so we can compute real engagement.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from io import BytesIO

import pandas as pd

# posts/<numericid>.opens.csv  /  posts/<numericid>.delivers.csv
_EVENT_RE = re.compile(r"(?:^|/)(\d+)\.(opens|delivers)\.csv$")
# posts/<numericid>.<slug>.html
_HTML_RE = re.compile(r"(?:^|/)(\d+)\.[^/]+\.html$")


def _numeric_id(post_id: str) -> str:
    """posts.csv post_id looks like '203033910.three-questions-...' -> '203033910'."""
    return str(post_id).split(".", 1)[0]


def _is_junk(name: str) -> bool:
    return "__MACOSX" in name or name.endswith("/")


@dataclass
class Export:
    """Everything we parsed out of one Substack export zip."""

    posts: pd.DataFrame = field(default_factory=pd.DataFrame)
    subscribers: pd.DataFrame = field(default_factory=pd.DataFrame)
    opens: pd.DataFrame = field(default_factory=pd.DataFrame)       # long: one row per open event
    delivers: pd.DataFrame = field(default_factory=pd.DataFrame)    # long: one row per delivery
    html_by_id: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.posts.empty and not self.subscribers.empty


def _read_csv(z: zipfile.ZipFile, name: str) -> pd.DataFrame | None:
    try:
        with z.open(name) as f:
            df = pd.read_csv(f)
        return df if not df.empty else None
    except Exception:
        return None


def load_export(uploaded_file) -> Export:
    """Parse a Substack export zip (path, file-like, or bytes) into an Export."""
    exp = Export()

    if isinstance(uploaded_file, (bytes, bytearray)):
        uploaded_file = BytesIO(uploaded_file)

    try:
        z = zipfile.ZipFile(uploaded_file)
    except zipfile.BadZipFile:
        exp.warnings.append("Uploaded file is not a valid zip archive.")
        return exp

    with z:
        names = [n for n in z.namelist() if not _is_junk(n)]

        # --- posts.csv ---
        posts_name = next((n for n in names if n.endswith("posts.csv")), None)
        if posts_name:
            df = _read_csv(z, posts_name)
            if df is not None:
                exp.posts = _clean_posts(df, exp)

        # --- subscriber list (email_list.*.csv or subscribers.csv) ---
        subs_name = next(
            (n for n in names
             if n.endswith(".csv")
             and ("email_list" in n.lower() or "subscribers" in n.lower())),
            None,
        )
        if subs_name:
            df = _read_csv(z, subs_name)
            if df is not None:
                exp.subscribers = _clean_subscribers(df, exp)

        # --- per-post opens / delivers events ---
        opens_frames, delivers_frames = [], []
        for n in names:
            m = _EVENT_RE.search(n)
            if not m:
                continue
            numeric_id, kind = m.group(1), m.group(2)
            df = _read_csv(z, n)
            if df is None:
                continue
            df["numeric_id"] = numeric_id
            (opens_frames if kind == "opens" else delivers_frames).append(df)

        if opens_frames:
            exp.opens = _clean_events(pd.concat(opens_frames, ignore_index=True))
        if delivers_frames:
            exp.delivers = _clean_events(pd.concat(delivers_frames, ignore_index=True))

        # --- full-text HTML bodies (kept lazily as raw strings) ---
        for n in names:
            m = _HTML_RE.search(n)
            if not m:
                continue
            try:
                with z.open(n) as f:
                    exp.html_by_id[m.group(1)] = f.read().decode("utf-8", "ignore")
            except Exception:
                pass

    if exp.posts.empty:
        exp.warnings.append("No posts.csv found in the archive.")
    if exp.subscribers.empty:
        exp.warnings.append("No subscriber list (email_list/subscribers csv) found.")
    if exp.opens.empty:
        exp.warnings.append("No per-post opens.csv files found — engagement views need these.")

    return exp


# --------------------------------------------------------------------------- #
# Cleaners
# --------------------------------------------------------------------------- #

def _to_dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True)


def _clean_posts(df: pd.DataFrame, exp: Export) -> pd.DataFrame:
    df = df.copy()
    if "post_date" in df:
        df["post_date"] = _to_dt(df["post_date"])
    if "email_sent_at" in df:
        df["email_sent_at"] = _to_dt(df["email_sent_at"])
    if "is_published" in df:
        df["is_published"] = df["is_published"].astype(str).str.lower().eq("true")
    if "post_id" in df:
        df["numeric_id"] = df["post_id"].map(_numeric_id)
    for col in ("title", "subtitle", "type", "audience"):
        if col in df:
            df[col] = df[col].fillna("").astype(str)
    return df


def _clean_subscribers(df: pd.DataFrame, exp: Export) -> pd.DataFrame:
    df = df.copy()
    for col in ("created_at", "first_payment_at", "expiry"):
        if col in df:
            df[col] = _to_dt(df[col])
    if "active_subscription" in df:
        df["is_paid"] = df["active_subscription"].astype(str).str.lower().eq("true")
    return df


def _parse_ua(ua: str) -> tuple[str, str]:
    """Best-effort (device, os) from a user-agent. Substack ships these columns
    blank, but the raw UA is always present — so we recover them here."""
    if not ua or ua.strip() in ("", "Mozilla/5.0"):
        # bare UA == Apple Mail Privacy Protection / generic proxy prefetch
        return "Privacy proxy", "Privacy proxy"
    s = ua
    if "GoogleImageProxy" in s:
        return "Gmail (proxy)", "Gmail"
    if "iPhone" in s or "iPod" in s:
        return "Mobile", "iOS"
    if "iPad" in s:
        return "Tablet", "iPadOS"
    if "Android" in s:
        return "Tablet" if "Mobile" not in s else "Mobile", "Android"
    if "Macintosh" in s or "Mac OS X" in s:
        return "Desktop", "macOS"
    if "Windows" in s:
        return "Desktop", "Windows"
    if "Linux" in s or "X11" in s:
        return "Desktop", "Linux"
    return "Other", "Other"


def _clean_events(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "timestamp" in df:
        df["timestamp"] = _to_dt(df["timestamp"])
    if "active_subscription" in df:
        df["is_paid"] = df["active_subscription"].astype(str).str.lower().eq("true")
    # normalize the geo/device columns that only exist on opens
    for col in ("country", "city", "region", "device_type", "client_os", "client_type"):
        if col in df:
            df[col] = df[col].fillna("").astype(str).str.strip()
    # Substack leaves device_type/client_os blank — recover them from user_agent.
    if "user_agent" in df:
        derived = df["user_agent"].fillna("").map(_parse_ua)
        dev = derived.map(lambda t: t[0])
        os_ = derived.map(lambda t: t[1])
        if "device_type" in df:
            df["device_type"] = df["device_type"].mask(df["device_type"].eq(""), dev)
        else:
            df["device_type"] = dev
        if "client_os" in df:
            df["client_os"] = df["client_os"].mask(df["client_os"].eq(""), os_)
        else:
            df["client_os"] = os_
    return df
