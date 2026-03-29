# codex-refs

[OpenAI Codex CLI](https://github.com/openai/codex) を使って学術文献を検索し、結果をCSVに出力する [Claude Code](https://docs.anthropic.com/en/docs/claude-code) カスタムスラッシュコマンドです。

## 特徴

- PubMedを中心に学術文献を検索（書籍・ガイドライン・プレプリントにも対応）
- PMID・著者・タイトル・DOI・要旨などを構造化してCSVに保存
- DOIベースの重複チェックで既存CSVへの安全な追記が可能
- 各文献に最大5つの文脈別知見（`whats_interesting1`〜`5`）を付与
- 複数テーマの並列検索に対応
- **コンテキスト認識検索**: 原稿テキストの文脈を分析し、各引用に最適な論文を自動検索
- **CEC表（Claim–Evidence–Citation）自動生成**: ハルシネーション・引用ドリフト防止のための主張-根拠-引用の対応表を出力
- **PubMed API検証**: 書誌情報をPubMed E-utilities APIで自動検証・補正
- **フルテキスト取得**: PMC / Unpaywall経由でOA論文のフルテキストを取得し、具体的な表・図・ページを特定

## 必要環境

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- [OpenAI Codex CLI](https://github.com/openai/codex)（`npm install -g @openai/codex`）
- Python 3

## インストール

`.claude/commands/codex-refs.md` をプロジェクトの `.claude/commands/` ディレクトリにコピーしてください。

```bash
git clone https://github.com/matsuikentaro1/codex-refs.git

mkdir -p /path/to/your/project/.claude/commands
cp codex-refs/.claude/commands/codex-refs.md /path/to/your/project/.claude/commands/
```

## 使い方

Claude Code上でスラッシュコマンドを実行します：

```
/codex-refs
```

検索キーワードとCSVファイル名を伝えるだけで、文献検索からCSV保存まで自動で行います。

### できること

- **キーワード検索** — 「腸内細菌叢とうつ病」のようなテーマで論文を検索
- **特定論文の検索** — 「Herring et al. 2016 Ann Intern Med suvorexant」のように著者・年・ジャーナルを指定
- **並列検索** — 複数テーマを同時に検索し、結果を1つのCSVに統合
- **コンテキスト認識検索** — 原稿テキストを貼り付けると、引用マーカーの前後の文脈を分析し、「なぜその引用が必要か」を理解したうえで最適な論文を検索

### CEC表（Claim–Evidence–Citation）

文献検索後、自動的にCECsheet.mdを生成します。

```markdown
| # | Claim | Evidence | Citation | Support | Notes |
|---|-------|----------|----------|---------|-------|
| 1 | 睡眠不足は扁桃体の過活動を… | Fig.2: 断眠群でamygdala活動60%↑ | Yoo SS et al. (2007) Curr Biol. PMID:17956744 | direct support | |
| 2 | 情動調節障害が生じる | [abstract only] "emotional dysregulation..." | Walker MP (2009) Ann N Y Acad Sci. PMID:19338508 | partial support | ⚠ フルテキスト未取得 |
```

- **ハルシネーション防止**: Citation列の書誌情報はPubMed APIで検証済みのデータのみ使用
- **フルテキスト活用**: PMC・Unpaywallからフルテキストを取得し、具体的な表・図・ページを特定
- **Support Level**: direct support / partial support / background / contraevidence の4段階で自動判定
- **追記・更新対応**: ユーザーが手動でEvidence列やNotes列を編集・補完可能。再検索時は既存内容を保持したまま新セクションを追記

### コンテキスト認識検索の例

```
以下の文章の引用を検索してください:

睡眠不足は扁桃体の過活動と前頭前皮質の抑制機能低下を引き起こし[要引用1]、
これにより情動調節障害が生じる[要引用2]。

CSVファイル: refs.csv
```

Claude Codeが各引用の文脈（主張の内容・必要なエビデンスの種類・適切な検索キーワード）を分析してからCodex CLIに検索を指示するため、的中率の高い文献が得られます。

## ライセンス

MIT
