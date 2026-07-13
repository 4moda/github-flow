# リポジトリへの issue-driven-flow の導入

[English](adopting.md) | [日本語](adopting.ja.md)

初回のみ必要な3つのステップ: Actions に PR 作成を許可し、認証情報を追加し、
ラベルを作成する —— その後、ラッパーワークフローを1つ配置します。

`4moda/issue-driven-flow` はパブリックなので、その再利用可能ワークフローとアク
ションはそのままどのリポジトリからでも利用できます。（プライベートフォーク
を運用する場合は、先にアクセスを許可してください: フォーク →
**Settings → Actions → General → Access** →
*"Accessible from repositories owned by ..."*。）

## 1. GitHub Actions にプルリクエストの作成を許可する

（後述の）`GF_BOT_TOKEN` を指定しない限り、Crafter はデフォルトの
`GITHUB_TOKEN` で PR を作成しますが、GitHub はデフォルトでこれをブロックし
ます —— build の実行は PR 作成の時点で
*"GitHub Actions is not permitted to create or approve pull requests"* という
エラーで失敗します。利用側リポジトリで以下を有効化してください:

- **Settings → Actions → General → Workflow permissions** →
  *"Allow GitHub Actions to create and approve pull requests"* にチェックを
  入れる。または、

```bash
gh api -X PUT repos/<owner>/<repo>/actions/permissions/workflow \
  -f default_workflow_permissions=read \
  -F can_approve_pull_request_reviews=true
```

（組織の場合、同じトグルが組織レベルにも存在し、リポジトリ設定の上限になり
ます。）これを省略しても安全に回復できます: Issue はプッシュ済みのブランチ
を保持したまま `flow/blocked-build` になり、設定を切り替えた後に `flow` を
追加すると PR のオープンから再開します。

## 2. エージェントの認証情報を追加する

利用側リポジトリ（または所有する組織）に、実行したいコーディングエージェン
トのシークレットを追加します —— ワークフローは提供されたシークレットから
エージェントを自動検出します:

| シークレット | 実行されるエージェント |
|--------------|------------------------|
| `ANTHROPIC_API_KEY`（Anthropic API キー）または `CLAUDE_CODE_OAUTH_TOKEN`（`claude setup-token` で取得、Pro/Max サブスクリプション向け） | **Claude Code**（[anthropics/claude-code-action](https://github.com/anthropics/claude-code-action)） |
| `OPENAI_API_KEY` | **Codex**（[openai/codex-action](https://github.com/openai/codex-action)） |
| `GEMINI_API_KEY` | **Gemini CLI**（[google-github-actions/run-gemini-cli](https://github.com/google-github-actions/run-gemini-cli)） |

シークレットは1つで十分です。複数のエージェントのシークレットが存在する
場合、自動検出は claude、codex、gemini の順で優先します。明示的に選ぶには
`shape` と `build` に `with: { agent: codex }`（または `gemini`、`claude`）
を渡してください。

任意: `GF_BOT_TOKEN` —— プッシュと PR 作成に使用する PAT（contents: write,
pull-requests: write）。これを指定しない場合はデフォルトの `GITHUB_TOKEN`
が使用され、動作はしますが **Crafter が開く PR でリポジトリ自身の CI がト
リガーされません**（GitHub は `GITHUB_TOKEN` で作成されたイベントに対する
ワークフロー実行を抑制します）。Crafter の PR で CI の結果が欲しい場合は
これを追加してください。

## 3. ラベルを作成する

```bash
bash scripts/setup-labels.sh <owner>/<repo>
```

（このリポジトリのチェックアウトから、`gh` を認証済みの状態で実行します。）
これにより、公開のトリガーラベル `flow` と `flow/*` ステートラベルが作成さ
れます。ステートラベルは自己修復もします —— ワークフローが必要に応じて作成
します —— が、人間が追加できるように `flow` は存在している必要があります。

**まず衝突がないか確認してください**: 対象リポジトリで `gh label list` を
実行します。このスクリプトは既存のラベルをそのまま更新するため
（`--force`）、既存の `flow`（または任意の `flow/*`）という名前のラベルは
流用されてしまいます —— そのラベルを追加するたびに実行がトリガーされます。
名前が既に使われている場合は、別のトリガーラベルを選んでください:

```bash
bash scripts/setup-labels.sh <owner>/<repo> run-ai
```

そして同じ名前をワークフローに渡します（後述の `trigger_label` を参照）。
トリガーラベルは `flow/` で始めることはできません —— このプレフィックスは
自動化が所有するステートラベル用に予約されており、ワークフローはそのような
名前を拒否します。

## 4. ラッパーワークフローを追加する

ファイルは1つ: `.github/workflows/issue-driven-flow.yml`

```yaml
name: issue-driven-flow

on:
  issues:
    types: [labeled, closed]
  pull_request:
    types: [labeled, closed, reopened]
  pull_request_review:
    types: [submitted]

jobs:
  shape:
    if: github.event_name == 'issues' && github.event.label.name == 'flow'
    uses: 4moda/issue-driven-flow/.github/workflows/shape.yml@v2
    permissions:
      contents: read
      issues: write
    secrets:
      anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
      claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
      openai_api_key: ${{ secrets.OPENAI_API_KEY }}
      gemini_api_key: ${{ secrets.GEMINI_API_KEY }}

  build:
    # fires for `flow` on an issue AND for `flow` on a flow/issue-N pull request
    if: github.event.label.name == 'flow'
    uses: 4moda/issue-driven-flow/.github/workflows/build.yml@v2
    permissions:
      contents: write
      issues: write
      pull-requests: write
    secrets:
      anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
      claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
      openai_api_key: ${{ secrets.OPENAI_API_KEY }}
      gemini_api_key: ${{ secrets.GEMINI_API_KEY }}
      bot_token: ${{ secrets.GF_BOT_TOKEN }}

  sync-pr:
    if: >-
      github.event_name == 'pull_request_review' ||
      (github.event_name == 'pull_request' && github.event.action != 'labeled') ||
      (github.event_name == 'issues' && github.event.action == 'closed')
    uses: 4moda/issue-driven-flow/.github/workflows/sync-pr.yml@v2
    permissions:
      issues: write
      pull-requests: read
```

`shape` と `build` はどちらもすべての `flow` トリガーを受け取りますが、共有
されテスト済みのルーティングテーブル（`scripts/gf.py`）が必ずどちらか一方
だけが動作することを保証するため、この二重配線は安全です。

バージョニング: `@v2` は移動するメジャータグです —— 常に最新の互換リリース
を指し、ワークフロー内部のアクション参照も同じタグを使用するため、すべてが
同期した状態を保ちます。アップグレードを自分で制御したい場合は特定のリリー
ス（例: `@v2.0.0`）を固定し、未リリースの変更を追いたい場合は `@main` を
使用してください。（`@v1` は v1.3.0 で凍結された旧ラインで、トリガーラベル
は `ai` でした。）

トリガーラベルを変更する場合（例えば `flow` が既に使われている場合）は、
`shape`、`build`、`sync-pr` に `with: { trigger_label: run-ai }` を渡し、
ラッパー内の2箇所の `github.event.label.name == 'flow'` 条件を合わせて変更
してください。名前は `flow/` で始めることはできません。

モデルを上書きするには、`shape` と `build` に
`with: { model: claude-opus-4-8 }`（または別のモデル ID）を渡します。省略
した場合は選択されたエージェントのデフォルトが使用されます。ID は実行され
るエージェントのものである必要があります: Claude の ID は
[Anthropic models overview](https://docs.claude.com/en/docs/about-claude/models)
に一覧があり（例: `claude-opus-4-8`、`claude-sonnet-5`）、Codex と Gemini
の ID はそれぞれのベンダードキュメントを参照してください。

Web 調査: デフォルトでエージェントには Web 検索・フェッチのツールが与えら
れます。これにより Composer は外部の事実（API やライブラリの機能など）を
ブロックせずに確認でき、Crafter はドキュメントを参照できます。エージェント
の実行をモデル API 以外オフラインに保ちたい場合は、`shape` と `build` に
`with: { web_research: false }` を渡してください。エージェントごとの対応
は、Claude の `WebSearch`/`WebFetch`、Codex の `web_search`、Gemini の
`web_fetch`/`google_web_search` です。

暴走防止策: すべてのエージェント実行は二重に制限されています —— エージェン
トのターン数（`with: { max_turns: N }` で調整可能。デフォルトは Composer
が50、Crafter が150）と、実時間のジョブタイムアウト（shape が30分、build
が90分）です。ターン数上限は claude と gemini で適用されます。codex には
相当する設定がないため、タイムアウトのみが上限になります。どちらかを超え
ると実行は失敗し、Issue は実行ログへのリンクとともに該当する
`flow/blocked-*` ステートになります。再試行するには `flow` を追加してくだ
さい。`max_turns` を下げると1回の実行あたりの API 利用量を抑えられます。

## 初回の実行

1. 実現したいことを自分の言葉で説明する Issue を作成します。
2. `flow` ラベルを追加します。Composer が Issue を整形済みテンプレートに
   書き直し、Issue は `flow/awaiting-approval` に移行します。
3. 整形された Issue をレビューします。自由に編集できます。内容に合意した
   ら **ready for implementation** にチェックを入れ、再度 `flow` を追加し
   ます。
4. Crafter がブランチ `flow/issue-<n>` をプッシュし、Issue をクローズする
   PR を開きます。これをレビューしてください。
   - 満足したらマージします —— Issue はクローズされ `flow/done` になりま
     す。
   - あるいは変更を要求します: 変更内容を記述した PR レビュー（またはコメ
     ント）を残し、**PR 自体に `flow` を追加します** —— Crafter がフィード
     バックをコンテキストとして同じブランチと PR で再作業します。Issue に
     `flow` を追加しても同様です。

実行がブロックされた場合、Issue には `flow/blocked-*` ラベルと、不足してい
る内容を正確に列挙したコメントが付きます。Issue で回答し、`flow` を追加す
ると再開します。

Issue が1つの PR には大きすぎる場合、Composer はそれを分割します: サブ
Issue はあらかじめ整形された状態（`flow/awaiting-approval`）で作成され、元
の Issue はチェックリスト付きの `flow/split` 追跡用 Issue になり、各サブ
Issue を個別に承認・トリガーします。すべてのサブ Issue がクローズされる
と、`sync-pr.yml` が追跡用 Issue を自動的にクローズして `flow/done` にしま
す —— 分割が完了したことに気づくための人間の操作は不要です。

## 人間が管理するもの

`flow` ラベルと `ready for implementation` チェックボックスだけです。すべ
ての `flow/*` ラベルは自動化が所有します —— 手動で追加・削除しないでくださ
い（ラベルが手動で変更された場合、次の `flow` 実行で復旧方法が説明されま
す）。

## 注意事項と制限

- `GF_BOT_TOKEN` に `workflow` スコープがない限り、Crafter は利用側リポジ
  トリの `.github/workflows/` を変更できません。デフォルトトークンはその
  ようなプッシュを拒否します。
- 実行は Issue ごとに直列化されます（`concurrency` グループ）。そのため実
  行中に `flow` を追加すると、競合するのではなく確認応答がキューに入りま
  す。
- 上記のラッパーではジョブごとに最小限の権限が宣言されており、フローの中
  でそれ以上を必要とするものはありません。
- `flow/*` ラベルの名前空間と `flow/issue-*` ブランチの名前空間は
  issue-driven-flow 用に予約されています —— 利用側リポジトリでこれらのプレフィッ
  クスを持つ独自のラベルやブランチを作成しないでください。
- build の実行は PR 自身の CI を **待ちません**: マージは人間の判断なの
  で、代わりに PR のブランチ保護 / 必須ステータスチェックでゲートしてくだ
  さい。（Crafter は自身の実行の中で既にリポジトリのテストを実行し、結果
  を PR 本文で報告しています。）`GF_BOT_TOKEN` がない場合、Crafter の PR
  では CI が一切発火しないことに注意してください。
- 利用側リポジトリに `PULL_REQUEST_TEMPLATE.md`（`.github/`、ルート、また
  は `docs/` 内）がある場合、Crafter はそれを PR 本文として埋めます。
  GitHub 自体は Web UI から作成された PR にしか PR テンプレートを適用しな
  いため、issue-driven-flow は API 経由で作成する PR に対してその挙動を再現しま
  す。クローズキーワード（`Closes #N`）と帰属フッターはいずれの場合も自動
  的に追加されます。
- 各エージェントロールは、Issue 上にロールごと1つの自動管理コメント
  （「… handoff notes …」）として申し送りメモを残すことがあります。これは
  エージェント自身の記憶（確認済みの事実、判断、未解決点）を同じ Issue の
  次回実行に引き継ぐためのものです。人間が自由に編集・削除して構いません。
  最新の Issue 本文と新しい人間のコメントが常に優先されます。
- 通常の Issue/PR の会話コメントだけでは何もトリガーされません —— 実行が
  開始されるのは `flow` ラベルからのみです（機械的な同期のための PR のク
  ローズ/再オープン/レビュー送信イベントと Issue のクローズイベントを除
  く）。
- セキュリティモデル —— 誰のトークンが使われるか、GitHub の外に何が出る
  か、なぜエージェントがプッシュやマージをできないか:
  [README](../README.ja.md#security-model) を参照してください。
