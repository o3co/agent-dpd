# AGENTS.md — DPD repository

[English](AGENTS.md)

このリポジトリで作業する AI コーディング・アシスタント (Claude / Cursor / Copilot …) および人間 contributor 向けの開発ガイドライン。変更前に読むこと。

## プロジェクト概要

**DPD (Decompose-Propagate Decision)** は、AI 対話を意思決定グラフとして構造化するためのグラフベース・プロトコル。本リポジトリは reference 実装を含む:

- `mcp/` — Model Context Protocol サーバ (Python、stdio、sqlite)。グラフ状態と tool API を所有。
- `skill/` — Claude Code skill。MCP サーバと対話する UX 層。
- `docs/` — spec、migration guide、ADR。

MCP サーバは対話状態を持たず、skill はグラフデータを持たない。両者は一緒に進化する。

## Setup

Python 3.11+ が必要。

```bash
# リポジトリのルートから
python3.11 -m venv mcp/.venv
mcp/.venv/bin/pip install -e 'mcp[dev]'

# Claude Code に登録 (one-time)
claude mcp add dpd-mcp-server -- "$(pwd)/mcp/.venv/bin/dpd-mcp-server"
```

登録後は Claude Code を再起動して `mcp__dpd-mcp-server__*` tool を discover させる。

ランタイムデータは `~/.claude/dpd-server/data/<encoded-agent-scope>/graph.sqlite` に置かれる。`DPD_DATA_DIR` 環境変数で override 可能 (テストは実データを汚さないために使っている)。

## テスト

```bash
mcp/.venv/bin/python -m pytest mcp/tests/ -q
```

commit 前に全テストが pass すること。実サーバを起動する stdio end-to-end smoke も含まれる — 失敗したら skip せず原因を debug する。

## 開発ワークフロー

### TDD 規律

機能追加・bug fix はすべて RED → GREEN → REFACTOR:

1. **RED** — まず失敗するテストを書く。正しい理由で失敗することを確認。
2. **GREEN** — pass させる最小コードを書く。
3. **REFACTOR** — green を保ったまま整理。

Plan (`.claude/superpowers/plans/` 配下にある場合) は各タスクをこの 3 ステップで明示する。

### Code review

PR ごとに `/multi-agent-review` を **1 回だけ** 実行してから完了宣言する。修正後の再レビューは user 判断 — 自動化しない。

レビュー指摘を dismiss する際は contract-based か verified-empirical のテンプレ (ルール + 「覆す条件」) を使う。曖昧な dismiss (「起きなさそう」) は default で must-fix。

2 人以上の独立レビュアーから同じ issue が出たら、以前 dismiss していても default must-fix に格上げ。

## コードスタイル

- **Python**: 型付き、modern syntax (`X | None` を `Optional[X]` より優先、`list[int]` を `List[int]` より優先)。各モジュール冒頭に `from __future__ import annotations`。
- **コメント**: default は書かない。*why* が non-obvious な場合のみ書く (隠れた制約、特定 issue への workaround、読者が驚く挙動)。*what* は決して narrate しない。
- **Docstring**: モジュールと public 関数は短い 1 行のみ。multi-paragraph essay は書かない。
- **エラー**: 境界 (ユーザ入力、MCP tool 引数) で validate する。内部呼び出し側は trust する — belt-and-suspenders なチェックは入れない。
- **Backward-compat hack 禁止 (v1.0 まで)**: 変更が必要なら直接変える。旧コードパスを残さない。

## Spec & docs

**ユーザ向け readable spec** (概念 + lifecycle + Mermaid) は [`README.md`](README.md) (長くなれば `docs/spec.md` に分割)。

**実装レベルの完全 spec** (SQL DDL、エラーコード、state machine 表、migration semantics) は現状 upstream の workspace `scopes/decompose-propagate.protocol/docs/dpd-v<N>-draft.md` に存在する。non-trivial な実装作業で必要ならメンテナに依頼を — 本リポへの graduation は計画中だが未完。

ADR と migration guide は `docs/`。

## Sub-scope 検出 (`.dpdrc`)

DPD は 2 階層の scope を区別する:

- **Agent scope** — ユーザが操作する workspace ルート。MCP の `roots/list` から walk-up して `.dpdrc` marker を探すことで server 側が決定する。Agent scope ごとに別の sqlite DB を持つ。
- **Sub-scope** — agent scope 内のさらに細かい区分 (sub-project、ワークストリーム等)。skill 側で `--scope=<name>` 引数を見るか、cwd から walk-up して `scope=<name>` 付きの `.dpdrc` を探す。

`.dpdrc` は 1 行 marker (`scope=<name>` or agent-scope-only 用の空ファイル)。scope に関する新規 convention ファイルは導入しない — `.dpdrc` を再利用する。

## Commits & PRs

- Conventional commit prefix (`feat:` / `fix:` / `refactor:` / `docs:` / `test:` / `chore:`)。
- Breaking change は `!` を付ける (例: `refactor!: rename server/ → mcp/`)。
- commit message 本文は *what* ではなく *why* を説明する。
- PR は merge 前に conversation resolution が必要 (branch protection で強制)。
- merge 後は branch を自動削除。
- `main` への force-push 厳禁。

## License & copyright

- **License**: Apache 2.0。`LICENSE` ファイルは <https://www.apache.org/licenses/LICENSE-2.0.txt> から取得した verbatim な公式テキスト。**LICENSE ファイルを AI 生成または paraphrase しない** — 1 文字でも違うと GitHub や pkg.go.dev の SPDX 検出が壊れる。
- **Copyright**: `1o1 Co. Ltd.` (株式会社 1o1、<https://1o1.co.jp/>)。`o3co` ではない、`o3co Inc.` でもない。Copyright 表記は `LICENSE` の verbatim 本文の外、ファイル冒頭に独立行で記載する。

## Versioning

現状 `0.x` — minor version を自由に bump、`1.0` までは breaking change 許容。新機能は review 後に直接 `main` へ。長命の feature ブランチは作らない。

`1.0` で public API 表面 (MCP tool 名 + 引数、`.dpdrc` schema、sqlite schema migration path) をロックする。それ以前は安定性を約束しない。
