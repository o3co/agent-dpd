# DPD — Decompose-Propagate Decision

[English](README.md)

長く枝分かれする AI 対話を明示的な意思決定グラフに変換する Claude Code skill + MCP server。決定はその根拠と共に記録され、却下された仮説も消えず、「X について何を決めたか」がトランスクリプト検索の問題ではなくなる。

> **Status**: `0.x` — 1.0 前、public surface は変更されうる。詳細は [docs/concept.ja.md](docs/concept.ja.md#status-と-versioning) 参照。

---

## インストール

Python 3.11+、[Claude Code](https://docs.anthropic.com/en/docs/claude-code)、`make` が必要。

```bash
git clone https://github.com/o3co/agent-dpd.git
cd agent-dpd
make dev          # venv を作成 + install + Claude Code に登録
```

Claude Code を再起動すると `/dpd` skill が利用可能になる。Make が使えない場合の手動手順は [AGENTS.md](AGENTS.md#setup) を参照。

---

## DPD を使うと何が起こるか

典型的な session を要約すると:

```text
You: /dpd
DPD: この workspace に既存 session なし。新規に始めますか？
     直前まで "認証方式を選ぶ" について話していたのを観測。
     提案する anchor:
       Start: 認証方式を選ぶ
       End:   認証方式が決定 + 根拠が記録
     適用しますか？
You: yes
DPD: [root + Start + End anchor を作成]
     ambient モードに入りました。普通に会話を続けてください。

[JWT、session cookie、OAuth について議論]

DPD: ここまでで記録したいのはこれです:
       - hypothesis: JWT          → rejected として close (token revocation 困難)
       - hypothesis: session cookie
       - hypothesis: OAuth 限定   → rejected として close (3rd-party 依存)
     適用しますか？
You: yes
DPD: [ノード追加、rejected を close]

[さらに議論、決定に至る]

DPD: session cookie で決まったようです。記録します:
       - decision: session cookie で行く
       - rationale: same-origin 制約に対する最も単純な正解
       - derived_from: ↑ same-origin 制約 (evidence node)
     End 条件を満たしたので、この subgraph を到達済みにマークしますか？
You: yes
DPD: [mark_reached] Session は idle に。Subgraph 閉じました。
```

あなたは普通に prose を書く。DPD は bookkeeping (提案・記録・narrowing) を担当し、自然な区切りで確認を求める。あなたの明示的 OK なしにグラフは変わらない。

---

## できること

### コマンド

| コマンド | 役割 |
| --- | --- |
| `/dpd` | 意思決定の tracking 開始、または既存 session の resume |
| `/dpd-status` | 「今どこ?」snapshot — active roots、focus、Pool items、mode |
| `/dpd-dump` | グラフ全体を tree または Mermaid で表示 |
| `/dpd-summary-md` | 決定済み/closed item を markdown summary で抽出 |
| `/dpd-edit <id>` | 直接制御したい時の手動編集 |
| `/dpd-import <file>` | prose/spec/graph 文書を archived subgraph として import |
| `/dpd-fill` | 現グラフに対する gap 候補の推論ノード生成 |

### 例: spec の self-validation

`/dpd-import → /dpd-fill → /fcot` パイプラインで任意の設計文書に systematic な gap 分析を適用できる:

```text
/dpd-import path/to/your-spec.md
    # spec を archived subgraph として import

/dpd-fill
    # 推論ノード生成 — 欠けた decomposition、未明示の前提

/fcot
    # 各推論ノードを spec text に対して反証
    # → real gap が survive、もっともらしいだけのものはフィルタアウト
```

これは DPD 自身の spec をリリース前に検証した方法 — 詳細と発見した gap については [docs/concept.ja.md#dpd-で-agent-driven-に作られた](docs/concept.ja.md#dpd-で-agent-driven-に作られた) 参照。

### 向いている場面

- 数日を跨ぐ作業、複数 session のプロジェクトで「何を決めたか」が後から重要になる
- 複数の有望なブランチがあり、後から振り返りたい対話
- アーキテクチャ / スコープ / ポリシー判断で paper trail が欲しい
- Spec / 設計文書のレビュー (上記 self-validation パイプライン)

### 大袈裟な場面

- 短い single-thread な対話
- 機械的でよく specified なタスク
- resume の必要がない使い捨て探索

---

## Optional: scope marker

複数の sibling プロジェクトディレクトリで 1 つの DPD DB を共有したい場合 (例: monorepo)、workspace ルートに `.dpdrc` を置く:

```ini
# .dpdrc — DPD scope marker
scope=my-workspace
```

server はエディタの cwd から walk-up してこの marker を探し、その位置を agent-scope の識別子に使う。詳細と sub-scope の挙動は [AGENTS.md](AGENTS.md#sub-scope-detection-dpdrc) を参照。

---

## 詳しく知る

- **[docs/concept.ja.md](docs/concept.ja.md)** — DPD とは何か、なぜ存在するか、グラフの動き、lifecycle 状態、agent-driven dogfood の物語
- **[mcp/README.ja.md](mcp/README.ja.md)** — MCP サーバアーキテクチャ + 30 tool リファレンス
- **[skill/README.ja.md](skill/README.ja.md)** — Skill family 概要 (main `/dpd` + sub-skills)
- **[AGENTS.md](AGENTS.md)** — Contributor ガイドライン (TDD、レビューフロー、規約)

---

## License

Apache 2.0 — [LICENSE](LICENSE) 参照。Copyright © 2026 [1o1 Co. Ltd.](https://1o1.co.jp/)

Contribution 歓迎。PR を開く前に [AGENTS.md](AGENTS.md) を読んでください。
