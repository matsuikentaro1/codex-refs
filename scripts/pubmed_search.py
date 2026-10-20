#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pubmed_search.py  ―  codex-refs スキル用の「決定論的 PubMed 取得ツール」

目的:
  codex（や Claude）に PubMed E-utilities を叩く Python を毎回書かせると、
  PowerShell エスケープ・パスのスペース・429 レート制限・XML パース失敗で
  トークンを浪費して flailing する。その配管をこの 1 ファイルに固定し、
  呼び出し側は「検索語の設計」と「論文の選別」だけを行えばよいようにする。

LLM 側にしてほしいこと（このスクリプトがやらないこと）:
  - 主張(claim)に最適な PubMed 検索語(query)を考える
  - 返ってきた候補から、文脈に合う論文を選ぶ（--peek で一覧を見て --keep で確定）
  - 各論文の whats_interesting（なぜ選んだか）を --note で渡す

このスクリプトがやること（LLM にやらせない配管）:
  - esearch でヒット件数を先に確認（0件/多すぎを早期に検知）
  - efetch で書誌(著者/誌名/巻号頁/年/DOI/abstract)を「実取得」→ 捏造ゼロ
  - NCBI API キーの読み込み（env か .secrets/ファイル）と適切なレート制御
  - 429 / 5xx / タイムアウトへの指数バックオフ再試行
  - 文字コード(UTF-8)・CSV スキーマ・DOI/PMID 重複排除

使い方:
  # 1) 候補を覗く（書き込みなし。stdout に一覧）
  python pubmed_search.py --query "migraine global burden GBD prevalence disability" --peek

  # 2) 選んだ PMID を確定して CSV に追記
  python pubmed_search.py --keep 33069326,30353868 \
      --note "33069326=GBD2019 神経疾患の世界的負荷の基準文献; 30353868=片頭痛の社会経済損失" \
      --out _tmp_refs_01.csv

  # 3) ワンショット（query の relevance 上位 retmax 件をそのまま書き込み）
  python pubmed_search.py --query "..." --out _tmp_refs_01.csv --note "GBD headache burden"

  # 4) 接続・キーの自己診断
  python pubmed_search.py --selftest

  # 5) 根拠の一文を抄録と照合して確定（抄録に無い文は書き込まない）
  python pubmed_search.py --keep 33069326 --note "33069326=理由" \
      --quote "33069326=Headache disorders were the third leading cause of YLDs" --out _tmp_refs_01.csv

  # 6) 仕上げ: CSV 内の全引用を抄録と再照合
  python pubmed_search.py --verify-csv refs_20261020.csv

  芋づる（参考文献・被引用・類似・著者・本文の参考文献）は同じフォルダの citation_chain.py。

終了コード:  0=成功(1件以上) / 3=ヒット0件 / 4=接続/キー異常 / 5=原文不一致あり / 1=その他エラー

CSV 列(codex-refs スキーマと一致):
  PubMed_ID, Author, Year, Title, Journal, Volume, Issue, Pages, doi, abstract,
  whats_interesting1, whats_interesting2, whats_interesting3, whats_interesting4, whats_interesting5
"""

import argparse
import csv
import http.client
import io
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

# Windows の cp932 端末でも UTF-8 で出力（cec-sheet と同じ作法）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
USER_AGENT = "codex-refs-pubmed-search/1.0 (research use)"

FIELDNAMES = [
    "PubMed_ID", "Author", "Year", "Title", "Journal", "Volume", "Issue",
    "Pages", "doi", "abstract",
    "whats_interesting1", "whats_interesting2", "whats_interesting3",
    "whats_interesting4", "whats_interesting5",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)  # skills/codex-refs


# ============================================================
# API キーの読み込み（直書き禁止。env か未追跡ファイルから）
# ============================================================
def load_api_key():
    """NCBI API キーを (1) 環境変数 (2) .secrets/ファイル の順で探す。無ければ None。"""
    key = os.environ.get("NCBI_API_KEY", "").strip()
    if key:
        return key, "env:NCBI_API_KEY"
    for cand in (
        os.path.join(SKILL_DIR, ".secrets", "ncbi_api_key.txt"),
        os.path.join(SCRIPT_DIR, ".secrets", "ncbi_api_key.txt"),
    ):
        if os.path.exists(cand):
            with open(cand, "r", encoding="utf-8") as f:
                k = f.read().strip()
            if k:
                return k, cand
    return None, None


API_KEY, API_KEY_SRC = load_api_key()
# キーあり: 公称 10 req/s → 余裕をみて ~9/s。なし: 3 req/s → ~2.8/s。
MIN_INTERVAL = 0.11 if API_KEY else 0.36
_last_call = [0.0]


def _throttle():
    """連続呼び出しの間隔を空けて 429 を未然に防ぐ。"""
    dt = time.monotonic() - _last_call[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last_call[0] = time.monotonic()


def _http_get(url, params, timeout=60, max_retry=4):
    """E-utilities への GET。429/5xx/タイムアウトは指数バックオフで再試行。"""
    if API_KEY:
        params = dict(params, api_key=API_KEY)
    full = url + "?" + urllib.parse.urlencode(params)
    backoff = 1.0
    last_err = None
    for attempt in range(max_retry + 1):
        _throttle()
        try:
            req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retry:
                sys.stderr.write(f"  [retry] HTTP {e.code} → {backoff:.0f}s 待機して再試行\n")
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.HTTPException) as e:
            # URLError / タイムアウト / 接続リセット(RemoteDisconnected 等) / 不完全応答
            last_err = e
            if attempt < max_retry:
                sys.stderr.write(f"  [retry] {e} → {backoff:.0f}s 待機して再試行\n")
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
    if last_err:
        raise last_err


# ============================================================
# esearch: クエリ → ヒット件数 + PMID 一覧
# ============================================================
def esearch(query, retmax=8, sort="relevance", mindate=None, maxdate=None):
    import json
    params = {
        "db": "pubmed", "term": query, "retmax": retmax,
        "retmode": "json", "sort": sort,
    }
    if mindate:
        params.update(datetype="pdat", mindate=mindate, maxdate=maxdate or "3000")
    data = json.loads(_http_get(ESEARCH, params))
    res = data.get("esearchresult", {})
    return int(res.get("count", 0)), res.get("idlist", [])


# ============================================================
# efetch: PMID 一覧 → 書誌 dict（cec-sheet の解析ロジックを流用）
# ============================================================
def efetch(pmids):
    uniq = list(dict.fromkeys(p for p in pmids if p))
    if not uniq:
        return {}
    xml = _http_get(EFETCH, {"db": "pubmed", "id": ",".join(uniq), "retmode": "xml"})
    root = ET.fromstring(xml)
    out = {}
    for art in root.findall(".//PubmedArticle"):
        mc = art.find(".//MedlineCitation")
        pmid = mc.findtext("PMID")
        article = mc.find("Article")
        if article is None:
            continue
        at = article.find("ArticleTitle")
        title = "".join(at.itertext()) if at is not None else ""
        journal = article.find("Journal")
        iso = ""
        ji = None
        if journal is not None:
            iso = journal.findtext("ISOAbbreviation") or journal.findtext("Title") or ""
            ji = journal.find("JournalIssue")
        vol = (ji.findtext("Volume") or "") if ji is not None else ""
        issue = (ji.findtext("Issue") or "") if ji is not None else ""
        year = ""
        pd = ji.find("PubDate") if ji is not None else None
        if pd is not None:
            year = pd.findtext("Year") or (pd.findtext("MedlineDate") or "")[:4]
        pages = article.findtext(".//Pagination/MedlinePgn") or ""
        authors = []
        al = article.find("AuthorList")
        if al is not None:
            for au in al.findall("Author"):
                ln = au.findtext("LastName")
                ini = au.findtext("Initials") or ""
                if ln:
                    authors.append((ln + " " + ini).strip())
                elif au.findtext("CollectiveName"):
                    authors.append(au.findtext("CollectiveName"))
        doi = ""
        for el in article.findall("ELocationID"):
            if el.get("EIdType") == "doi":
                doi = el.text or ""
        if not doi:
            for aid in art.findall(".//ArticleIdList/ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = aid.text or ""
        abst = " ".join("".join(a.itertext()) for a in article.findall(".//Abstract/AbstractText"))
        # 論文種別（RCT/SR/MA の機械的な 1 次ふるい用。CSV には書かない）
        ptypes = [pt.text or "" for pt in article.findall(".//PublicationTypeList/PublicationType")]
        out[pmid] = {
            "PubMed_ID": pmid, "Author": "; ".join(authors), "Year": year,
            "Title": title, "Journal": iso, "Volume": vol, "Issue": issue,
            "Pages": pages, "doi": doi, "abstract": abst,
            "_pubtypes": ptypes,
        }
    return out


def efetch_many(pmids, chunk=200):
    """efetch を chunk 件ずつに分けて呼ぶ（芋づるで数百件になるとき用）。"""
    uniq = list(dict.fromkeys(p for p in pmids if p))
    out = {}
    for i in range(0, len(uniq), chunk):
        out.update(efetch(uniq[i:i + chunk]))
    return out


# ============================================================
# 原文照合: 「この論文が主張を支える」根拠の一文を、取得した
# title+abstract にそのまま在るか機械で確かめる（言い換え・捏造の防壁）
# ============================================================
QUOTE_TAG = '｜原文:"'


def _norm(s):
    """照合用の正規化。NFKC・引用符/ダッシュの統一・空白の圧縮のみ（語は変えない）。"""
    import unicodedata
    s = unicodedata.normalize("NFKC", s or "")
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("−", "-"), ("‐", "-")):
        s = s.replace(a, b)
    return " ".join(s.split())


def verify_quote(rec, quote):
    """quote が title+abstract にそのまま在れば (True, "")。
    「…」で区切った断片は、順番どおりに在ればよい（省略を許すが言い換えは許さない）。
    無ければ (False, いちばん近い文)。"""
    import difflib
    import re
    src = _norm(rec.get("Title", "") + " " + rec.get("abstract", ""))
    parts = [_norm(p) for p in re.split(r"…|\.\.\.", quote) if _norm(p)]
    if not parts:
        return False, "（引用が空）"
    pos = 0
    for p in parts:
        i = src.find(p, pos)
        if i < 0:
            sents = re.split(r"(?<=[.!?])\s+", src)
            best = max(sents, key=lambda s: difflib.SequenceMatcher(None, p, s).ratio(), default="")
            return False, best
        pos = i + len(p)
    return True, ""


def parse_quotes(spec):
    """--quote の書式: 'PMID=原文の一文 || PMID=原文の一文'（文中の ; を壊さないよう || で区切る）。"""
    quotes = {}
    for chunk in (spec or "").split("||"):
        pid, sep, txt = chunk.strip().partition("=")
        if sep and pid.strip().isdigit() and txt.strip():
            quotes[pid.strip()] = txt.strip()
    return quotes


def verify_csv(path):
    """refs CSV 内の ｜原文:"..." を、PubMed から抄録を取り直して全件照合する（最終チェック）。"""
    import re
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    recs = efetch_many([r.get("PubMed_ID", "") for r in rows])
    n_q, bad, noquote = 0, [], []
    for r in rows:
        pmid = r.get("PubMed_ID", "")
        qs = []
        for col in FIELDNAMES[10:]:
            qs += re.findall(r'｜原文:"(.*?)"(?=$|\s|｜)', r.get(col, "") or "")
        if not qs:
            noquote.append(pmid)
            continue
        for q in qs:
            n_q += 1
            ok, near = verify_quote(recs.get(pmid, {}), q)
            if not ok:
                bad.append((pmid, q, near))
    print(f"[verify] {len(rows)} 件中 原文つき {len(rows) - len(noquote)} 件・引用 {n_q} 本を照合")
    for pmid, q, near in bad:
        print(f"  ✗ PMID {pmid}: 抄録に無い →「{q}」\n      近い文:「{near}」")
    if noquote:
        print(f"  （原文なし）PMID: {', '.join(noquote)}")
    print("[verify] OK: 全引用が抄録に在る" if not bad else f"[verify] NG: {len(bad)} 本が不一致")
    return 0 if not bad else 5


# ============================================================
# --note のパース: "PMID=理由; PMID=理由" → {pmid: 理由}
# ============================================================
def parse_notes(note, pmids):
    """--note の書式は柔軟に。'='で PMID 別、無ければ全件に同じ文を付ける。"""
    notes = {}
    if not note:
        return notes
    if "=" not in note:
        return {p: note.strip() for p in pmids}
    for chunk in note.replace("||", ";").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        pid, _, txt = chunk.partition("=")
        notes[pid.strip()] = txt.strip()
    return notes


# ============================================================
# CSV 追記（DOI / PMID 重複排除）
# ============================================================
def append_csv(out_path, records, notes):
    existing = []
    seen_doi, seen_pmid = set(), set()
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                existing.append(row)
                if row.get("doi"):
                    seen_doi.add(row["doi"].lower())
                if row.get("PubMed_ID"):
                    seen_pmid.add(row["PubMed_ID"])

    added, skipped = [], []
    for pmid, rec in records.items():
        doi = (rec.get("doi") or "").lower()
        if pmid in seen_pmid or (doi and doi in seen_doi):
            # 既存 → 次の空き whats_interesting に選定理由を追記
            for row in existing:
                if row.get("PubMed_ID") == pmid or (doi and (row.get("doi") or "").lower() == doi):
                    for col in FIELDNAMES[10:]:
                        if not row.get(col):
                            row[col] = notes.get(pmid, "")
                            break
                    break
            skipped.append(pmid)
            continue
        row = {k: rec.get(k, "") for k in FIELDNAMES}
        row["whats_interesting1"] = notes.get(pmid, "")
        existing.append(row)
        seen_pmid.add(pmid)
        if doi:
            seen_doi.add(doi)
        added.append(pmid)

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for row in existing:
            w.writerow({k: row.get(k, "") for k in FIELDNAMES})
    return added, skipped


def _fmt_candidate(rec, abstract_chars=320):
    """agentic な relevance 判断ができるよう、書誌1行 + abstract スニペットを返す。"""
    au = rec.get("Author", "").split(";")[0].strip() or "—"
    nauth = len([a for a in rec.get("Author", "").split(";") if a.strip()])
    who = au + (" et al." if nauth > 1 else "")
    head = (f"  PMID {rec['PubMed_ID']:>9} | {rec.get('Year','????'):>4} | {who} | "
            f"{rec.get('Journal','')} | {rec.get('Title','')}")
    doi = rec.get("doi", "")
    meta = f"      doi: {doi}" if doi else "      doi: —"
    abst = (rec.get("abstract", "") or "").strip().replace("\n", " ")
    if len(abst) > abstract_chars:
        abst = abst[:abstract_chars].rstrip() + " …"
    snip = f"      abstract: {abst}" if abst else "      abstract: （なし）"
    return head + "\n" + meta + "\n" + snip


def main():
    ap = argparse.ArgumentParser(description="決定論的 PubMed 取得ツール（codex-refs 用）")
    ap.add_argument("--query", help="PubMed 検索式")
    ap.add_argument("--keep", help="確定する PMID（カンマ区切り）。--query 不要")
    ap.add_argument("--note", help="選定理由。'PMID=理由; PMID=理由' or 全件共通の 1 文")
    ap.add_argument("--out", help="追記先 CSV パス")
    ap.add_argument("--retmax", type=int, default=8, help="候補取得件数（既定 8）")
    ap.add_argument("--sort", default="relevance", help="esearch ソート（relevance/pub_date）")
    ap.add_argument("--mindate", help="発行年の下限（例 2010）")
    ap.add_argument("--maxdate", help="発行年の上限")
    ap.add_argument("--peek", action="store_true", help="候補一覧を表示するだけ（CSV 書込なし）")
    ap.add_argument("--quote", help="--keep 時の根拠の一文（抄録からそのまま）。'PMID=文 || PMID=文'。"
                                    "抄録に無ければその PMID は書き込まない")
    ap.add_argument("--verify-csv", help="CSV 内の ｜原文:\"...\" を抄録と全件照合して終了")
    ap.add_argument("--selftest", action="store_true", help="接続/キーの自己診断")
    args = ap.parse_args()

    # キーの状態を最初に明示
    if API_KEY:
        sys.stderr.write(f"[key] NCBI API key 検出 ({API_KEY_SRC}) → ~9 req/s\n")
    else:
        sys.stderr.write("[key] NCBI API key 無し → 3 req/s（無くても動作可。.secrets/ncbi_api_key.txt で高速化）\n")

    if args.selftest:
        try:
            cnt, ids = esearch("headache", retmax=1)
            recs = efetch(ids)
            ok = bool(recs)
            print(f"[selftest] esearch ok (count={cnt}), efetch ok={ok}")
            sys.exit(0 if ok else 4)
        except Exception as e:
            print(f"[selftest] 失敗: {e}")
            sys.exit(4)

    if args.verify_csv:
        try:
            sys.exit(verify_csv(args.verify_csv))
        except Exception as e:
            print(f"[error] 照合失敗: {e}")
            sys.exit(1)

    try:
        if args.keep:
            pmids = [p.strip() for p in args.keep.split(",") if p.strip()]
            recs = efetch_many(pmids)
            missing = [p for p in pmids if p not in recs]
            if missing:
                sys.stderr.write(f"[warn] efetch で取得できなかった PMID: {', '.join(missing)}\n")
        elif args.query:
            cnt, ids = esearch(args.query, retmax=args.retmax, sort=args.sort,
                               mindate=args.mindate, maxdate=args.maxdate)
            sys.stderr.write(f"[esearch] ヒット {cnt} 件 / 取得 {len(ids)} 件: {args.query}\n")
            if cnt == 0:
                print("[結果] ヒット 0 件。別の検索語で再試行を（同義語/MeSH/AND→OR/年指定の解除など）。")
                sys.exit(3)
            if cnt > 2000:
                sys.stderr.write(f"[hint] ヒットが多すぎ({cnt}件)。relevance上位{len(ids)}件のみ表示。絞り込み推奨。\n")
            recs = efetch(ids)
        else:
            ap.error("--query か --keep のどちらかが必要です")
    except Exception as e:
        print(f"[error] 取得失敗: {e}")
        sys.exit(1)

    if not recs:
        print("[結果] 書誌を取得できませんでした。")
        sys.exit(3)

    # 候補一覧（relevance 順を維持して表示）
    order = (args.keep.split(",") if args.keep else None)
    items = list(recs.values())
    print(f"\n=== 候補 {len(items)} 件 ===")
    for rec in items:
        # PMID 指定で覗くときは抄録を切らない（--quote に写す文を探すため）
        print(_fmt_candidate(rec, abstract_chars=10 ** 6 if args.keep else 320))

    if args.peek or not args.out:
        print("\n[peek] CSV 未書込。abstract を読んで relevance を判断し、"
              "必要なら別クエリで再 --peek（広げる/絞る/同義語/MeSH）。"
              "良ければ --keep <PMID,...> --note \"PMID=理由\" --out <csv> で確定。")
        sys.exit(0)

    notes = parse_notes(args.note, list(recs.keys()))
    rejected = []
    if args.quote:
        for pmid, q in parse_quotes(args.quote).items():
            if pmid not in recs:
                continue
            ok, near = verify_quote(recs[pmid], q)
            if ok:
                notes[pmid] = (notes.get(pmid, "") + " " + QUOTE_TAG + q + '"').strip()
            else:
                rejected.append(pmid)
                print(f"\n[quote ✗] PMID {pmid}: 引用が抄録にそのまま無い → 書き込まない\n"
                      f"    渡した文:「{q}」\n    近い文  :「{near}」")
        for pmid in rejected:
            recs.pop(pmid)
    added, skipped = append_csv(args.out, recs, notes) if recs else ([], [])
    print(f"\n[CSV] {args.out} へ 追加 {len(added)} 件 / 既存追記 {len(skipped)} 件")
    if added:
        print("  追加 PMID: " + ", ".join(added))
    if skipped:
        print("  既存(理由を追記) PMID: " + ", ".join(skipped))
    if rejected:
        print("  原文不一致で未書込 PMID: " + ", ".join(rejected) + "（抄録の文をそのまま写して再実行）")
        sys.exit(5)
    sys.exit(0)


if __name__ == "__main__":
    main()
