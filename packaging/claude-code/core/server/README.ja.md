# dpd / mcp — MCP server

[English](README.md)

DPD の Model Context Protocol サーバ。グラフ状態をすべて所有し、ユーザに直接対話することはない。

このサーバは DPD reference 実装の半分 — persistence と tools を担う側。もう半分は [`../skill/`](../skill/) で、対話 UX を駆動しここで定義された tool を呼ぶ。プロトコルの概念や全体的な使い方は [`../README.ja.md`](../README.ja.md) 参照。

## 役割

MCP サーバが DPD を単なる prompt ではなく **プロトコル** たらしめている要素:

- **状態は server 側、skill 側ではない**。session / root / node / edge / Pool item はすべて SQLite (WAL モード) に永続化される。Claude Code 再起動、context リセット、並行 session を跨いでも消えない。
- **agent scope ごとに 1 DB**。Server は MCP `roots/list` で client が advertise したパスから walk-up して `.dpdrc` marker を探し、その位置を agent scope の識別子に使う。無関係な workspace が偶然 state を共有することがない。
- **stdio transport**。HTTP も port も使わない。MCP サーバは Claude Code の子プロセスとして stdin/stdout で spawn され、Claude Code が死ねば一緒に死ぬ。MCP のセキュリティモデルに準拠。
- **prompt ではなく tool**。グラフ操作はすべて型付き引数を持つ MCP tool として exposed。skill が prompt を組み立て、server は入力検証 + SQL 実行 + 構造化結果返却を行う。境界が明確なので prompt 層は差し替え可能。

## アーキテクチャ

```text
┌─────────────────────────────┐
│ Claude Code (host)          │
│  ├─ skill (prompts, UX)     │
│  └─ MCP client              │
└─────────────┬───────────────┘
              │ stdio (JSON-RPC)
┌─────────────▼───────────────┐
│ dpd-mcp-server (このディレクトリ) │
│  ├─ tool dispatch           │
│  ├─ scope resolution        │
│  ├─ sqlite storage (WAL)    │
│  └─ schema migration        │
└─────────────────────────────┘

Storage path: ~/.claude/dpd-server/data/<encoded-agent-scope>/graph.sqlite
              (DPD_DATA_DIR で override 可)
```

旧スキーマの DB を open すると schema migration が自動実行される: 現状のスキーマバージョンは `v4`、必要に応じて `migrate_v2_to_v3.py` と `migrate_v3_to_v4.py` を順次適用する。通常利用で手動操作は不要。

## インストール

リポジトリのルートから:

```bash
./install.sh        # venv 作成 + editable install + Claude Code への MCP 登録
```

手動でやる場合:

```bash
python3.11 -m venv mcp/.venv
mcp/.venv/bin/pip install -e 'mcp[dev]'
claude mcp add dpd-mcp-server -- "$(pwd)/mcp/.venv/bin/dpd-mcp-server"
```

リポジトリのクローンも含めた one-liner はトップレベルの [README.ja.md](../README.ja.md#インストール) を参照。

`mcp__dpd-mcp-server__*` tool を discover させるため Claude Code を再起動。

## Tools

30 個の MCP tool、関心ごとに分類:

### Session lifecycle

| Tool | 役割 |
| --- | --- |
| `start_session` | 新しい session を作成 (entry モード)。 |
| `list_sessions` | agent scope の session 一覧、sub-scope や mode で絞り込み可能。 |
| `get_session_state` | スナップショット: session メタ + active roots + focus node。 |
| `set_session_mode` | `entry → ambient → idle` を遷移。 |

### Root 管理

| Tool | 役割 |
| --- | --- |
| `spawn_root` | session 内に新規 root subgraph を作成。 |
| `list_active_roots` | `lifecycle=active` の root 一覧。 |
| `set_root_lifecycle` | `active → archived → closed` を遷移 (単調)。 |
| `set_focus` | session の focus node を設定 (resume 用文脈)。 |

### Node CRUD

| Tool | 役割 |
| --- | --- |
| `add_node` | ノード追加 (type ∈ start / end / question / hypothesis / decision / rationale / evidence / …)。`provenance` (grounded / inferred / imported / manual) をサポート。 |
| `get_node` | id でノード取得。 |
| `close_node` | `closure_reason` 付きで close。 |
| `list_open_nodes` | root 配下の open ノード一覧。 |
| `list_unblocked_open_nodes` | 同上、ただし `blocks` 入り edge があるものを除外。 |
| `walk_subtree` | 親から subtree を辿る。`parent_kind` でフィルタして安全性確保。 |

### Edge 管理

| Tool | 役割 |
| --- | --- |
| `add_edge` | edge 追加 (`derived_from` / `contributes_to` / `blocks` / …)。 |
| `list_edges` | endpoint と type で edge 一覧。 |

### Decision flow

| Tool | 役割 |
| --- | --- |
| `resolve_branch` | 汎用 branch 解決: N 個の sibling node を atomic に close + decision を作る + 任意の rationale + `derived_from` edge。 |
| `resolve_hypothesis_branch` | 特殊化: 1 つの hypothesis を accept、sibling を rejected として close、decision + rationale を attach。 |
| `mark_reached` | `achievement_conditions` を評価して End node を到達済みにマーク。 |

### Pool

| Tool | 役割 |
| --- | --- |
| `pool_add` | 観察を Pool に park。重複検知用に `text_hash` を自動計算。 |
| `pool_list` | Pool item 一覧、rejected を含めるか選択可。 |
| `pool_elevate` | Pool item を explicit edge と共にグラフノードに昇格。 |
| `pool_drop` | reject を記録せず削除。 |
| `pool_reject` | 理由付きで reject — 同じ canonical text の再提案を抑制。 |

### Bulk & export

| Tool | 役割 |
| --- | --- |
| `bulk_import_subgraph` | subgraph (nodes + edges) を 1 transaction で挿入、parent_kind の整合検証付き。 |
| `export_yaml` | subgraph を JSON 互換 YAML として render。 |
| `dump_persist` | session 状態を安定 on-disk 形式に dump。 |

### Advanced lifecycle

| Tool | 役割 |
| --- | --- |
| `delete` | ソフト削除 (node が `state=closed` で documented grace 期間を経過した場合のみ可)。 |
| `force_delete` | ハード削除、grace 期間を bypass (`audit.kind=force_delete` として記録)。 |

正確な引数形状は [`src/dpd_mcp_server/server.py`](src/dpd_mcp_server/server.py) を参照 — 各 `types.Tool(...)` 定義が input JSON Schema を持つ。

## Storage レイアウト

```text
~/.claude/dpd-server/data/
└── <encoded-agent-scope>/        # 例: "-Volumes-Workspace-scopes-mcp"
    └── graph.sqlite               # agent scope ごとに 1 DB (WAL モード)
```

agent-scope エンコーディングは scope ルートの絶対パスの `/` を `-` に置換する。衝突なしで決定的なディレクトリ名を保証する。

`DPD_DATA_DIR` で root を override 可 (テストが実データを汚さないために使う)。

## テスト

```bash
make test    # 同等: mcp/.venv/bin/python -m pytest mcp/tests/ -q
```

v0.3.1 時点で 255 tests。実サーバを spawn して tool chain 全体を歩く stdio end-to-end smoke を含む。Schema migration テストは制約違反を inject して rollback の atomic 性を確認する。

## Migrations

migration は `Storage.open()` の中で自動実行される。DB の `PRAGMA user_version` が現在のスキーマバージョン未満なら、適切な `migrate_v<N>_to_v<N+1>.py` を transactional に適用してから connection を返す。手動操作は不要。

DB を offline で migrate する場合 (例: archive 用に bundle する前):

```bash
mcp/.venv/bin/python -m dpd_mcp_server.migrate_v3_to_v4 path/to/graph.sqlite
```
