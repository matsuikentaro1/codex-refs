---
name: codex-refs
description: Search academic references for a manuscript and export them to a verified refs CSV, then optionally hand off to cec-sheet. Uses Codex CLI as the search driver, but Codex calls a bundled deterministic PubMed tool (scripts/pubmed_search.py) instead of writing its own E-utilities code — bibliography is fetched live from PubMed so PMIDs are never fabricated. Triggers: "論文検索", "文献検索", "文脈検索", "コンテキスト検索", "原稿の引用を探して", "refs.csv", "codex-refs", "/codex-refs".
---

# Codex Ref Finder (v2 — bundled PubMed tool)

## これは何か

原稿の主張(claim)を裏付ける学術文献を検索し、**検証済みの書誌つき CSV (`refs.csv`)** を作るスキル。
検索の駆動は従来どおり **Codex CLI** に任せるが、**Codex には PubMed を叩く Python を書かせない**。
代わりにバンドル済みの決定論的ツール `scripts/pubmed_search.py` を呼ばせる。

### v1 からの変更点（なぜ変えたか）

v1 は「PubMed E-utilities を叩く Python を Codex に毎回ゼロから書かせる」設計で、以下の壁に繰り返しぶつかってトークンを浪費していた:

| 壁 | v2 での解消 |
|---|---|
| PowerShell の here-string / パイプ実行の構文崩壊 | スクリプトを呼ぶだけ。引数は単純文字列のみ |
| パスのスペース・日本語で `is not recognized` | 配管をスクリプトに固定。`--cd` で作業 dir へ |
| NCBI 429 レート制限・タイムアウト | スクリプトが API キー＋指数バックオフ＋件数事前確認 |
| XML パース失敗・書誌の取りこぼし | テスト済みの efetch 解析を流用 |
| **PMID 捏造**（記憶で書いて別論文を挿入する重大事故） | PMID は esearch/efetch の実取得のみ。Codex は書かない |
| 毎回スクリプトを書き直す reasoning コスト | ゼロ（呼ぶだけ） |
| **stdin 無限ハング**（`Reading additional input from stdin...` で固まる） | 起動コマンドに **`< /dev/null` 必須**（モード B 参照） |
| **過剰反復**（定番文献で 20+ 回検索し収束せず焼ける） | **定番はモード A で狙い撃ち**／探索は「3〜4 回で収束」と明示 |
| `--full-auto` 非推奨警告 | フラグから外す（`--sandbox danger-full-access` のみ） |

**核心ルール（3つ）:**
1. **モードを分ける。** 定番・既知文献は Claude が狙い撃ち（モード A）。探索的な問いだけ Codex に agentic に回す（モード B）。定番を agentic に流すと過剰反復で焼ける。
2. **Codex は agentic な検索者のまま（モード B）。** トピックを渡せば Codex 自身がクエリを設計・反復・選別する。ただし「3〜4 回で収束・完璧を追うな」でしつける。
3. **Codex に PubMed E-utilities のコードを書かせない。** 検索の"計器"は必ず `pubmed_search.py`。agentic 反復をしても plumbing で flailing しない。

---

## バンドルツール `scripts/pubmed_search.py`

絶対パス（Codex に渡すときは**必ずダブルクオートで囲む**。スペースを含む）:

Claude Code がこのスキルを `~/.claude/skills/codex-refs/` にインストールした場合、
パスは自動で解決される。手動で指定する場合は `<SKILL_DIR>` を実際のパスに置き換える:
```
"<SKILL_DIR>\scripts\pubmed_search.py"
```

使い方:
```bash
# 候補を覗く（CSV 書込なし。stdout に relevance 上位の一覧）
python "<上記パス>" --query "migraine global burden GBD prevalence disability" --retmax 6 --peek

# 選んだ PMID を確定して一時 CSV に追記（書誌は実取得・捏造ゼロ）
python "<上記パス>" --keep 38493795,28919117 \
    --note "38493795=GBD2021 神経疾患の世界負荷の基準; 28919117=GBD2016 有病率/YLDの定番" \
    --out _tmp_refs_01.csv

# ワンショット（query の relevance 上位 retmax 件をそのまま書込）
python "<上記パス>" --query "..." --out _tmp_refs_01.csv --note "テーマの一言"

# 接続・APIキーの自己診断
python "<上記パス>" --selftest
```

- 終了コード: `0`=成功 / `3`=ヒット0件（→検索語を広げる）/ `4`=接続・キー異常 / `1`=その他。
- CSV 列: `PubMed_ID, Author, Year, Title, Journal, Volume, Issue, Pages, doi, abstract, whats_interesting1..5`。
- DOI/PMID 重複は自動排除。既存 PMID には次の空き `whats_interesting` 列へ理由を追記。

### API キー（任意・強く推奨）

`.secrets/ncbi_api_key.txt` にキー1行を置くと 3→~9 req/s になり 429 がほぼ消える(`.secrets/` は `.gitignore` 済み、共有・公開しても漏れない)。無くても動作する。
環境変数 `NCBI_API_KEY` でも可（こちらが優先）。取得は無料・即時（NCBI account → API Key Management）。

---

## ワークフロー

### 0. まずモードを見極める（最重要）

文献を 2 種類に分け、**取りに行き方を変える**。実測で、定番文献を agentic 探索させると codex が「完璧な言い回し」を追って 20 回以上反復し収束せず大量トークンを焼く一方、狙い撃ちは一発で当たる。

| 種類 | 例 | モード | 担当 |
|---|---|---|---|
| **定番・既知**（欲しい物が分かっている） | GBD 頭痛負荷 / Felitti オリジナル ACE / Hughes メタ / HIT-6・K6・ACE 尺度の妥当性 / Hazumi 2025 | **A. 狙い撃ち** | **Claude 本体**が計器を直接叩く（codex 不要・低トークン） |
| **探索的**（どの論文かは未知） | 「X と Y の関連を縦断で見た論文」「この知見の最新の観察研究」 | **B. agentic 探索** | **Codex** に自由に検索させる |

### モード A: 狙い撃ち取得（Claude が計器を直接叩く）

定番文献は反復不要。Claude が `pubmed_search.py` を直接呼ぶ:
```bash
# 候補を見る（abstract つき）
python "<SKILL_DIR>\scripts\pubmed_search.py" --query "<狙ったクエリ>" --retmax 6 --peek
# 確定
python "<SKILL_DIR>\scripts\pubmed_search.py" --keep <PMID,...> --note "PMID=理由" --out refs_YYYYMMDD.csv
```
これは codex を使わないので**最速・最小トークン**。定番はこれで十分。

### モード B: agentic 探索（Codex に委ねる）

探索的な問いだけ Codex に回す。**手順は縛らないが、収束はしつける**（さもないと過剰反復で焼ける）。

**0-1. Codex 認証プリフライト**（起動前に必ず。v1 は失効に気づかず全滅した）:
```bash
codex exec --model gpt-5.4-mini --sandbox danger-full-access --skip-git-repo-check --cd "<project>" "PRINT_OK とだけ出力して終了して。" < /dev/null
```
401 / `token_invalidated` / `refresh_token_reused` が出たら `! codex login` を依頼し、成功後に起動。

**0-2. Codex 起動コマンドの定型**（★ 過去に踏んだ罠を回避）:
```bash
timeout 220 codex exec --model gpt-5.4-mini --sandbox danger-full-access --skip-git-repo-check \
  --cd "<project>" "<下のプロンプト>" < /dev/null 2>&1
```
- **`< /dev/null` 必須**: 付けないと codex が「Reading additional input from stdin...」で**無限ハング**する（特に run_in_background 時）。
- **`--full-auto` は使わない**（非推奨。`--sandbox danger-full-access` だけでよい）。
- **`| tail` でパイプしない**: 終了まで出力が見えずライブ監視不能になる。出力はファイルへ。
- **`timeout` を付ける**: 過剰反復の保険。
- 1 トピック = 1 Codex、各自 `_tmp_refs_NN.csv` のみに書く（共有 CSV 並列書込はデータ消失）。

**0-3. プロンプト雛形**（agentic な裁量 + **収束のしつけ**）:
```
あなたは PubMed 検索エージェントです。下のトピックに最適な引用文献を見つけて確定してください。

## トピック（裏付けたい主張）
「<自然文の主張・トピック>」

## 使える唯一の検索計器（PubMed を叩くコードは自分で書かない）
  python "<SKILL_DIR>\scripts\pubmed_search.py" --query "<任意>" --peek
  python "<SKILL_DIR>\scripts\pubmed_search.py" --keep <PMID,...> --note "PMID=理由" --out _tmp_refs_<NN>.csv

## 進め方（裁量で反復するが、必ず収束させる）
- まず広めのクエリで --peek。abstract を読んで relevance を判断。
- ずれていれば同義語・MeSH・AND/OR・年指定を変えて再 --peek。
- ★ 収束ルール: **検索は多くても 3〜4 回まで**。完璧な言い回しを追わない。
  代表的で妥当な 1〜数件が見つかったら、それ以上探さず即 --keep で確定する。
- 良い候補が無い／0件(exit 3)が続くなら、最善の候補を確定して理由に「代表的」と記す。
保存先 _tmp_refs_<NN>.csv のみ。確定したら PMID と理由を 1 行で報告。
```

> 設計の要点: **探索戦略は Codex に委ねて間口を保ちつつ、「3〜4 回で収束・完璧を追うな」で過剰反復を防ぐ**。
> 定番をここに流さない（モード A で狙い撃つ）。

### 3. マージ（Claude 本体が順次実行）

全バッチ完了後、`_tmp_refs_*.csv` をメイン `refs_YYYYMMDD.csv` に DOI 重複チェックしながら統合し、一時ファイルを削除する。下のテンプレート参照。

### 4. PMID 空欄チェック（★ハルシネーション最終防壁）

マージ後、メイン CSV の `PubMed_ID` 列に空欄が無いか検査。空欄があれば検索/マージ漏れなので補完してから次工程へ。
（`pubmed_search.py` 経由なら PMID は必ず実在するが、手動編集が混じった場合に備える。）

---

## 文献選定の品質ルール

`pubmed_search.py` が書誌の正確性を保証するので、Codex/Claude は **意味的な選別**に集中する:

- 件数を指定しない。「代表的・定番」で品質を指定する（枠埋めの粗悪論文を避ける）。
- エビデンスレベルは文脈から判断: 広く認知された事実→メタアナリシス/システマティックレビュー、治療効果→RCT、稀な知見→観察研究も可。
- **abstract のメインテーマが主張と合致するか**を必ず確認。キーワードがかすっているだけの論文は除外。
- `whats_interesting1` に「なぜこの論文を選んだか」を必ず書かせる（選定理由＝後の検証材料）。

---

## マージ処理テンプレート（Claude 本体が実行）

```python
import csv, glob, os
MAIN_CSV = 'refs_YYYYMMDD.csv'
TMP_PATTERN = '_tmp_refs_*.csv'
FIELDNAMES = ['PubMed_ID','Author','Year','Title','Journal','Volume','Issue','Pages','doi','abstract',
              'whats_interesting1','whats_interesting2','whats_interesting3','whats_interesting4','whats_interesting5']
WI = FIELDNAMES[10:]
rows, doi_index, pmid_index = [], {}, {}
if os.path.exists(MAIN_CSV):
    with open(MAIN_CSV, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            rows.append(r)
            if r.get('doi'): doi_index[r['doi'].lower()] = len(rows)-1
            if r.get('PubMed_ID'): pmid_index[r['PubMed_ID']] = len(rows)-1
for tmp in sorted(glob.glob(TMP_PATTERN)):
    with open(tmp, encoding='utf-8-sig') as f:
        for nr in csv.DictReader(f):
            doi = (nr.get('doi') or '').lower(); pmid = nr.get('PubMed_ID','')
            idx = doi_index.get(doi) if doi else None
            if idx is None and pmid: idx = pmid_index.get(pmid)
            if idx is not None:
                ex = rows[idx]
                for c in WI:
                    if not ex.get(c):
                        ex[c] = nr.get('whats_interesting1',''); break
            else:
                rows.append(nr)
                if doi: doi_index[doi] = len(rows)-1
                if pmid: pmid_index[pmid] = len(rows)-1
with open(MAIN_CSV, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=FIELDNAMES); w.writeheader()
    for r in rows: w.writerow({k: r.get(k,'') for k in FIELDNAMES})
for tmp in glob.glob(TMP_PATTERN): os.remove(tmp)
empty = [r for r in rows if not (r.get('PubMed_ID') or '').strip()]
print(f'Total {len(rows)} entries. Empty PMID: {len(empty)}')
```

---

## CEC 表が欲しいとき

本スキルの責務は **refs.csv の作成まで**。主張↔根拠↔引用の対応表は **`cec-sheet` スキル**に渡す
（PubMed efetch から書誌を再取得して HTML 表を生成し、捏造を防ぐ）。

---

## 直書き禁止の徹底

- API キーを SKILL.md・スクリプト・CSV・ドキュメントに直書きしない。必ず `.secrets/`（gitignore 済）か環境変数。
- PMID を記憶・推測で書かない。必ず `pubmed_search.py` の出力から取る。
