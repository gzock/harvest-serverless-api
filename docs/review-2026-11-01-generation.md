# 帳票・非同期ジョブの独立レビュー（2026-11-01）

比較元は `66d7d18`、対象は復元後の `src/harvest/jobs.py`、`generate.py`、`controllers/generate_controller.py`、`utils/excel_doc_generator.py` と関連テスト。旧実装から継承された不具合も対象にした。

## 再現して修正した問題

1. **Excelの印刷範囲から静的な欄が欠落する。** 元テンプレートが `B2:H6` でも、写真・名前など差し込みセルの最終列だけから印刷範囲を再計算していた。署名欄など右側の静的な列を切り落とし、開始位置も `A1` に変えていた。元の矩形を保持し、複製した行数だけ終端を拡張する。
2. **未選択のオプションが `{date}` / `{hierarchy}` のまま帳票へ出る。** オプションを無効にした場合は該当プレースホルダを空にする。画像・他の値・静的ラベルは維持する。
3. **長い日本語の帳票名でS3タグ付けが失敗する。** `name` をbase64化すると、APIで受理される文字数でもS3タグ値の256文字上限を超える。表示名は既存のオブジェクトキー末尾から得るよう変更し、従来のbase64タグは上限以内の場合だけ維持する。これにより長い帳票名も履歴表示・ダウンロードできる。
4. **生成可能な最大長タイトルを履歴からダウンロードできない。** タイトル240文字に `.xlsx` を追加した245文字のファイル名に対して、再度240文字制限を適用していた。完成済みファイル名の検証では拡張子分を含める。
5. **出力S3キーのUTF-8バイト長を検証しない。** 長いUnicodeタイトルとIDを組み合わせたキーが1024バイトを超える場合、S3アップロード前に明確な入力エラーとする。

`tests/test_generation_review.py` の4テストは変更前すべて失敗し、修正後は既存のExcel/生成セキュリティテストと合わせて **31件成功**。変更した2つのソースはmypyに成功。新しいサービスへの接続やAWS上の操作は行っていない。

## ジョブ・再試行の確認

条件付きclaimの試行上限5回、360秒leaseとworker timeout300秒の関係、古いattemptによる完了/失敗書込の拒否、部分バッチ失敗、DLQによる有効leaseの保護、reconcilerのstrong readと条件付きfairness更新を再確認した。今回の担当レビューでは追加の確定不具合は見つかっていない。実AWSのイベント配信・IAMの実行確認はこの結果に含まれない。

テンプレートのXML座標・印刷範囲による過大展開と、静的文字列を変数扱いして拒否する検証については、テンプレート担当へ具体的な所見を引き継いだ。

## 全体検証手順

元リポジトリをcwdとして以下を実行する。共有venvのeditable登録先は復元用コピーなので、`PYTHONPATH=src` でレビュー中のソースを明示する。

```sh
PYTHONPATH=src DYNAMODB_LOCAL_ENDPOINT=http://127.0.0.1:18000 \
  /workspace/recovered/integration/harvest-serverless-api/.venv/bin/python -m pytest -q
PYTHONPATH=src /workspace/recovered/integration/harvest-serverless-api/.venv/bin/mypy
/workspace/recovered/integration/harvest-serverless-api/.venv/bin/ruff check .
/workspace/recovered/integration/harvest-serverless-api/.venv/bin/ruff format --check src tests scripts
```

DynamoDB Localは既存の `harvest-recovery-dynamodb` コンテナを使用する。全体件数と他担当の修正後のビルド結果は親担当の最終検証で記録する。
