"""
content.py — Topic clustering over the full post bodies.

The export ships 172 HTML post bodies the rest of the app never touches. Here we
strip them to text, TF-IDF vectorize, and KMeans-cluster them into topics, then
auto-label each cluster by its most distinctive terms and join engagement so you
can see *which themes convert vs. just get opened*.

Heavy deps (scikit-learn, BeautifulSoup) are imported lazily so the rest of the
app runs without them.
"""

from __future__ import annotations

import re

import pandas as pd

from ingest import Export
from metrics import post_engagement

# Ultra-generic words that would otherwise dominate a finance newsletter's TF-IDF
# without distinguishing topics. Real topic words (options, dividend, crypto…) stay.
EXTRA_STOPWORDS = {
    "just", "like", "going", "want", "know", "think", "make", "get", "got",
    "one", "time", "way", "thing", "really", "good", "day", "week", "year",
    "im", "dont", "youre", "thats", "ll", "ve", "re", "https", "http", "com",
    "substack", "read", "post", "subscribe", "click", "www", "amp", "nbsp",
}


def _strip_html(raw: str) -> str:
    try:
        from bs4 import BeautifulSoup
        text = BeautifulSoup(raw, "html.parser").get_text(" ")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", text).strip()


def post_texts(exp: Export) -> pd.DataFrame:
    """DataFrame(numeric_id, text) for every post body we could parse."""
    rows = [{"numeric_id": nid, "text": _strip_html(raw)}
            for nid, raw in exp.html_by_id.items()]
    df = pd.DataFrame(rows)
    return df[df["text"].str.len() > 200] if not df.empty else df


def cluster_topics(exp: Export, k: int = 6, min_delivers: int = 30) -> pd.DataFrame:
    """Cluster post bodies into `k` topics, labelled and joined to open rate.

    Returns DataFrame(topic, label, n_posts, avg_open_rate, top_terms, examples).
    Empty if there aren't enough bodies to cluster.
    """
    texts = post_texts(exp)
    if len(texts) < k:
        return pd.DataFrame()

    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.feature_extraction import text as sk_text

    stop = list(sk_text.ENGLISH_STOP_WORDS.union(EXTRA_STOPWORDS))
    vec = TfidfVectorizer(stop_words=stop, ngram_range=(1, 2),
                          min_df=3, max_df=0.6, max_features=4000,
                          token_pattern=r"[A-Za-z$][A-Za-z$]+")
    X = vec.fit_transform(texts["text"])
    km = KMeans(n_clusters=k, random_state=0, n_init=10)
    texts = texts.copy()
    texts["topic"] = km.fit_predict(X)

    terms = vec.get_feature_names_out()
    centroids = km.cluster_centers_

    # engagement per post to roll up per topic
    eng = post_engagement(exp)
    rate = (eng.set_index("numeric_id")["open_rate"]
            if not eng.empty else pd.Series(dtype=float))
    deliv = (eng.set_index("numeric_id")["delivers"]
             if not eng.empty else pd.Series(dtype=float))
    titles = (exp.posts.set_index("numeric_id")["title"]
              if not exp.posts.empty and "title" in exp.posts else pd.Series(dtype=str))

    rows = []
    for c in range(k):
        members = texts[texts["topic"] == c]
        ids = members["numeric_id"].tolist()
        top_idx = centroids[c].argsort()[::-1][:6]
        top_terms = [terms[i] for i in top_idx]

        r = rate.reindex(ids).dropna()
        d = deliv.reindex(ids).dropna()
        # weight open rate by delivers so a topic isn't skewed by tiny sends
        avg = float((r * d).sum() / d.sum()) if d.sum() > 0 else float("nan")

        # example titles: the best-reaching posts in this cluster
        ex_ids = d.sort_values(ascending=False).head(3).index if not d.empty else ids[:3]
        examples = [str(titles.get(i, i)) for i in ex_ids]

        rows.append({
            "topic": c,
            "label": " / ".join(top_terms[:3]),
            "n_posts": len(ids),
            "avg_open_rate": avg,
            "top_terms": ", ".join(top_terms),
            "examples": examples,
        })

    out = pd.DataFrame(rows).sort_values("avg_open_rate", ascending=False, na_position="last")
    return out.reset_index(drop=True)
