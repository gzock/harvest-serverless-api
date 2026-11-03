# 旧検証コードの保管

旧 `workspace/*.py` を、実行されない `.py.txt` として保存しています。Lambdaパッケージ・型検査・通常のPython lint対象には含みません。内容は移行前の検討経緯であり、現在の運用手順ではありません。

| ファイル | 元の用途 | 現行の代替 |
| --- | --- | --- |
| `foo.py.txt` | CSV階層生成の途中実装。未定義変数あり | `harvest.utils.csv_optimizer` とテスト |
| `hoge.py.txt` | 廃止されたBatchImporterの手動検証 | `harvest.controllers.import_controller` とCSV統合テスト |
| `gen.py.txt` | 大量CSVの実験用生成。現行の件数・階層制約に未対応 | テスト内の合成CSV |
| `fetch_streams.py.txt` | localhost固定のDynamoDB Streams採取。ページング未対応、イベントARNを仮値へ置換 | `events/` の合成入力、通知テスト |

履歴を確認するための資料です。内容を実行形式へ戻す場合は、対象環境・データ範囲・ページング・出力先を設計し直してください。
