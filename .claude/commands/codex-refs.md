---
name: codex-refs
description: Search academic references via Codex CLI, export to CSV, and generate CEC sheet (Claim-Evidence-Citation). Triggers: "論文検索", "文献検索", "文脈検索", "コンテキスト検索", "原稿の引用を探して", "CEC", "codex-refs", "/codex-refs"
---
# Codex Ref Finder
Codex CLIを使用して学術文献を検索し、結果をCSVファイルに保存するスキル。PubMedを主な検索ソースとするが、PubMed未掲載の文献（書籍、ガイドライン、プレプリント等）も対象とする。

## 実行コマンド
```bash
codex exec --model gpt-5.4 --full-auto --sandbox danger-full-access --skip-git-repo-check --cd <project_directory> "<request>"
```

### コマンドオプションについて
- `--sandbox danger-full-access`: ファイル書き込みとネットワークアクセスの両方が必要なため使用（他の選択肢: `read-only`, `workspace-write`）
- `--skip-git-repo-check`: Gitリポジトリ外のディレクトリでも実行可能にするため使用

## 検索リクエストの形式

```
「<検索キーワード>」に関する論文をPubMedから検索して、CSVファイルに保存してください。

保存先: <ファイルパス>
保存方法: 既存のCSVファイルがある場合は行を追加して追記、ない場合は新規作成

CSV列: PubMed_ID, Author, Year, Title, Journal, Volume, Issue, Pages, doi, abstract, whats_interesting1, whats_interesting2, whats_interesting3, whats_interesting4, whats_interesting5
- PubMed_IDはPMID（PubMed識別子）。PubMed未掲載の文献はエムダッシュ（—）を入れる
- whats_interesting1〜whats_interesting5は検索文脈に関連した主要な知見を記載するための列
- 新規論文の場合はwhats_interesting1に記載
- 既存の論文（doiで照合）に追記する場合は、次の空きwhats_interesting列に記載

重要な実装指示:
- Pythonスクリプトを一時ファイル（例: _tmp_search.py）として書き出してからpythonで実行すること
- PowerShellのhere-string（@'...'@）やパイプ経由のPython実行は禁止（エスケープエラーの原因）
- abstractやwhats_interesting内のカンマ・引用符はcsv.DictWriterが自動処理するので手動エスケープ不要
- 既存CSVに追記する場合、まず既存データを読み込み、doiで重複チェックを行うこと
- 重複論文が見つかった場合は行を追加せず、既存行の次の空きwhats_interesting列に新しい知見を追記すること
```

## 並列実行時のルール（重要）

複数のcodex検索を並列実行する場合、同じCSVファイルに同時に書き込むとレースコンディション（競合状態）が発生し、データが消失する。これを防ぐため、以下のルールに従う。

### 並列実行の流れ

1. **各codex検索は個別の一時CSVに書き出す**
   - ファイル名: `_tmp_refs_<連番またはテーマ>.csv`（例: `_tmp_refs_01.csv`, `_tmp_refs_circadian.csv`）
   - 各codexプロセスは自分専用の一時CSVのみに書き込む
   - 既存のメインCSVには直接書き込まない

2. **全codex検索が完了した後に、メインプロセスでマージする**
   - メインCSV（例: `refs_YYYYMMDD.csv`）を読み込む
   - 各一時CSVを順番に読み込む
   - DOIで重複チェック → 新規論文は行追加、既存論文はwhats_interesting列に追記
   - マージ完了後、一時CSVファイルを削除する

3. **マージ処理はClaude Code本体（親プロセス）が行う**
   - codex（子プロセス）にはマージさせない
   - マージ処理はPythonスクリプトとして実装し、順次処理する

### 並列実行時の検索リクエスト形式

```
「<検索キーワード>」に関する論文をPubMedから検索して、CSVファイルに保存してください。

保存先: _tmp_refs_<連番>.csv（新規作成）
注意: 既存のメインCSVには書き込まないこと。一時ファイルに新規作成のみ。

CSV列: PubMed_ID, Author, Year, Title, Journal, Volume, Issue, Pages, doi, abstract, whats_interesting1, whats_interesting2, whats_interesting3, whats_interesting4, whats_interesting5
- whats_interesting1に検索文脈に関連した知見を記載

重要: Pythonスクリプトを一時ファイルとして書き出してから実行すること。
```

### マージ処理のPythonテンプレート

```python
import csv
import glob
import os

MAIN_CSV = 'refs_YYYYMMDD.csv'
TMP_PATTERN = '_tmp_refs_*.csv'
FIELDNAMES = ['PubMed_ID', 'Author', 'Year', 'Title', 'Journal', 'Volume', 'Issue', 'Pages', 'doi', 'abstract', 'whats_interesting1', 'whats_interesting2', 'whats_interesting3', 'whats_interesting4', 'whats_interesting5']
WI_COLS = ['whats_interesting1', 'whats_interesting2', 'whats_interesting3', 'whats_interesting4', 'whats_interesting5']

# 1. メインCSV読み込み
rows = []
doi_index = {}
if os.path.exists(MAIN_CSV):
    with open(MAIN_CSV, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            if row.get('doi'):
                doi_index[row['doi']] = len(rows) - 1

# 2. 各一時CSVをマージ
tmp_files = sorted(glob.glob(TMP_PATTERN))
for tmp_file in tmp_files:
    with open(tmp_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for new_row in reader:
            doi = new_row.get('doi', '')
            if doi and doi in doi_index:
                # 既存論文 → 次の空きwhats_interesting列に追記
                existing = rows[doi_index[doi]]
                for col in WI_COLS:
                    if not existing.get(col):
                        existing[col] = new_row.get('whats_interesting1', '')
                        break
            else:
                # 新規論文 → 行追加
                rows.append(new_row)
                if doi:
                    doi_index[doi] = len(rows) - 1

# 3. メインCSVに書き出し
with open(MAIN_CSV, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, '') for k in FIELDNAMES})

# 4. 一時ファイル削除
for tmp_file in tmp_files:
    os.remove(tmp_file)

print(f'Merged {len(tmp_files)} temp files. Total entries: {len(rows)}')

# 5. PubMed_ID空欄チェック（★ハルシネーション防止）
empty_pmid_rows = []
for i, row in enumerate(rows):
    pmid = row.get('PubMed_ID', '').strip()
    if not pmid:
        empty_pmid_rows.append(f"  Row {i+2}: {row.get('Author','')} {row.get('Year','')} - {row.get('Title','')[:50]}")
if empty_pmid_rows:
    print(f'\n⚠️ WARNING: {len(empty_pmid_rows)} rows have empty PubMed_ID:')
    for line in empty_pmid_rows:
        print(line)
    print('→ PubMed APIまたはcodex-refsで補完してから次の工程に進むこと')
else:
    print('✓ All rows have PubMed_ID (or em-dash for non-PubMed items)')
```

## コンテキスト認識検索モード

原稿テキストに含まれる引用マーカーの前後の文脈を分析し、各引用に最適な論文を検索するモード。キーワードだけでなく「なぜその引用が必要か」を理解してから検索するため、的中率が高い。

### 実行手順

1. ユーザーから原稿テキスト（引用マーカー付き）とCSVファイル名を受け取る
2. **Claude Codeがコンテキスト分析を行う**（codex呼び出し前）:
   a. テキスト中の引用マーカーを検出する
      - 対応パターン: `<sup>N)</sup>`, `[要引用]`, `[要引用N]`, `[ref]`, `[citation needed]`, `N)` など
   b. 各引用マーカーの**直前テキスト**から以下を抽出・推論する:
      - **原文の主張**: どのような事実・知見を述べているか
      - **必要なエビデンスの種類**: RCT、メタアナリシス、コホート研究、レビュー、基礎研究等
      - **検索キーワード（英語）**: PubMed検索に適した英語キーワード
      - **whats_interesting記載方針**: CSVのwhats_interesting列に何を書くべきか
   c. 検索インテントを構造化して出力する（下記テンプレート参照）
3. 検索インテントに基づいてcodexを実行する
   - **常に並列実行（1引用 = 1 Codexプロセス）**
   - バッチサイズ3: 3件ずつ並列起動 → 全完了後 → 次の3件へ
   - 各codexは `_tmp_refs_<連番>.csv` に個別出力 → 全バッチ完了後にマージ
4. 生成されたCSVを確認し、結果を報告する

### コンテキスト分析の出力テンプレート

Claude Codeがcodex呼び出し前に生成する中間出力:

```
## 検索インテント

### 引用 [1]
- 原文の主張: 「VLPOを中心とした睡眠促進系とオレキシン神経系を中心とした覚醒促進系の相互抑制機構」
- 必要なエビデンス: レビュー論文または基礎神経科学研究
- 検索キーワード（英語）: "VLPO sleep-wake regulation flip-flop switch hypothalamus"
- whats_interesting記載方針: 睡眠促進系と覚醒促進系の相互抑制機構（フリップフロップモデル）を提唱または実証した研究であることを記載

### 引用 [2]
- 原文の主張: 「扁桃体・前頭前皮質・前帯状皮質のネットワークが情動制御に重要」
- 必要なエビデンス: レビュー論文またはfMRI研究
- 検索キーワード（英語）: "amygdala prefrontal cortex anterior cingulate emotional regulation"
- whats_interesting記載方針: 扁桃体-前頭前皮質ネットワークによる情動制御機構を示した研究であることを記載
```

### 検索リクエストの形式（コンテキスト認識版・1引用1Codex）

各引用に対して、以下の形式で**個別にcodexを起動する**。1つのCodexには1つの引用のみを割り当てる。

```
以下の学術論文の文脈に基づいて、適切な引用文献をPubMedから検索してCSVに保存してください。

## 検索コンテキスト
原稿の分野: <例: 精神医学、睡眠医学>
対象読者: <例: 精神科医向け日本語総説>

## 検索対象（このCodexが担当する1件の引用）
### 引用 [N]
原文の主張: <主張の要約>
必要なエビデンス: <研究デザインの種類>
検索キーワード: "<英語キーワード>"
whats_interesting記載方針: <記載すべき観点>

保存先: _tmp_refs_<連番>.csv（新規作成）
注意: メインCSVには書き込まないこと。この一時ファイルにのみ書き込む。

CSV列: PubMed_ID, Author, Year, Title, Journal, Volume, Issue, Pages, doi, abstract, whats_interesting1, whats_interesting2, whats_interesting3, whats_interesting4, whats_interesting5
- whats_interesting1には上記「whats_interesting記載方針」に基づく具体的な知見を記載
- 件数は問わない。代表的・定番の論文だけを返すこと。abstractにキーワードがあるだけの論文は除外

重要な実装指示:
- Pythonスクリプトを一時ファイルとして書き出してからpythonで実行すること
- PowerShellのhere-stringやパイプ経由のPython実行は禁止
```

## 使用例

### 基本的な文献検索（単発実行）
```bash
codex exec --model gpt-5.4 --full-auto --sandbox danger-full-access --skip-git-repo-check --cd "C:\path\to\project" "「腸内細菌叢とうつ病」に関する論文をPubMedから検索して、CSVファイルに保存してください。保存先: manuscripts/refs.csv。保存方法: 既存CSVがあればdoiで重複チェックし、既存論文は次の空きwhats_interesting列に追記、新規論文は行追加。CSV列: PubMed_ID, Author, Year, Title, Journal, Volume, Issue, Pages, doi, abstract, whats_interesting1, whats_interesting2, whats_interesting3, whats_interesting4, whats_interesting5。whats_interesting1に検索文脈「腸内細菌叢とうつ病」に関連した知見を記載。重要: Pythonスクリプトを一時ファイルとして書き出してから実行すること。PowerShellのhere-stringやパイプ経由のPython実行は禁止。"
```

### 特定論文を指定して検索
```bash
codex exec --model gpt-5.4 --full-auto --sandbox danger-full-access --skip-git-repo-check --cd "C:\path\to\project" "以下の論文をPubMedから検索してCSVに追記してください: (1) Herring et al. 2016 Ann Intern Med suvorexant, (2) Mignot et al. 2022 Lancet Neurol daridorexant。保存先: manuscripts/refs.csv。保存方法: doiで重複チェックし、既存論文は次の空きwhats_interesting列に追記、新規論文は行追加。CSV列: PubMed_ID, Author, Year, Title, Journal, Volume, Issue, Pages, doi, abstract, whats_interesting1, whats_interesting2, whats_interesting3, whats_interesting4, whats_interesting5。whats_interesting列に検索文脈「DORA RCT」に関連した知見を記載。重要: Pythonスクリプトを一時ファイルとして書き出してから実行すること。"
```

### コンテキスト認識検索（原稿テキストから引用を探す）
ユーザー入力:
```
以下の文章の引用を検索してください:

睡眠不足は扁桃体の過活動と前頭前皮質の抑制機能低下を引き起こし[要引用1]、
これにより情動調節障害が生じる[要引用2]。

CSVファイル: refs_20260302.csv
```

Claude Codeの分析（codex呼び出し前の中間出力）:
```
### 引用 [1]
- 原文の主張: 「睡眠不足→扁桃体過活動+前頭前皮質の抑制機能低下」
- 必要なエビデンス: fMRI研究
- 検索キーワード（英語）: "sleep deprivation amygdala prefrontal cortex fMRI"
- whats_interesting記載方針: 睡眠不足による扁桃体-前頭前皮質の機能的解離を示したfMRI研究であることを記載

### 引用 [2]
- 原文の主張: 「睡眠不足による情動調節障害」
- 必要なエビデンス: レビュー論文またはメタアナリシス
- 検索キーワード（英語）: "sleep deprivation emotional dysregulation review"
- whats_interesting記載方針: 睡眠不足と情動調節障害の関連を体系的にまとめた研究であることを記載
```

この分析結果を元に、引用ごとに個別のcodexを起動する（バッチサイズ3で並列実行）。

### 並列実行（バッチサイズ3）
引用を3件ずつのバッチに分割し、1バッチ内は並列実行。全バッチ完了後にマージ:
```bash
# === バッチ1（3件並列実行） ===
codex exec ... "「VLPO sleep regulation」に関する論文を検索。保存先: _tmp_refs_01.csv（新規作成）。..."
codex exec ... "「amygdala prefrontal sleep deprivation」に関する論文を検索。保存先: _tmp_refs_02.csv（新規作成）。..."
codex exec ... "「circadian rhythm bipolar disorder」に関する論文を検索。保存先: _tmp_refs_03.csv（新規作成）。..."
# ↑ 3件同時に起動し、全完了を待ってから次へ

# === バッチ2（3件並列実行） ===
codex exec ... "「...」に関する論文を検索。保存先: _tmp_refs_04.csv（新規作成）。..."
codex exec ... "「...」に関する論文を検索。保存先: _tmp_refs_05.csv（新規作成）。..."
codex exec ... "「...」に関する論文を検索。保存先: _tmp_refs_06.csv（新規作成）。..."
# ↑ 全完了を待ってから次へ

# === 全バッチ完了後にマージ ===
```
全バッチ完了後にマージスクリプトを実行してメインCSVに統合する。

## CSV列定義

| 列名 | 説明 |
|------|------|
| PubMed_ID | PubMed ID（PMID）。PubMed未掲載の場合はエムダッシュ（—） |
| Author | 著者名（セミコロン区切り） |
| Year | 発行年 |
| Title | 論文タイトル |
| Journal | ジャーナル名 |
| Volume | 巻番号 |
| Issue | 号番号 |
| Pages | ページ範囲 |
| doi | DOI |
| abstract | 要旨 |
| whats_interesting1 | 検索文脈に関連した知見1 |
| whats_interesting2 | 別の検索文脈での知見2 |
| whats_interesting3 | 知見3 |
| whats_interesting4 | 知見4 |
| whats_interesting5 | 知見5 |

**注意**: 列名に特殊文字を含めない。アポストロフィはPowerShellのエスケープエラーの原因となる。

## 実行手順

1. ユーザーから検索リクエストとCSVファイル名を受け取る
2. プロジェクトディレクトリのパスを確認する
3. **検索モードを判断する**
   - **コンテキスト認識検索**: 原稿テキスト（引用マーカー付き）が提供された場合 → コンテキスト分析を先に実行
   - **通常検索**: キーワードや論文情報が明示されている場合 → 直接codexを実行
4. コンテキスト認識検索の場合、Claude Codeが検索インテントを生成する（codex呼び出し前）
5. **常に並列実行（バッチサイズ3）**
   - 引用を3件ずつのバッチに分割し、1バッチ内は並列実行
   - 各codexは `_tmp_refs_<連番>.csv` に個別出力
   - 1バッチが全完了してから次のバッチを開始
   - 全バッチ完了後にマージスクリプトを実行
6. 上記コマンド形式でCodexを実行
7. 生成されたCSVファイルの行数を確認して結果を報告
8. **★ 全バッチ完了後、マージテンプレートを実行してメインCSVに統合する。マージが完了するまで次の工程（endnote-insert等）に絶対に進まない**
9. **★ マージ後、メインCSVのPubMed_ID列を検査する。空欄の行がある場合はユーザーに報告し、PubMed APIまたは追加検索で補完する。全行にPubMed_IDが入っている（またはPubMed未掲載としてエムダッシュが入っている）ことを確認してから次の工程に進む**

### ★ PMIDハルシネーション防止ルール（絶対厳守）

過去にLLMがPMIDを「記憶」から捏造し、完全に間違った論文がEndNoteフィールドに挿入される重大事故が発生した。以下のルールを絶対に守ること。

1. **PMIDを「記憶」や「推測」で生成することは絶対に禁止** — PMIDは必ずPubMed APIの検索結果、またはcodex-refsが生成したCSVから取得する
2. **PMIDの出典を明示する** — スクリプトやentries配列にPMIDを書く際は、データソース（CSVファイル名、API応答等）をコメントとして記載する。出典を書けないPMIDは使用しない
3. **PMIDを使用する前に必ず検証する** — PubMed APIでPMIDを引き、返ってきた著者名・年・タイトルが意図した文献と一致することを確認する
4. **CSVのPubMed_ID列が空欄のまま次工程に進まない** — 空欄がある場合はマージ漏れか検索漏れなので、原因を特定して補完する

## 文献選定品質ルール（重要）

codexが返す論文の品質を担保するためのルール。codexへのプロンプトとClaude Code側の検証の両方に適用する。

### ルール1: 件数を指定しない。「代表的」で品質を指定する

```
❌ 「1-2件検索して」「3件見つけて」
✅ 「代表的な論文を検索して」「この分野で最も広く引用されている論文を探して」
```

件数を指定するとcodexは「枠を埋める」ために質の低い論文を拾う。件数はcodex側に判断させ、適切なものだけ返させる。

### ルール2: エビデンスレベルは文脈から推論させる

研究デザインをこちらが固定せず、codexに文脈から判断させる。以下の定型文をプロンプトに含める：

```
この主張を裏付ける、最もエビデンスレベルの高い代表的な論文を探して。
- 広く認知された事実 → メタアナリシスやシステマティックレビュー
- 治療効果に関する主張 → RCTまたはそのメタアナリシス
- 稀な現象や新しい知見 → 観察研究や症例集積でもよい
- いずれの場合も、その分野で代表的・定番とされる論文を優先すること
```

### ルール3: 「なぜこの引用が必要か」を必ず含める

```
❌ 「BZD withdrawal rebound insomnia review を検索」
✅ 「原稿中で『BZD/Z-drug離脱がリバウンド不眠や悪夢を引き起こすことが知られている』
   と述べた箇所の引用。この主張を裏付ける代表的なレビュー論文をPubMedから探して。」
```

キーワード検索だけだとabstractに単語が含まれるだけの論文が引っかかる。主張の文脈を伝えることで、その主張の根拠として最適な論文が選ばれる。

### ルール4: 選定理由を説明させる

```
✅ 「各論文について、なぜこの論文が上記の主張の引用として適切か、
   他の候補と比べてなぜこれを選んだかを whats_interesting1 に記載すること」
```

選定理由を書かせることで、abstractにキーワードがあっただけの論文が排除される。

### ルール5: Claude Code側で内容的妥当性を必ず判断する

codexから返ってきた論文に対して、PMIDの技術的検証（タイトル・著者一致）に**加えて**、以下の内容的検証を行う：

- その論文の**メインテーマ**が引用文脈と合致しているか
- abstractの一部にキーワードが出てくる**だけ**ではないか
- もっと代表的・定番の文献が存在しないか

技術的に正しいPMIDでも、文脈に不適切な論文はユーザーに報告して差し替えを提案する。

### 改善されたcodexプロンプトテンプレート

上記ルールを反映した統一テンプレート：

```
以下の原稿の主張を裏付ける、代表的かつエビデンスレベルの高い
PubMed掲載論文を検索してCSVに保存してください。

## 引用コンテキスト
原稿の主張: 「<主張をそのまま記載>」
分野: <睡眠医学、精神薬理学 等>

## 選定基準
- この主張を直接裏付ける、その分野で代表的・定番の論文を優先
- 広く認知された事実 → メタアナリシスやシステマティックレビュー
- 治療効果 → RCTまたはそのメタアナリシス
- 稀な現象・新しい知見 → 観察研究や症例集積も可
- 件数は問わない。適切な論文だけを返すこと
- abstractにキーワードが含まれるだけの論文は除外すること

## CSV出力
保存先: _tmp_refs_<連番>.csv（新規作成）
CSV列: PubMed_ID, Author, Year, Title, Journal, Volume, Issue, Pages, doi, abstract,
       whats_interesting1, whats_interesting2, whats_interesting3, whats_interesting4, whats_interesting5
- whats_interesting1: なぜこの論文を選んだか、他の候補と比べた選定理由を記載
- PubMed_IDはPMID。PubMed未掲載の場合はエムダッシュ（—）

重要な実装指示:
- Pythonスクリプトを一時ファイルとして書き出してからpythonで実行すること
- PowerShellのhere-stringやパイプ経由のPython実行は禁止
```

## Step 10〜12: CEC表（Claim–Evidence–Citation）生成

refs.csv完成後、以下のステップでCECsheet.mdを生成・更新する。CEC表はハルシネーションと引用ドリフトを防止するための主張-根拠-引用の対応表である。

### 目的
- 「その論文が実在する」ことと「その論文がその主張を支える」ことは別問題（Li et al., 2025）
- 各引用について「どのページ・表・図が主張を支えるか」まで記録する
- ユーザー（上司含む）がレビュー時に文献の妥当性を即座に確認できるようにする

### Step 10: PubMed APIで書誌情報を検証・補正

refs.csvの各行に対してPubMed E-utilities APIで書誌情報を検証し、codexが返した情報をAPI応答で上書きする。これによりAuthor/Title/Journal等のハルシネーションを排除する。

#### 検証Pythonテンプレート

```python
import csv
import urllib.request
import xml.etree.ElementTree as ET
import time
import os

MAIN_CSV = 'refs_YYYYMMDD.csv'  # 実際のファイル名に置き換え
FIELDNAMES = ['PubMed_ID', 'Author', 'Year', 'Title', 'Journal', 'Volume', 'Issue', 'Pages', 'doi', 'abstract', 'whats_interesting1', 'whats_interesting2', 'whats_interesting3', 'whats_interesting4', 'whats_interesting5']

def fetch_pubmed_metadata(pmid):
    """PubMed efetch APIで書誌情報を取得"""
    url = f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&rettype=xml'
    req = urllib.request.Request(url, headers={'User-Agent': 'Claude-Code/1.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        tree = ET.parse(resp)
    root = tree.getroot()
    article = root.find('.//PubmedArticle')
    if article is None:
        return None

    # 著者抽出
    authors = []
    for au in article.findall('.//Author'):
        last = au.findtext('LastName', '')
        fore = au.findtext('ForeName', '')
        if last:
            authors.append(f'{last} {fore}'.strip())

    # 書誌情報抽出
    meta = {
        'Author': '; '.join(authors),
        'Title': article.findtext('.//ArticleTitle', ''),
        'Journal': article.findtext('.//ISOAbbreviation', '') or article.findtext('.//Title', ''),
        'Year': article.findtext('.//PubDate/Year', '') or article.findtext('.//PubDate/MedlineDate', '')[:4] if article.findtext('.//PubDate/MedlineDate') else '',
        'Volume': article.findtext('.//Volume', ''),
        'Issue': article.findtext('.//Issue', ''),
        'Pages': article.findtext('.//MedlinePgn', ''),
        'doi': '',
        'abstract': '',
    }
    # DOI
    for aid in article.findall('.//ArticleId'):
        if aid.get('IdType') == 'doi':
            meta['doi'] = aid.text or ''
    # Abstract
    abs_parts = article.findall('.//AbstractText')
    meta['abstract'] = ' '.join(a.text or '' for a in abs_parts)

    return meta

# メインCSV読み込み → 検証 → 上書き
rows = []
corrections = []
with open(MAIN_CSV, 'r', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append(row)

for i, row in enumerate(rows):
    pmid = row.get('PubMed_ID', '').strip()
    if not pmid or pmid == '—':
        continue  # PubMed未掲載はスキップ
    try:
        meta = fetch_pubmed_metadata(pmid)
        if meta is None:
            corrections.append(f'  Row {i+2}: PMID {pmid} — PubMedに存在しない！要確認')
            continue
        # 不一致を検出・上書き
        for key in ['Author', 'Title', 'Journal', 'Year', 'Volume', 'Issue', 'Pages', 'doi', 'abstract']:
            old_val = row.get(key, '').strip()
            new_val = meta.get(key, '').strip()
            if new_val and old_val != new_val:
                if old_val:
                    corrections.append(f'  Row {i+2} [{key}]: "{old_val[:50]}" → "{new_val[:50]}"')
                row[key] = new_val
        time.sleep(0.35)  # レート制限: 3 req/sec
    except Exception as e:
        corrections.append(f'  Row {i+2}: PMID {pmid} — API error: {e}')

# 上書き保存
with open(MAIN_CSV, 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, '') for k in FIELDNAMES})

print(f'Verified {len(rows)} rows.')
if corrections:
    print(f'\n⚠️ {len(corrections)} corrections:')
    for c in corrections:
        print(c)
else:
    print('✓ All bibliographic data matches PubMed API.')
```

### Step 11: フルテキスト取得（PMC → Unpaywall → 断念）

各論文のフルテキストを取得し、CEC表のEvidence列に具体的な表/図/セクションを記載するために使用する。

#### 取得優先順位
1. **PMC**: elink APIでPMID→PMC IDを変換し、efetchでフルテキストXMLを取得
2. **Unpaywall**: DOIからOA版（著者最終稿含む）のURLを取得
3. **断念**: CECsheet.mdに「フルテキスト未取得。ユーザーに確認を委任」と記載

#### フルテキスト取得Pythonテンプレート

```python
import urllib.request
import xml.etree.ElementTree as ET
import json
import time

UNPAYWALL_EMAIL = 'your_email@example.com'  # Unpaywall API用（APIキー不要、メールのみ）

def get_pmc_id(pmid):
    """elink APIでPMID → PMC IDを取得。PMCに無い場合はNone"""
    url = f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi?dbfrom=pubmed&db=pmc&id={pmid}&retmode=xml'
    req = urllib.request.Request(url, headers={'User-Agent': 'Claude-Code/1.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        tree = ET.parse(resp)
    link_set = tree.find('.//LinkSetDb')
    if link_set is not None:
        link_id = link_set.findtext('.//Id')
        return link_id  # PMC ID（数字のみ、"PMC"接頭辞なし）
    return None

def get_pmc_fulltext(pmc_id):
    """PMCからフルテキストXMLを取得"""
    url = f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmc_id}&rettype=xml'
    req = urllib.request.Request(url, headers={'User-Agent': 'Claude-Code/1.0'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode('utf-8')

def get_unpaywall_url(doi):
    """Unpaywall APIでOA版URLを取得。無い場合はNone"""
    url = f'https://api.unpaywall.org/v2/{doi}?email={UNPAYWALL_EMAIL}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Claude-Code/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        best = data.get('best_oa_location')
        if best:
            return best.get('url_for_pdf') or best.get('url_for_landing_page') or best.get('url')
    except Exception:
        pass
    return None

def try_get_fulltext(pmid, doi):
    """フルテキスト取得を試みる。結果は (text_or_url, source) のタプル"""
    # 1. PMC
    pmc_id = get_pmc_id(pmid)
    if pmc_id:
        time.sleep(0.35)
        try:
            xml_text = get_pmc_fulltext(pmc_id)
            return xml_text, 'PMC'
        except Exception:
            pass

    time.sleep(0.35)

    # 2. Unpaywall
    if doi:
        oa_url = get_unpaywall_url(doi)
        if oa_url:
            return oa_url, 'Unpaywall'

    # 3. 断念
    return None, 'unavailable'
```

#### Claude本体でのフルテキスト活用手順
1. `try_get_fulltext()` を各論文に対して実行
2. PMCフルテキスト（XML）が取れた場合 → Claude本体がXMLを読み、該当する表/図/セクションを特定
3. Unpaywall URLが取れた場合 → WebFetchでHTML/PDFを取得し、内容を確認
4. 取得不可の場合 → abstractのみでEvidence列を記載し、Notes列に `⚠ フルテキスト未取得` と記載

### Step 12: CECsheet.md の生成・更新

Claude本体が以下の情報を統合してCECsheet.mdを生成（または既存ファイルに追記）する。

#### 入力情報
- 検索インテント（Step 2で生成済み）: 原稿の主張と検索キーワード
- refs.csv（Step 10で検証済み）: 書誌情報（PubMed API由来で信頼性担保済み）
- フルテキスト or abstract（Step 11で取得）: Evidence抽出元

#### Support Level 判定基準（Claude本体が判断）
- **direct support**: 論文の主要結果が原稿の主張を直接裏付ける
- **partial support**: 関連するが、主張の一部のみ支持。または異なる集団・条件での結果
- **background**: 背景知識・定義・概念の紹介として引用。直接の裏付けではない
- **contraevidence**: 主張と矛盾する結果を含む（引用する場合は「一方で〜」等の文脈）

#### CECsheet.md フォーマット仕様

```markdown
# CEC Sheet (Claim–Evidence–Citation)
<!-- codex-refs スキルが自動生成・追記するファイル -->
<!-- ユーザーが手動でEvidence列やNotes列を編集・補完することを推奨 -->
<!-- Support Level: direct support / partial support / background / contraevidence -->

## YYYY-MM-DD: <検索テーマ>

| # | Claim | Evidence | Citation | Support | Notes |
|---|-------|----------|----------|---------|-------|
| 1 | <原稿の主張> | <フルテキストから: Fig.X / Table Y / p.Z の具体的記述> | <著者 (年) ジャーナル. PMID:XXXXX> | direct support | |
| 2 | <原稿の主張> | [abstract only] "<abstractからの引用>" | <著者 (年) ジャーナル. PMID:XXXXX> | partial support | ⚠ フルテキスト未取得。具体的なTable/Figの確認はユーザーに委任 |
| 3 | <原稿の主張> | — | <著者 (年) ジャーナル. PubMed未掲載> | background | ⚠ PubMed未掲載：書誌情報は未検証 |
```

#### フォーマットルール
- **通し番号（#列）**: ファイル全体で連番。新セクション追記時は前セクションの最終番号の続きから
- **Claim列**: 検索インテントの「原文の主張」をそのまま記載
- **Evidence列**:
  - フルテキストあり → `Fig.2: 断眠群でamygdala活動60%増加(p<0.001)` のように具体的に
  - abstractのみ → `[abstract only] "引用文"` の形式
  - PubMed未掲載 → `—`
- **Citation列**: `著者 (年) ジャーナル略称. 巻(号):ページ. PMID:XXXXX` 形式。書誌情報はStep 10でPubMed API検証済みのデータを使用すること（codexの出力をそのまま使わない）
- **Support列**: 上記4段階のいずれか
- **Notes列**: 自動コメント + ユーザーの手書きコメント用。空欄でもよい

#### 既存CECsheet.mdへの追記ルール
1. 既存ファイルがある場合は末尾に新セクション（`## YYYY-MM-DD: <テーマ>`）を追加
2. 通し番号は既存の最終番号の続きから
3. **既存セクションの内容は絶対に変更しない**（ユーザーが手動編集している可能性がある）
4. 同じ論文が複数セクションに登場してもよい（異なるClaimに対するEvidenceが異なるため）

#### ユーザーによる手動更新の想定
ユーザーが後からフルテキストを読んだ場合:
- `[abstract only]` を削除し、具体的なEvidence（Table/Fig/ページ番号）に書き換える
- Support Levelを修正する
- Notes列にコメントを追加する

## 実行手順（更新版・全体フロー）

1〜9: （既存の手順、変更なし）
10. **書誌情報検証**: refs.csvの全行をPubMed efetch APIで検証。Author/Title/Journal等をAPI応答で上書き。不一致があればログ出力
11. **フルテキスト取得**: 各論文に対してPMC → Unpaywall の順でフルテキスト取得を試みる
12. **CEC表生成**: 原稿の主張（検索インテント）× 論文（refs.csv + フルテキスト/abstract）を照合し、CECsheet.mdを生成または追記。Citation列の書誌情報は必ずStep 10の検証済みデータを使用する

## 注意事項

### PowerShellエスケープ問題の回避
Codexは内部でPowerShellを使用するため、以下のパターンでエラーが頻発する：
- `@'...'@`（here-string）内の引用符
- パイプ（`|`）経由でのPython実行
- 長い文字列リテラル内の特殊文字

**解決策**: プロンプトで「Pythonスクリプトを一時ファイルとして書き出してから実行」を明示的に指示する。

### 重複論文の扱い
- doiで重複チェックを行い、同じ論文は同一行に保持する
- 異なる検索文脈での知見はwhats_interesting1〜whats_interesting5の空き列に順次追記
- 重複チェックの流れ:
  1. 既存CSVを読み込み、doiの一覧を取得
  2. 新規検索結果のdoiが既存に含まれていれば、その行の次の空きwhats_interesting列に知見を追記
  3. 新規論文（doiが一致しない）は新しい行として追加
- doiが空欄の論文は常に新規行として追加する

### 並列実行時のレースコンディション防止
- **絶対に複数のcodexプロセスが同じCSVファイルに同時書き込みしない**
- 並列実行時は必ず個別の一時CSVに出力し、全完了後にマージする
- マージ処理は親プロセス（Claude Code本体）が順次実行する
