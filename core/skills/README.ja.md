# dpd / skill — Claude Code skill family

[English](README.md)

DPD の対話 UX 層。skill family はユーザの意図 (`/dpd` 発火、「これ何度も浮上するから記録したい」等) を解釈し、グラフ更新を提案し、確認済みの更新を MCP tool 呼び出しに proxy する。グラフ状態はすべて [MCP server](../mcp/) に存在し、skill は stateless。

## 役割

[`mcp/`](../mcp/) サーバがプロトコルの "kernel" (state + tools) なら、skill は "shell" (prompts + UX)。意図的な分離:

- **skill は *何を提案するか* を決める** — 対話文脈 (決定動詞、仮説クラスタ、トピックシフト) に基づく。検出ロジック、絞り込みルール、自然な区切り検出ヒューリスティクスはここに住む。
- **MCP server は *何が valid か* を決める** — parent_kind の整合性、lifecycle の単調性、atomic な branch 解決。schema と不変条件はあちらに住む。
- **どちらも対話履歴を保存しない**。対話は Claude Code の session transcript にあり、グラフ状態は SQLite にある。

## ファミリ

エントリ skill 1 つと、ユーザが直接 invoke できる明示的な sub-skill 数個で構成。

### Entry skill: `/dpd`

[`SKILL.md`](SKILL.md) がメインの `/dpd` skill。**operating lifecycle 全体** を司る:

- **Bottom-up trigger** — ユーザが「対話を整理したい」と感じた時に `/dpd` 発火。
- **Claude-suggested trigger** — もつれ (複数 open thread、anchor 無しの決定動詞、矛盾の surface) を検知した時、Claude が soft な提案を volunteer する。
- **Startup sequence** — sub-scope 検出 (`.dpdrc` walk-up または `--scope=<name>` 引数)、session 一覧、resume か新規かの確認。
- **Entry phase** — 対話要約、ゴール絞り込み、grounded/inferred 階層化での初期グラフ構築。
- **Ambient mode** — 定常状態の観察: 信号検知、attach 判定、pending 更新蓄積、自然な区切りで custodial トーンで提案。
- **End 達成** — achievement_conditions が満たされたら `mark_reached` を提案 + Pool 処分。

挙動を拡張する前に [`SKILL.md`](SKILL.md) を完全に読むこと。「コードが何をするか」を超えた設計判断 — 特に End modification gate、drift 検出、Pool reject identity ルール — がここに encode されている。

### Sub-skills

各 sub-skill は自分の `SKILL.md` を持つディレクトリ。独立に invoke 可能 (例: `/dpd-status`)、main `/dpd` skill が暗黙裡に delegate することもある。

| Sub-skill | 役割 |
| --- | --- |
| [`dpd-status`](dpd-status/) | 現 session のスナップショット: active roots、focus node、Pool items (active + rejected)、session mode。「今どこ?」に答える。 |
| [`dpd-dump`](dpd-dump/) | DPD グラフ全体を JSON 形式 YAML で dump (`export_yaml`、json.loads round-trip 可)。audit / snapshot / diff / docs 貼り付け用。 |
| [`dpd-summary-md`](dpd-summary-md/) | 決定済み / closed 済みアイテムを抽出して markdown summary にする。session 終了時、settled subgraph から spec 素材を作る時に使う。 |
| [`dpd-edit`](dpd-edit/) | node または Pool item の手動編集 — `close_node` / `add_node(provenance='manual')` / `pool_reject` / Pool unsuppress を wrap。ambient mode を超える直接制御をユーザが望む時に。 |
| [`dpd-fill`](dpd-fill/) | 現グラフに対する推論ノードを生成: 欠けた decomposition、未明示の前提、gap 候補。各推論ノードはユーザ opt-in が必要。Falsification 用の `/fcot` と組み合わせて使われることが多い。 |
| [`dpd-import`](dpd-import/) | 外部 prose/spec/graph 文書を hypothetical archived DPD subgraph として import。systematic な gap 分析 (例: spec の self-validation) の `dpd-import → dpd-fill → /fcot` パイプラインで使う。 |
| [`dpd-verify-edge`](dpd-verify-edge/) | `layer='necessary'` の edge (proof-tree discipline, #42) を context-stripped prompt で外部検証。独立した verifier が agent の枠組みに同調せず含意を判定する。verdict を記録し、`refuted` は降格を自動適用せず提案のみ。 |

`dpd-import → dpd-fill → /fcot` パイプラインは v0.3.1 spec 自体を検査した **documented な self-validation フローの 1 つ**。`/fcot` は high-stakes 推論ノードに対しては自動、それ以外は opt-in で、求める厳密性に応じて verification コストがスケールする。dogfood の経緯は top-level [`README.ja.md`](../README.ja.md#dpd-で-agent-driven-に作られた) 参照。

## インストール

skill family は Claude Code が discover するために `~/.claude/skills/dpd/` (および `~/.claude/skills/dpd-status/` のような sub-skill ディレクトリ) に展開される。これは repo-root の `install.sh` が自動で symlink する — ワンライナーは [top-level README](../README.ja.md#インストール) を、手動 symlink コマンドは [AGENTS.md](../AGENTS.md#setup) を参照。install.sh にこのステップを skip させたい場合は `DPD_NO_SKILL_LINK=1` を設定する。

install 後は Claude Code を再起動。`/dpd` / `/dpd-status` / `/dpd-dump` 等の invocation が利用可能になるはず。

## MCP サーバとの関係

skill の `SKILL.md` は MCP tool を完全修飾名 (`mcp__dpd-mcp-server__list_sessions` 等) で参照している。server 未登録の場合 ([`../mcp/README.ja.md`](../mcp/README.ja.md) 参照)、skill は起動時に検知してインストールを案内する。

skill は MCP server の外にデータを永続化しない。skill が render するもの (グラフ図、session 一覧、pool スナップショット) はすべて、毎ターン server から fresh に読み出される。Claude Code 再起動を跨いでもグラフ状態が生存するのはこのため — skill 自身は記憶を持たない。

## Versioning

skill のバージョンは MCP server の major.minor (現状 `0.3.x`) を tracking する。server 変更を要する skill 更新は [AGENTS.md](../AGENTS.md) のワークフローに従い 1 PR で coordinate する。
