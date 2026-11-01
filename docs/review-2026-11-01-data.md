# データ処理の再監査 — 2026-11-01

## 現在の実施範囲と制約

今回の実行環境で `/workspace/harvest-serverless-api` は旧版HEAD `66d7d18`、作業treeは監査開始時点でcleanだった。比較対象となるmodern版の `src/harvest`、新しい回帰テスト、`uv.lock`、`.venv` は存在しない。branchは `work` のみ、stashおよびunreachable objectからも新しい実装を確認できなかった。

したがって、この記録は旧版の比較基準抽出までであり、modern版との差分監査を完了したという意味ではない。過去会話にある実装・テスト結果を、現在のfilesystemで再検証できた結果として扱わない。ソース変更やmodern版の推測による再構築は行っていない。比較対象を復元した後に、以下の基準で実コードを比較して再現テストを実行する。

## 旧版から確認した仕様

| 項目 | HEAD 66d7d18の挙動・根拠 | modern版比較の観点 |
|---|---|---|
| 課金単価・税 | `routes/site-packages/harvest/commons/billing/billing.py`: 500円／account、税率1.10、円未満切捨て | Decimal化しても金額を維持する。既存invoice金額を自動補正しない |
| 課金周期 | 同ファイル: 25日まで当月末請求、26日以降翌月末。期間は前月26日〜当月25日。naive `datetime.now()` | Lambda既定UTCでの旧挙動を確認する。JSTへの変更を依存更新に混ぜない |
| 同月減員 | 同ファイル `create`: users_countが既存以下なら更新しない。登録イベントは人数を1増やす | baseline／イベントledgerが二重課金と減額を防ぐか。登録と月次reconcileの競合を検証 |
| 課金対象 | `controllers/corporation_controller.py:list_users`: 法人group所属かつEnabledの全ユーザー。employee限定filterなし | employeeのみへ絞ると既存請求対象が変わる。任意Cognito表示属性の欠落対策と対象変更を分ける |
| 法人ゼロ人 | 同メソッドは空配列をValueError、Billing.createも空snapshotを拒否 | ゼロ件対応は旧不具合修正として明示し、既存invoice減額とは区別 |
| CSV行数 | `drivers/csv_driver.py`: 最大300件 | 上限の維持と、byte／文字数の追加上限による互換差を確認 |
| CSV形式 | `import_controller.py`: base64入力で日本語header・種類を単純文字列置換。`csv_driver.py`はUTF-8 BOM読取 | 正規の日本語／英語headerとquoted field内の文字列を扱う。ヘッダーとデータの置換混同を防ぐ |
| CSV内容 | `utils/csv_optimizer.py`: place名の全体重複禁止、親を名前で参照。placesとtargets両方がないと失敗 | target-only／place-only受理は意図した拡張か明示。missing parent・cycle・重複の検証 |
| CSV階層 | 同ファイル: `MAX_HIERARCHY_NUM=5`。root hierarchyはproject ID、nested hierarchyは祖先place ID列でproject IDを含まない | 既存保存行の二種類のhierarchyを読み取れるか。深さ計算と祖先写真集計を確認 |
| UI階層 | `controllers/place_controller.py:create`: 親hierarchyの分割長が6なら拒否。UI生成行はproject IDを含み、最大6階層 | CSVに合わせ5階層へ統一する場合、旧6階層データの操作に影響する。単なる互換維持とは表現しない |
| 写真件数 | `controllers/target_controller.py`: target作成／削除で祖先Placeのrequiredを±1。写真有り対象の削除ではbefore/after resultsも減算 | 親子競合対策で集計や既存hierarchyを破損しないか。既存データを用いたtransactionテストが必要 |
| 日付ソート | `utils/sorters.py`: `%Y-%m-%dT%H:%M:%S.%f`だけ受理 | naive UTCとoffset/Z付き形式の混在時に安定し、昇順／降順の契約を維持するか |

## 次の実行条件

modern版の実ファイルが利用可能になったら、旧HEADとの比較、再現した不具合の局所修正、回帰テスト、利用可能ならDynamoDB Localでの実式検証を行う。今回の環境では新旧比較も新実装のテストも未実施である。
