# データ処理の再監査 — 2026-11-01

比較基準は旧HEAD `66d7d18`。復元されたmodern版（`7d5506b`、レビュー開始時のHEADは`933ba67`）を実コードとオフラインテストで比較した。前段の環境確認時にはmodern版が未復元だったが、この記録は復元後の再監査結果である。実AWSへの接続・書き込みは行っていない。

## 修正した問題

| 重要度 | 問題と影響 | 修正・根拠 |
|---|---|---|
| P2 | UIのPlace追加が最大6階層から5階層へ縮小していた。旧UIで可能だった6階層目を新APIが拒否する。さらに旧CSV保存行はhierarchyにproject prefixを含まないため、同じ深さでも上限の判定が変わっていた | `controllers/place_controller.py:67`: UIの上限を6として明示し、実際の祖先Placeだけを数える。既存行は書き換えず、新しく作る子のhierarchyをproject prefix付きへ統一。CSVは元の5階層を維持する |
| P2 | completed CSV journalのTTLは物理削除が非同期。metadataだけ消えてchunkが残った場合、同じCSVの再取込が条件付きPutで409になる。metadataが残った場合は期限後も古い成功結果を返し、新しい取込が実行されない | `controllers/import_controller.py:116`: completedの論理期限を判定し、期限済metadata/chunkをproject lockと同一transactionで置換する。未期限切れ・active journalは置換しない。再実行の挙動をTTL削除順序に依存させない |
| P2（検証基盤） | DynamoDB Local fixtureのRoles indexが`UserProjectsIndex`で、本体の`UserRolesIndex`と不一致。Places/Targetsの親indexも実際のLSIと異なるGSIだった。従来のLocalテストはproject一覧を実行しておらず検出できなかった | `tests/integration/conftest.py:68`: 旧`create_tables.sh`および現`admin.py`と同じ、Roles=`UserRolesIndex`（user_id HASHのみ）、Places/Targets=`ParentPlacesIndex`（LSI）へ修正。`RoleController.list_projects`→`ProjectController.list_projects`を実DBで検証するケースを追加 |

## 再現と修正後の保証

旧PlaceControllerとCsvOptimizerのclassを`git show 66d7d18:path`からAST抽出し、DB部分をメモリの保存処理へ置き換えて実行した。旧UIは1〜6階層が成功し7階層が拒否、旧CSVは5階層が成功し6階層が拒否となることを確認した。新しい互換性テストは、project prefixがある既存UI行・prefixがない旧CSV行の両方について6階層目を許可し、7階層目を拒否する。CSVの5階層上限と祖先写真required集計も維持する。

journalの修正前には、TTL未削除・metadata先行削除・chunk先行削除の3ケースを実DynamoDB Localで失敗として再現し、修正後にすべて成功した。さらに同じdigestの期限後restartを競合させ、勝者がstage直後に停止するケースを追加した。敗者が生成したIDは保存されず、active planにTTLが付かず、lease失効後は勝者の保存済みIDだけで再開する。以前のleaseによるtransactionは、phase/cursorが再び同値でもproject側のlease条件により拒否される。metadataとchunkの再利用で別attemptのplanが混ざらないことを検証した。

## 維持した仕様と確認範囲

- CSVはUTF-8 BOM／CP932、300行上限、日本語／英語header、place名の重複・未解決親・cycle検証を維持する。新journal方式では同一project・同一CSVの再送は30日のcompleted保持期間内は冪等な再送として扱い、論理期限後は新規取込となる。旧版の無条件な再追加とは異なる、既に導入済みの耐障害設計である。
- Place/Targetの親子競合は、子作成transaction内の親`children_version`更新と、強整合読み取り後の親削除CASで引き続き防止する。既存の競合テストと実DBのtarget／写真集計テストも再実行した。
- project削除は先にdeleting状態を付け、Targets／Places／Rolesをbase tableから強整合で読み、ownerとprojectを最後に削除する。importingとの排他、失敗時にownerを残す再試行手順を既存回帰テストで確認した。実S3を含む削除の復旧はこのオフライン検証の範囲外である。
- 課金は500円／account、税率1.10、円未満切捨て、UTC25/26日の周期、同月減員時の非減額を維持する。baselineに含まれるmember、重複登録イベント、月次reconcileとの重複、並行増分のCAS、月跨ぎ、既存invoiceの条件付きbaseline移行を実DynamoDB Localの既存3ケースで再確認した。請求計算の実装は今回変更していない。

## 旧版由来の課金上の制約（今回変更なし）

月次初期化は`template.yaml`のUTC26日00:10のscheduleで、新しい請求contextはUTC26日00:00から選ばれる。このため新しいinvoiceがまだ存在しない場合、初期化前の約10分間は新規member課金が失敗し得る。旧版にも同じscheduleと初期化依存がある。既存invoiceのbaseline移行と初回snapshotの業務判断が必要なため、この互換性修正で自動初期化や金額推測を追加していない。

## 検証結果

実行Pythonは`/workspace/recovered/integration/harvest-serverless-api/.venv/bin/python`。pytestのrootdir／sourceは元repo `/workspace/harvest-serverless-api`。

- `pytest -q tests/test_data_review_compatibility.py tests/test_import_journal.py tests/test_data_correctness.py tests/test_place_concurrency.py tests/test_billing_correctness.py tests/test_integration_review.py`: **77 passed**。
- `DYNAMODB_LOCAL_ENDPOINT=http://127.0.0.1:18000 pytest -q tests/integration/test_data_review_local.py`: **5 passed**（TTL3順序、restart競合、実project一覧）。
- 同じLocal endpointで`tests/integration/test_place_dynamodb_local.py tests/integration/test_billing_dynamodb_local.py`: **10 passed**。
- 変更したsource/test 7ファイルのRuff: **pass**。変更したcontroller 2ファイルのmypy: **pass**。

別途Local全体を実行した際のupload lease保持変更に関する既存テスト不一致は、その担当へ引き継いだ。上記の担当範囲の成功と全体suiteの成功を混同しない。最終の全体検証とcommitは統合担当が実施する。
