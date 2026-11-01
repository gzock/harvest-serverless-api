# 復元後の互換性・不具合レビュー（2026-11-01 UTC）

旧版 `66d7d18` と復元版 `933ba67` を比較し、6領域を並列調査した後、写真確定・CSV期限後再実行・フロント配布を別担当が再レビューした。元リポジトリ上で修正し、領域ごとのローカルコミットに保存した。過去の復元検証とは別の、新しい検証結果である。

## 主な結果

- **旧仕様の復元**: UIの場所上限6階層（CSVは従来の5階層）、`GET /corporation/billing` の法人情報全体、`GET /corporation/users/{id}` の一覧配列を復元。OpenAPIとフロント生成型も同期。
- **新しい処理の競合修正**: 写真登録とsession完了を同一トランザクションへまとめ、失効したworkerによる削除済み写真の復活を防止。ロール更新にもprojectロック条件を適用。CSV journalはTTL削除順序に依存せず再実行できるよう修正。
- **帳票・既存データ**: 印刷範囲の右側固定欄と開始位置を維持し、無効なオプションのplaceholderを除去。長い日本語ファイル名のS3タグ／ダウンロード制限を修正。削除済みCognitoユーザーや任意属性欠落で一覧・通知が停止しないよう修正。
- **入力・運用**: XLSXの巨大座標をopenpyxl処理前に拒否し、正常な固定ラベルの重複を受理。従業員作成途中の失敗補償を修正。配布artifactのmanifest照合、環境名の明示、既存tableのStreams検証を追加。
- **フロント**: パスワード表示、認証属性待機中の設定画面、project切替／画面破棄後の遅延処理を修正。可変assetのrollbackで同サイズ・古いmtimeのファイルが転送されない問題も修正。

旧版由来の不具合と今回のモダン化によるデグレは、各詳細報告で分けている。代表的な回帰テストは修正前のコードで失敗することを確認した。単に既存テストの成功を互換性の根拠にはしていない。

## 統合検証

- 元repoの最新srcを `PYTHONPATH=src` で指定し、pytest **622件成功／skip 0**（61.29秒）。**DynamoDB Local 38件**を含む。
- mypy **65 source files**、Ruff、format **103 files**、OpenAPI freshness検査成功。
- フロント全**75テスト**、strict app/spec型検査、ESLint、本番ビルド成功。ブラウザ／配布の最終結果はフロント側の最新レビュー記録を参照。
- 最終SAMビルド成功、artifactの全65 source一致・18 handlerのartifact単独import・manifest照合成功。[成果物記録](validation/review-build-2026-11-01.json)を保存。
- 本番AWSへのデプロイ・API実行はしていない。モック、合成データ、loopback DynamoDB Localを使用した。

コマンド: `PYTHONPATH=src DYNAMODB_LOCAL_ENDPOINT=http://127.0.0.1:18000 /workspace/recovered/integration/harvest-serverless-api/.venv/bin/python -m pytest -q`。同venvのmypy、Ruff、`scripts/export_contract.py --check`を使用。復元用コピーのeditable登録を参照しないよう、元repoのsrcを明示した。

## 残る制約・意図的な変更

1. APIのlogical ID／stage変更はURL維持を保証しない。[切替手順](review-cutover-2026-11-01.md)に従う実環境検証が必要。実Cognito、IAM、S3 CORS、旧PWA更新、実帳票の印刷結果は未検証。
2. XLSXに新しい処理上限を設けた。大きい既存テンプレートは[具体的な上限](review-2026-11-01-security.md)との照合が必要。
3. テンプレートの同名／5件制限は同時upload時に原子的ではない。Cognito作成中断の完全自動回復、S3孤立objectの自動削除も行っていない。
4. 旧版からの課金初期化前の制約（UTC26日00:00から定時処理00:10まで）と、既存invoiceのbaseline移行要件を維持。[データレビュー](review-2026-11-01-data.md)と[課金移行](billing-migration.md)を参照。
5. フロント開発用API型生成ツールにmoderate 3件が残る。本番依存のnpm auditは0件。強制更新が型生成を壊すことを確認しており、互換修正待ちとしてフロントREADMEへ記録した。

詳細: [データ](review-2026-11-01-data.md)、[帳票・ジョブ](review-2026-11-01-generation.md)、[認証・メディア](review-2026-11-01-security.md)。復元時のOpenAPIハッシュ一致記録は復元時点の記録であり、今回の互換性修正後は契約と生成型を意図的に更新している。
