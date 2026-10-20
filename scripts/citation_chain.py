#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
citation_chain.py  ―  codex-refs モード C（芋づる）用の決定論的ツール

キーワード検索（モード A/B）では届かない文献に、引用と著者でたどって届く。
発想は imozuru（MaryCache/imozuru）と SR の citation searching / snowballing。
ただし出口は必ず PMID に揃え、書誌は pubmed_search.py の efetch で実取得する
（PMID を記憶・推測で書かない、という codex-refs の核心ルールを崩さない）。

サブコマンド:
  refs    <PMID>   後ろへ: その論文の参考文献
  cites   <PMID>   前へ  : その論文を引いている論文（被引用の多い順）
  similar <PMID>   近くを: PubMed の Similar articles（出て間もなく引用が 0 件のとき用）
  author  "<著者>" 著者で: PubMed の著者検索（--seed PMID で同名別人を減らす）
  fulltext <PMID> --dir <保存先>
                   本文の参考文献で後ろへ: PMC の XML → 無ければ OA の PDF を pdftotext。
                   本文テキストを行番号つきで保存し、参考文献を PMID に戻す。
                   PMID に戻せなかった参考文献は「未解決」として表示（推測で埋めない）。

引用データの出どころ（新しい論文ほど抜けやすいので複数を合わせる）:
  I = NIH iCite / P = PubMed elink / E = Europe PMC / O = OpenAlex / F = 本文(PMC XML・PDF)

共通オプション:
  --known a.csv,b.csv   既に持っている CSV（PubMed_ID / pmid / PMID / pmids 列）。各行に 既知/新規 を付け、
                        末尾に「新規 n / 既知 m（既知率 x%）」を出す → 止めどきの判定材料
  --types rct,sr,ma     論文種別で絞る（rct, sr, ma, review, trial, guideline）
  --since 2020          この年以降だけ
  --top 40              表示件数（既定 40。全件は --tsv に保存）
  --tsv out.tsv         全件を TSV 保存（search_log の添付用）

例:
  python citation_chain.py refs 36425870 --types rct --known refs_20261020.csv
  python citation_chain.py cites 36425870 --since 2022 --types rct,sr,ma --tsv c_36425870.tsv
  python citation_chain.py fulltext 39111059 --dir sources

終了コード: 0=成功 / 3=0 件 / 1=エラー
次の一手: 候補から選んだ PMID を pubmed_search.py --keep ... --quote ... で確定する。
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pubmed_search as ps  # efetch_many / esearch / NCBI 用 _http_get（stdout の UTF-8 化もここで済む）

ELINK = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
ICITE = "https://icite.od.nih.gov/api/pubs"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
OPENALEX = "https://api.openalex.org"

TYPE_MAP = {
    "rct": {"Randomized Controlled Trial"},
    "sr": {"Systematic Review"},
    "ma": {"Meta-Analysis"},
    "review": {"Review", "Systematic Review"},
    "trial": {"Clinical Trial", "Randomized Controlled Trial", "Controlled Clinical Trial",
              "Clinical Trial, Phase II", "Clinical Trial, Phase III", "Clinical Trial, Phase IV"},
    "guideline": {"Guideline", "Practice Guideline"},
}
TYPE_SHORT = [("Randomized Controlled Trial", "RCT"), ("Meta-Analysis", "MA"),
              ("Systematic Review", "SR"), ("Practice Guideline", "GL"), ("Review", "Rev"),
              ("Clinical Trial", "CT"), ("Observational Study", "Obs")]


# ============================================================
# NCBI 以外への GET（NCBI の API キーを他所へ送らないよう ps._http_get とは分ける）
# ============================================================
def get_json(url, params=None, timeout=60, max_retry=3):
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    backoff = 1.0
    for attempt in range(max_retry + 1):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": ps.USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retry:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt < max_retry:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise


def _warn(src, e):
    sys.stderr.write(f"  [{src}] 取得できず（他の出どころで続行）: {e}\n")


# ============================================================
# 出どころ別: PMID → 関連 PMID の集合
# ============================================================
def elink(pmid, linkname):
    """PubMed elink。refs/citedin は PMC 収載分に偏るので単独では使わない。"""
    data = json.loads(ps._http_get(ELINK, {"dbfrom": "pubmed", "db": "pubmed", "id": pmid,
                                           "linkname": linkname, "retmode": "json"}))
    out = []
    for ls in data.get("linksets", []):
        for db in ls.get("linksetdbs", []) or []:
            if db.get("linkname") == linkname:
                out += [str(x) for x in db.get("links", [])]
    return out


def icite(pmids, fields="pmid,references,cited_by,citation_count,year"):
    """NIH iCite。PMID で参考文献・被引用・被引用数を返す（キー不要）。"""
    out = {}
    pmids = list(dict.fromkeys(pmids))
    for i in range(0, len(pmids), 500):
        data = get_json(ICITE, {"pmids": ",".join(pmids[i:i + 500]), "fl": fields}) or {}
        for d in data.get("data", []):
            out[str(d.get("pmid"))] = d
    return out


def epmc_list(pmid, kind):
    """Europe PMC の references / citations。PMID の無い参考文献は書誌のまま返す。"""
    key = {"references": ("referenceList", "reference"),
           "citations": ("citationList", "citation")}[kind]
    pmids, unresolved, page = [], [], 1
    while True:
        data = get_json(f"{EPMC}/MED/{pmid}/{kind}",
                        {"format": "json", "page": page, "pageSize": 1000}) or {}
        items = (data.get(key[0]) or {}).get(key[1]) or []
        for it in items:
            if it.get("source") == "MED" and str(it.get("id", "")).isdigit():
                pmids.append(str(it["id"]))
            elif kind == "references":
                unresolved.append(" ".join(str(it.get(k, "")) for k in
                                           ("authorString", "pubYear", "title", "journalAbbreviation")
                                           if it.get(k)))
        hit = int(data.get("hitCount") or 0)
        if page * 1000 >= hit or not items:
            break
        page += 1
    return pmids, unresolved


def _oa_pmids(works):
    return [w["ids"]["pmid"].rstrip("/").split("/")[-1]
            for w in works if (w.get("ids") or {}).get("pmid")]


def openalex_work(pmid):
    return get_json(f"{OPENALEX}/works/pmid:{pmid}",
                    {"select": "id,ids,referenced_works,cited_by_count,best_oa_location,open_access"})


def openalex_refs(work):
    ids = [w.rsplit("/", 1)[-1] for w in (work or {}).get("referenced_works", [])]
    out = []
    for i in range(0, len(ids), 50):
        data = get_json(f"{OPENALEX}/works", {"filter": "openalex_id:" + "|".join(ids[i:i + 50]),
                                              "select": "ids", "per-page": 50}) or {}
        out += _oa_pmids(data.get("results", []))
    return out


def openalex_cites(work, limit=1000):
    wid = (work or {}).get("id", "").rsplit("/", 1)[-1]
    if not wid:
        return []
    out, cursor = [], "*"
    while cursor and len(out) < limit:
        data = get_json(f"{OPENALEX}/works", {"filter": f"cites:{wid}", "select": "ids",
                                              "per-page": 200, "cursor": cursor}) or {}
        out += _oa_pmids(data.get("results", []))
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not data.get("results"):
            break
    return out


# ============================================================
# 本文から参考文献（PMC XML → OA PDF）
# ============================================================
def pmc_id(pmid):
    links = elink_pmc(pmid)
    return links[0] if links else None


def elink_pmc(pmid):
    data = json.loads(ps._http_get(ELINK, {"dbfrom": "pubmed", "db": "pmc", "id": pmid,
                                           "linkname": "pubmed_pmc", "retmode": "json"}))
    out = []
    for ls in data.get("linksets", []):
        for db in ls.get("linksetdbs", []) or []:
            out += [str(x) for x in db.get("links", [])]
    return out


def fulltext_pmc(pmcid, outdir, pmid):
    xml = ps._http_get(ps.EFETCH, {"db": "pmc", "id": pmcid, "retmode": "xml"}, timeout=120)
    root = ET.fromstring(xml)
    body = root.find(".//body")
    if body is None:
        return None  # 出版社の制限で本文 XML が出ないことがある
    paras = [" ".join("".join(p.itertext()).split()) for p in body.iter() if p.tag in ("title", "p")]
    path = os.path.join(outdir, f"PMID{pmid}_PMC{pmcid}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(x for x in paras if x) + "\n")
    pmids, dois, unresolved = [], [], []
    for ref in root.iter("ref"):
        pm = ref.find(".//pub-id[@pub-id-type='pmid']")
        do = ref.find(".//pub-id[@pub-id-type='doi']")
        if pm is not None and (pm.text or "").strip().isdigit():
            pmids.append(pm.text.strip())
        elif do is not None and do.text:
            dois.append(do.text.strip())
        else:
            unresolved.append(" ".join(t.strip() for t in ref.itertext() if t.strip())[:200])
    return path, pmids, dois, unresolved


DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.I)


def fulltext_pdf(url, outdir, pmid):
    """OA の PDF を落として pdftotext。参考文献の節から DOI を拾い、残りは未解決として返す。"""
    req = urllib.request.Request(url, headers={"User-Agent": ps.USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if not data.startswith(b"%PDF"):
        raise RuntimeError("PDF ではない応答（ログイン画面やボット対策の可能性）")
    pdf = os.path.join(outdir, f"PMID{pmid}.pdf")
    with open(pdf, "wb") as f:
        f.write(data)
    txt = pdf[:-4] + ".txt"
    subprocess.run(["pdftotext", "-enc", "UTF-8", pdf, txt], check=True, timeout=120)
    with open(txt, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    start = 0
    for i, ln in enumerate(lines):
        if re.fullmatch(r"\s*(references|reference list|bibliography|literature cited|文献|引用文献)\s*",
                        ln, re.I):
            start = i  # 最後の見出しを採る（本文中の "References" 語に引っかからないよう）
    sect = "\n".join(lines[start:]) if start else ""
    dois = list(dict.fromkeys(d.rstrip(".,;)]") for d in DOI_RE.findall(sect)))
    chunks = [c.strip() for c in re.split(r"\n\s*(?:\[?\d{1,3}[\].]|\d{1,3}\s)", sect) if len(c.strip()) > 30]
    unresolved = [" ".join(c.split())[:200] for c in chunks if not DOI_RE.search(c)]
    if not start:
        unresolved = ["（参考文献の見出しが見つからない。本文 .txt を grep して手で確認）"]
    return txt, [], dois, unresolved


def dois_to_pmids(dois):
    out = []
    for i in range(0, len(dois), 50):
        term = " OR ".join(f'"{d}"[doi]' for d in dois[i:i + 50])
        _, ids = ps.esearch(term, retmax=200)
        out += ids
    return out


# ============================================================
# 集約・表示
# ============================================================
def load_known(spec):
    known = set()
    for path in [p for p in (spec or "").split(",") if p.strip()]:
        with open(path.strip(), encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                for col in ("PubMed_ID", "pmid", "PMID", "pmids"):
                    known.update(re.findall(r"\d{5,9}", r.get(col) or ""))
    return known


def short_types(ptypes):
    s = [lab for full, lab in TYPE_SHORT if full in ptypes]
    return "/".join(s[:2]) or "—"


def report(seed_desc, found, args, unresolved=()):
    """found: {pmid: set(出どころ記号)}"""
    known = load_known(args.known)
    recs = ps.efetch_many(list(found))
    cnt = {p: d.get("citation_count") or 0 for p, d in icite(list(recs), "pmid,citation_count").items()} \
        if recs else {}
    want = set().union(*(TYPE_MAP[t] for t in args.types.split(","))) if args.types else None
    rows = []
    for pmid, rec in recs.items():
        if want and not (want & set(rec["_pubtypes"])):
            continue
        y = rec.get("Year", "")
        if args.since and not (y[:4].isdigit() and int(y[:4]) >= args.since):
            continue
        rows.append({"pmid": pmid, "year": y, "type": short_types(rec["_pubtypes"]),
                     "cited": cnt.get(pmid, 0), "src": "".join(sorted(found[pmid])),
                     "status": "既知" if pmid in known else "新規",
                     "first": (rec.get("Author", "").split(";")[0] or "—").strip(),
                     "title": rec.get("Title", "")})
    rows.sort(key=lambda r: (r["status"] != "新規", -int(r["cited"])))
    print(f"\n=== {seed_desc}: 関連 {len(found)} 件 → 条件後 {len(rows)} 件（新規を先、被引用の多い順）===")
    print("  PMID      | 年   | 種別   | 被引用 | 出どころ | 状態 | 筆頭 | 題名")
    for r in rows[:args.top]:
        print(f"  {r['pmid']:>9} | {r['year'][:4]:4} | {r['type']:6} | {r['cited']:>6} | "
              f"{r['src']:8} | {r['status']} | {r['first']} | {r['title'][:110]}")
    if len(rows) > args.top:
        print(f"  …ほか {len(rows) - args.top} 件（--top で増やす / --tsv で全件保存）")
    if known:
        k = sum(r["status"] == "既知" for r in rows)
        print(f"\n[既知率] 条件後 {len(rows)} 件中 既知 {k}・新規 {len(rows) - k}"
              f"（既知率 {100 * k / len(rows):.0f}%）" if rows else "\n[既知率] 条件後 0 件")
    lost = len(found) - len(recs)
    if lost:
        print(f"[note] PMID はあるが efetch で書誌が取れなかった {lost} 件は除外")
    if unresolved:
        print(f"\n[未解決] PMID に戻せなかった参考文献 {len(unresolved)} 件"
              f"（必要なものだけ pubmed_search.py --peek で題名検索して確かめる。推測で PMID を書かない）:")
        for u in list(unresolved)[:args.top]:
            print("   - " + u)
    if args.tsv:
        with open(args.tsv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["pmid"], delimiter="\t")
            w.writeheader()
            w.writerows(rows)
        print(f"[tsv] {args.tsv} に {len(rows)} 件保存")
    print("\n[次] 選んだ PMID は pubmed_search.py --keep <PMID> --note \"PMID=理由\" "
          "--quote \"PMID=抄録の一文\" --out <csv> で確定。")
    return 0 if rows else 3


def collect(pmid, direction):
    found, srcs_ok = {}, []

    def add(src, ids):
        for p in ids:
            if p != pmid:
                found.setdefault(p, set()).add(src)
        srcs_ok.append(f"{src}={len(set(ids))}")

    unresolved = []
    try:
        d = icite([pmid]).get(pmid, {})
        add("I", [str(x) for x in (d.get("references") if direction == "refs" else d.get("cited_by")) or []])
    except Exception as e:
        _warn("iCite", e)
    try:
        add("P", elink(pmid, "pubmed_pubmed_refs" if direction == "refs" else "pubmed_pubmed_citedin"))
    except Exception as e:
        _warn("elink", e)
    try:
        ids, unresolved = epmc_list(pmid, "references" if direction == "refs" else "citations")
        add("E", ids)
    except Exception as e:
        _warn("EuropePMC", e)
    try:
        w = openalex_work(pmid)
        add("O", openalex_refs(w) if direction == "refs" else openalex_cites(w))
    except Exception as e:
        _warn("OpenAlex", e)
    sys.stderr.write(f"[{direction}] PMID {pmid}: " + ", ".join(srcs_ok) + "\n")
    if not found:
        sys.stderr.write("  0 件。出て1年ほどの論文は引用の登録が追いついていない（0 件 ≠ 無い）。"
                         "refs なら fulltext、cites なら similar か題名でのキーワード検索へ。\n")
    return found, unresolved


def main():
    ap = argparse.ArgumentParser(description="codex-refs モード C: 芋づる式の文献たどり")
    ap.add_argument("cmd", choices=["refs", "cites", "similar", "author", "fulltext"])
    ap.add_argument("target", help="PMID（author のときは著者名 例 \"Morin CM\"）")
    ap.add_argument("--seed", help="author: この PMID の共著者・所属で同名別人を減らす")
    ap.add_argument("--dir", default="sources", help="fulltext: 本文の保存先")
    ap.add_argument("--known")
    ap.add_argument("--types")
    ap.add_argument("--since", type=int)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--tsv")
    args = ap.parse_args()
    if args.types and not set(args.types.split(",")) <= set(TYPE_MAP):
        ap.error(f"--types は {', '.join(TYPE_MAP)} から")

    try:
        if args.cmd in ("refs", "cites"):
            found, unresolved = collect(args.target, args.cmd)
            label = "参考文献（後ろへ）" if args.cmd == "refs" else "被引用（前へ）"
            sys.exit(report(f"PMID {args.target} の{label}", found, args, unresolved))

        if args.cmd == "similar":
            found = {p: {"P"} for p in elink(args.target, "pubmed_pubmed")[:200] if p != args.target}
            sys.exit(report(f"PMID {args.target} の類似論文（PubMed Similar articles 上位 200）", found, args))

        if args.cmd == "author":
            term = f"{args.target}[au]"
            if args.seed:
                rec = ps.efetch([args.seed]).get(args.seed, {})
                authors = [a for a in rec.get("Author", "").split("; ") if a]
                if args.target not in authors:
                    sys.stderr.write(f"[author] 警告: {args.target} は PMID {args.seed} の著者に無い"
                                     f"（著者表記: {', '.join(authors[:6])} …）。--seed を外すか表記を合わせる。\n")
                co = [a for a in authors if a != args.target][:3]
                if co:
                    term += " AND (" + " OR ".join(f'"{a}"[au]' for a in co) + f" OR {args.seed}[pmid])"
                    sys.stderr.write(f"[author] 共著者で絞り込み: {term}\n")
            cnt, ids = ps.esearch(term, retmax=200, sort="pub_date")
            sys.stderr.write(f"[author] ヒット {cnt} 件（新しい順に最大 200）\n")
            sys.exit(report(f"著者 {args.target}", {p: {"P"} for p in ids}, args))

        if args.cmd == "fulltext":
            os.makedirs(args.dir, exist_ok=True)
            res, how = None, ""
            pmc = pmc_id(args.target)
            if pmc:
                res, how = fulltext_pmc(pmc, args.dir, args.target), f"PMC{pmc} の XML"
            if not res:
                w = openalex_work(args.target) or {}
                url = ((w.get("best_oa_location") or {}).get("pdf_url")
                       or (w.get("open_access") or {}).get("oa_url"))
                if not url:
                    print("[fulltext] 無料で読める本文が見つからない（有料の可能性）。"
                          "必要ならユーザーにブラウザで PDF を取ってもらう。")
                    sys.exit(3)
                try:
                    res, how = fulltext_pdf(url, args.dir, args.target), f"OA PDF {url}"
                except Exception as e:
                    print(f"[fulltext] PDF 取得失敗: {e}\n  URL: {url}\n  → ユーザーにブラウザで開いてもらう")
                    sys.exit(3)
            path, pmids, dois, unresolved = res
            pmids += dois_to_pmids(dois) if dois else []
            print(f"[fulltext] {how} → 本文 {path}（grep -n で行番号を確かめて引用に使える）")
            sys.exit(report(f"PMID {args.target} の本文の参考文献", {p: {"F"} for p in pmids}, args, unresolved))
    except Exception as e:
        print(f"[error] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
