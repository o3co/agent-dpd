# DPD — Decompose-Propagate Decision - AIを用いた思考の整理手法

[English](README.md)

AI 対話で閃いたり、何をやりたいかがシフトした際、AI がついてこない — そんなことありませんか？

DPD は、AI との対話を通し、思考をグラフ化、ゴールに向けた思考プロセスを整理します。これにより、ゴールが変更されたり、不足している考慮点、また、過去に話した内容との矛盾を適切に絞り出すためのアプローチを AI 自身が手助けしてくれます。

Claude Code スキル + MCP サーバで実現します。

> **状態**: `0.x` — 1.0 前のため、公開インターフェースは変更される可能性があります。詳細は [docs/concept.ja.md](docs/concept.ja.md#status-と-versioning) 参照。

---

## インストール

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) と **Python 3.11+** (`PATH` 上) が必要です。Python は SessionStart フックが MCP サーバ用の venv を bootstrap するときに使用します。他クライアントは以下を参照。

### Claude Code (推奨)

```text
/plugin marketplace add o3co/agent-dpd
/plugin install dpd@agent-dpd
```

**インストール後は新しい Claude Code セッションを開始してください** (ターミナルで `claude` を新規起動、または IDE を完全に閉じて開き直す)。`/reload-plugins` や window reload だけでは初回 install 時の SessionStart フックが発火しないことがあり、venv が bootstrap されません。新セッションの初回起動は ~10–30s かかります (フックがバンドルされた server source に対して `pip install -e` を走らせるため)。

> **0.3.x からのアップグレード**: 以前 `install.sh` でインストールしていた場合は、先に `rm -f ~/.claude/skills/dpd ~/.claude/skills/dpd-*` と `claude mcp remove dpd-mcp-server` を実行してください。古い symlink と MCP 登録がプラグイン側を shadow しないようにするためです。`~/.claude/dpd-server/data/` のグラフデータは保持されます。詳細は [CHANGELOG: Upgrading from 0.3.x](CHANGELOG.md#upgrading-from-03x) を参照。

このリポジトリを Claude Code のマーケットプレイスとして登録し、`dpd` プラグインをインストールします。プラグインには以下が含まれます:

- `/dpd`, `/dpd-status`, `/dpd-dump`, `/dpd-edit`, `/dpd-fill`, `/dpd-find-similar`, `/dpd-import`, `/dpd-summary-md`, `/dpd-feedback` スラッシュコマンド
- MCP サーバ (`dpd-mcp-server`)。初回セッション時に venv を自動構築します
- プラグイン同梱の Python パッケージと venv を同期させる SessionStart フック

プラグイン本体は `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` (実際のパス: `~/.claude/plugins/cache/agent-dpd/dpd/0.5.0/`)、永続 venv は `~/.claude/plugins/data/<plugin>-<marketplace>/.venv/` (実際のパス: `~/.claude/plugins/data/dpd-agent-dpd/.venv/`) に配置されます。venv パスは Claude Code が SessionStart フックに渡す `${CLAUDE_PLUGIN_DATA}` の実体です。

更新するには `/plugin update dpd` を実行するか、Claude Code の自動更新に任せます。これでプラグイン同梱ソースが更新され、次セッションの SessionStart フックが pyproject.toml ハッシュ変化を検出して venv を再ビルドします。プラグインの venv 内で直接 `pip install -U dpd-mcp-server` を **実行しないでください**: フックはこの手動更新を検出できず (バンドルソース変化時のみ再ビルド)、venv が同梱ソースと静かに desync します。venv をクリーンにしたい場合は `~/.claude/plugins/data/dpd-agent-dpd/.venv/` を削除して Claude Code を再起動すれば、フックが再ビルドします。

### Cursor

```bash
curl -fsSL https://raw.githubusercontent.com/o3co/agent-dpd/main/install.sh | bash
```

リポジトリのクローン、`core/server/.venv` への venv 作成、`core/skills/*` の `~/.cursor/skills/` への symlink、および `~/.cursor/mcp.json` への `dpd-mcp-server` 登録をまとめて行います。実行後は Cursor を再起動してください。

Cursor インストーラの環境変数:

| 変数 | デフォルト | 用途 |
| --- | --- | --- |
| `DPD_INSTALL_DIR` | `$HOME/agent-dpd` | クローン先ディレクトリ |
| `DPD_PYTHON` | `python3.11` | 使用する Python インタプリタ |
| `DPD_CURSOR_HOME` | `$HOME/.cursor` | Cursor 設定ディレクトリ |
| `DPD_NO_CURSOR_SKILL_LINK` | 未設定 | スキルの symlink をスキップ |
| `DPD_NO_CURSOR_MCP_PATCH` | 未設定 | `mcp.json` へのパッチをスキップ |

### Cline

Cline は Anthropic 形式のスキルを自動検出します。リポジトリをクローンし、Cline のドキュメントに従って `core/skills/` を参照先として設定してください。MCP は Cline のマーケットプレイス経由で利用できます。

### Codex CLI / Gemini CLI / Claude Desktop / ChatGPT

0.4 では未対応です。ロードマップは [tracking issue #16](https://github.com/o3co/agent-dpd/issues/16) を参照。

### 手動インストール (任意のエージェント)

```bash
git clone https://github.com/o3co/agent-dpd.git
cd agent-dpd
python3.11 -m venv core/server/.venv
core/server/.venv/bin/pip install -e 'core/server[dev]'
# その後、各クライアントのドキュメントに従って dpd-mcp-server を登録してください。
# スキル群は core/skills/ 配下にあります。
```

手動セットアップの詳細は [AGENTS.md](AGENTS.md#setup) を参照。

---

## DPD を使うと何が起こるか

典型的なセッションを紹介します:

```text
You: /dpd
DPD: このワークスペースに既存セッションなし。新規に始めますか？
     直前まで "認証方式を選ぶ" について話していたのを観測。
     提案するアンカー:
       Start: 認証方式を選ぶ
       End:   認証方式が決定 + 根拠が記録
     適用しますか？
You: yes
DPD: [ルート + Start + End アンカーを作成]
     ambient モードに入りました。普通に会話を続けてください。

[JWT、session cookie、OAuth について議論]

DPD: ここまでで記録したいのはこれです:
       - 仮説: JWT          → 却下として close (トークン失効が困難)
       - 仮説: session cookie
       - 仮説: OAuth 限定   → 却下として close (サードパーティ依存)
     適用しますか？
You: yes
DPD: [ノード追加、却下を close]

[さらに議論、決定に至る]

DPD: session cookie で決まったようです。記録します:
       - 決定: session cookie で行く
       - 根拠: same-origin 制約に対する最も単純な正解
       - derived_from: ↑ same-origin 制約 (証拠ノード)
     End 条件を満たしたので、このサブグラフを到達済みにマークしますか？
You: yes
DPD: [mark_reached] セッションは idle に。サブグラフを閉じました。
```

あなたは普通に会話するだけです。DPD は記録と整理 (提案・記録・絞り込み) を担当し、自然な区切りで確認を求めます。あなたの確認なしにグラフは変わりません。

### ここで何が起きていたか

上のやりとりを DPD の用語にマップするとこうなります:

1. **Entry phase (エントリーフェーズ)** — `/dpd` 発火直後、ゴール (Start) と達成条件 (End) をアンカーとして確定。これ以降の判断はこのアンカーからの drift (ずれ) を測れるようになる。
2. **Ambient mode (アンビエントモード)** — 普通の会話に観察を重ね、自然な区切りで「ここまでで記録したいのはこれ」と提案。仮説の追加・却下、決定の確定はすべてユーザの明示 OK が必須。
3. **mark_reached** — End 条件が満たされたらサブグラフを完結 (closure)。後から「あの判断の根拠は?」と問い直せる状態で凍結される。

つまり「**アンカーを立てる → 会話を観察 → 自然な区切りで整理を提案 → 確定したら凍結**」が 1 セッションの基本リズムです。

---

## できること

### コマンド

| コマンド | 役割 |
| --- | --- |
| `/dpd` | 意思決定の追跡開始、または既存セッションの再開 |
| `/dpd-status` | 「今どこ?」スナップショット — アクティブなルート、フォーカス、Pool アイテム、モード |
| `/dpd-dump` | グラフ全体をツリーまたは Mermaid で表示 |
| `/dpd-summary-md` | 決定済み / クローズ済みアイテムを markdown サマリで抽出 |
| `/dpd-edit <id>` | 直接制御したい時の手動編集 |
| `/dpd-import <file>` | 散文 / 仕様 / グラフ文書をアーカイブ済みサブグラフとして取り込み |
| `/dpd-fill` | 現グラフに対するギャップ候補の推論ノード生成 |

#### スコープを指定して起動 / 再開

```text
# 好きな名称をサブスコープとして明示できます。
# 未指定時はトップレベルのセッションとして動作 (`.dpdrc` が見つかればそれに従う、無ければスコープなし)。
# `.dpdrc` を置いた場合は、そのディレクトリ配下から起動した時に自動でそのスコープに紐づきます。
/dpd --scope=<scope名>
```

### 詳細な使用例

実際に MCP ツールで graph を組んだ walkthrough (transcript + Mermaid 込み) を [`docs/examples.md`](docs/examples.md) (英語のみ) に集約しています。「こんな時に使います」の各ケースに対応:

1. **マネタイズモデルを落とし込む** — 複数仮説 → evidence → 採用 + 却下案も根拠付きで保存
2. **もやっとしたサービスを最小スペックに narrowing** — End 絞り込みで「アプリ作りたい」を具体的な初版 spec (target / interaction / non-goals) まで落とす
3. **仕様の妥当性 / 整合性チェック** — `/dpd-import → /dpd-fill → /fcot` パイプライン。DPD 自身の spec もこれでリリース前検証した
4. **セッションを跨ぐ multi-agent 開発** — 1 つのエージェントの context に収まらない実装を、session を handoff surface として複数 agent でリレーする

### 向いている場面

- 数日を跨ぐ作業や複数セッションのプロジェクトで「何を決めたか」が後から重要になる
- 複数の有望なブランチがあり、後から振り返りたい対話
- アーキテクチャ / スコープ / ポリシー判断でドキュメントの出力が欲しい
- 仕様 / 設計文書のレビュー (上記の self-validation パイプライン)

### 向いてない場面

- 短い単スレッドの対話
- 機械的で明確なタスク
- 再開する必要がない使い捨ての探索

---

## オプション: スコープマーカー (`.dpdrc`)

複数の隣接プロジェクトディレクトリで 1 つの DPD DB を共有したい場合 (例: モノレポ) は、ワークスペースのルートに `.dpdrc` を置きます:

```ini
# .dpdrc — DPD scope marker
scope=my-workspace
```

サーバはエディタの作業ディレクトリから親をたどってこのマーカーを探し、見つかった場所をエージェントスコープの識別子として使います。詳細とサブスコープの挙動は [AGENTS.md](AGENTS.md#sub-scope-detection-dpdrc) を参照。

---

## DPD の設計思想について

なぜ意思決定を *グラフ* として保持するのか、End drift gate / Pool / 仮説の却下 などの設計判断がどんな失敗モードを防ぐためにあるのか、そして DPD 自身が DPD の self-validation を通して開発された経緯 — これらの背景を [docs/concept.ja.md](docs/concept.ja.md) にまとめています。

- **[docs/concept.ja.md](docs/concept.ja.md)** — DPD とは何か、なぜ存在するか、グラフの動き、ライフサイクル状態、agent-driven な dogfood の物語

## その他

- **[core/server/README.ja.md](core/server/README.ja.md)** — MCP サーバのアーキテクチャ + 30 ツールのリファレンス
- **[core/skills/README.ja.md](core/skills/README.ja.md)** — スキル群の概要 (メインの `/dpd` + サブスキル)
- **[AGENTS.md](AGENTS.md)** — コントリビューター向けガイドライン (TDD、レビューフロー、規約)

---

## License

Apache 2.0 — [LICENSE](LICENSE) 参照。Copyright © 2026 [1o1 Co. Ltd.](https://1o1.co.jp/)

コントリビューション歓迎。PR を開く前に [AGENTS.md](AGENTS.md) をご一読ください。
